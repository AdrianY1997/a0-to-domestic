"""Raster engine (SPEC 9.7, SAL-01, SAL-07).

Each sheet embeds only its own crop of the canonical page TIFF: ``extract_area`` of the tile,
rounded outward to whole pixels. The crop is placed at the rectangle those whole pixels occupy
(so the geometry stays exact), clipped to the part of the tile that has content. It is
downsampled only when the effective resolution exceeds ``max_dpi`` (never upsampled).

Compression follows the content: JPEG for continuous-tone colour, Flate (PNG ``Up`` predictor)
for line art and grey, and packed 1-bit Flate for bilevel scans.
"""

import math
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pymupdf

from a0td.domain.geometry import Rect
from a0td.domain.page import ViewTransform
from a0td.domain.units import EPS, MM_PER_INCH, mm_to_pt
from a0td.imaging import vips
from a0td.render.geometry import ViewMap

if TYPE_CHECKING:  # pragma: no cover
    from numpy.typing import NDArray

LINE_ART_MAX_COLORS = 64
_SAMPLE_PIXELS = 200_000
_ROT = {0: None, 90: "d90", 180: "d180", 270: "d270"}  # libvips rotates clockwise


class RasterSource:
    """An opened canonical raster page (tiled TIFF) with its physical size."""

    def __init__(
        self, path: Path, width_mm: float, height_mm: float, *, sequential: bool = False
    ) -> None:
        im: Any = vips.Image.new_from_file(
            str(path), access="sequential" if sequential else "random"
        )
        if im.hasalpha():
            im = im.flatten(background=255)
        if im.format != "uchar":
            im = im.cast("uchar")
        if im.bands == 2:  # pragma: no cover - defensive, canonical pages are 1 or 3 bands
            im = im.extract_band(0)
        self.im = im
        self.width_px: int = im.width
        self.height_px: int = im.height
        self.bands: int = im.bands
        self.px_per_mm_x = self.width_px / width_mm
        self.px_per_mm_y = self.height_px / height_mm
        bps = im.get("bits-per-sample") if im.get_typeof("bits-per-sample") else 8
        self.bilevel = bool(self.bands == 1 and bps == 1)

    def dpi_at(self, scale: float) -> tuple[float, float]:
        """Effective resolution (x, y) when the page is printed at ``scale``."""
        return (
            self.px_per_mm_x * MM_PER_INCH / scale,
            self.px_per_mm_y * MM_PER_INCH / scale,
        )


# --------------------------------------------------------------------------- encoding


def _up_filtered(rows: "NDArray[np.uint8]") -> bytes:
    """PNG ``Up`` predictor (type 2) applied to every row, then Flate."""
    h, rowbytes = rows.shape
    out = np.empty((h, rowbytes + 1), dtype=np.uint8)
    out[:, 0] = 2
    out[0, 1:] = rows[0]
    if h > 1:
        out[1:, 1:] = rows[1:] - rows[:-1]  # uint8 arithmetic wraps modulo 256
    return zlib.compress(out.tobytes(), 6)


def _add_image(
    doc: pymupdf.Document,
    w: int,
    h: int,
    cs: str,
    bpc: int,
    data: bytes,
    filt: str,
    parms: str | None = None,
) -> int:
    """Image XObject whose (already encoded) stream is ``data``."""
    xref: int = doc.get_new_xref()
    doc.update_object(
        xref,
        f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}/ColorSpace/{cs}"
        f"/BitsPerComponent {bpc}>>",
    )
    doc.update_stream(xref, data, new=True, compress=False)
    # update_stream rewrites the dictionary; the filter keys must be set afterwards
    doc.xref_set_key(xref, "Filter", f"/{filt}")
    if parms is not None:
        doc.xref_set_key(xref, "DecodeParms", parms)
    return xref


def _flate_parms(colors: int, bpc: int, columns: int) -> str:
    return f"<</Predictor 15/Colors {colors}/BitsPerComponent {bpc}/Columns {columns}>>"


def _is_line_art(arr: "NDArray[np.uint8]") -> bool:
    h, w = arr.shape[:2]
    step = max(1, int(math.sqrt(h * w / _SAMPLE_PIXELS)))
    sample = arr[::step, ::step].reshape(-1, 3).astype(np.uint32)
    codes = (sample[:, 0] << 16) | (sample[:, 1] << 8) | sample[:, 2]
    return int(np.unique(codes).size) <= LINE_ART_MAX_COLORS


def embed_tile(
    doc: pymupdf.Document,
    arr: "NDArray[np.uint8]",
    *,
    bilevel: bool,
    jpeg_quality: int,
) -> int:
    """Add the pixels as an image XObject with the compression that suits them (SAL-07)."""
    h, w, bands = arr.shape
    if bands == 1:
        if bilevel:
            packed = np.packbits(arr[:, :, 0] > 127, axis=1)
            return _add_image(
                doc, w, h, "DeviceGray", 1, _up_filtered(packed), "FlateDecode",
                _flate_parms(1, 1, w),
            )  # fmt: skip
        return _add_image(
            doc, w, h, "DeviceGray", 8, _up_filtered(arr[:, :, 0]), "FlateDecode",
            _flate_parms(1, 8, w),
        )  # fmt: skip
    if _is_line_art(arr):
        return _add_image(
            doc, w, h, "DeviceRGB", 8, _up_filtered(arr.reshape(h, w * 3)), "FlateDecode",
            _flate_parms(3, 8, w),
        )  # fmt: skip
    im: Any = vips.Image.new_from_memory(arr.tobytes(), w, h, 3, "uchar")
    jpg: bytes = im.write_to_buffer(".jpg", Q=jpeg_quality, strip=True, optimize_coding=True)
    return _add_image(doc, w, h, "DeviceRGB", 8, jpg, "DCTDecode")


# ----------------------------------------------------------------------------- placing


def _insert_clipped(page: pymupdf.Page, xref: int, dest: Rect, clip: Rect) -> None:
    """Draw image ``xref`` into ``dest`` (sheet mm), clipped to ``clip`` (sheet mm)."""
    doc = page.parent
    before = set(page.get_contents())
    page.insert_image(
        pymupdf.Rect(*(mm_to_pt(v) for v in dest.as_list())),
        xref=xref,
        keep_proportion=False,
        overlay=True,
    )
    height = page.rect.height
    x0, x1 = mm_to_pt(clip.x0), mm_to_pt(clip.x1)
    y_top, y_bot = height - mm_to_pt(clip.y0), height - mm_to_pt(clip.y1)  # PDF is y-up
    head = f"q {x0:.4f} {y_bot:.4f} {x1 - x0:.4f} {y_top - y_bot:.4f} re W n\n".encode()
    for xr in page.get_contents():
        if xr not in before:
            doc.update_stream(xr, head + doc.xref_stream(xr) + b"\nQ\n")


def place_raster(
    page: pymupdf.Page,
    src: RasterSource,
    tf: ViewTransform,
    visible: Rect,
    vmap: ViewMap,
    *,
    max_dpi: float,
    jpeg_quality: int,
) -> bool:
    """Embed the crop of ``src`` that covers ``visible`` (view mm) on ``page``.

    ``vmap`` maps view space to the sheet. Returns ``False`` when nothing was drawn (the crop is
    empty or all white). Colour modes are applied beforehand (:mod:`a0td.render.color`).
    """
    cv = tf.to_canonical(visible)
    x0 = max(0, math.floor(cv.x0 * src.px_per_mm_x + EPS))
    y0 = max(0, math.floor(cv.y0 * src.px_per_mm_y + EPS))
    x1 = min(src.width_px, math.ceil(cv.x1 * src.px_per_mm_x - EPS))
    y1 = min(src.height_px, math.ceil(cv.y1 * src.px_per_mm_y - EPS))
    if x1 <= x0 or y1 <= y0:
        return False
    w, h = x1 - x0, y1 - y0
    tile: Any = src.im.extract_area(x0, y0, w, h)

    dpi_x, dpi_y = src.dpi_at(vmap.scale)
    if dpi_x > max_dpi or dpi_y > max_dpi:
        fx, fy = min(1.0, max_dpi / dpi_x), min(1.0, max_dpi / dpi_y)
        nw, nh = max(1, round(w * fx)), max(1, round(h * fy))
        # COL-05: nearest neighbour keeps 1-bit line art crisp, Lanczos for continuous tone
        kernel = "nearest" if src.bilevel else "lanczos3"
        tile = tile.resize(nw / w, vscale=nh / h, kernel=kernel)
    rot = _ROT[tf.rotate]
    if rot is not None:
        tile = tile.rot(rot)
    if tf.mirror:  # ESC-06: the view is the rotated crop flipped horizontally
        tile = tile.fliphor()

    buf = tile.write_to_memory()
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(tile.height, tile.width, tile.bands)
    if int(arr.min()) == 255:  # nothing but white paper
        return False

    exact = Rect(
        x0 / src.px_per_mm_x, y0 / src.px_per_mm_y, x1 / src.px_per_mm_x, y1 / src.px_per_mm_y
    )
    dest = vmap.rect(tf.to_view(exact))
    xref = embed_tile(page.parent, arr, bilevel=src.bilevel, jpeg_quality=jpeg_quality)
    _insert_clipped(page, xref, dest, vmap.rect(visible))
    return True
