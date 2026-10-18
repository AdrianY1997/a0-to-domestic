"""Colour modes and rasterized output (SPEC COL-02..05, SAL-04).

Source preparation happens once per job, before the tiles are drawn:

* grey scale of a vector document keeps it vector: Ghostscript ``pdfwrite`` writes a grey copy of
  the canonical PDF (cached next to it);
* black and white, invert, background whitening and the ``rasterize`` option turn a vector page
  into pixels (PyMuPDF at ``output.dpi``, then libvips operations) and save it as a tiled TIFF
  that the raster engine consumes like any scanned page. Raster documents get the same operations
  applied to their canonical TIFF.
"""

import hashlib
import json
import math
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf

from a0td.config import get_config
from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.settings import ColorMode, ColorSettings, JobSettings
from a0td.domain.units import MM_PER_INCH
from a0td.imaging import backend_name, vips
from a0td.ingest import IngestResult, Page
from a0td.ingest.converters.base import run_tool, tool_available
from a0td.ingest.models import Layer
from a0td.render.layers import (
    has_layer_changes,
    layer_states,
    reopen_with_layers,
)
from a0td.runtime import is_web

MAX_RASTER_PIXELS = 100_000_000 if is_web() else 400_000_000  # wasm32 memory in the browser
_STATS_PIXELS = 1_000_000
_BACKGROUND_PERCENTILE = 95.0
_KNEE = 220  # after the levels, grey values above this become paper white
_DARK = 128  # luminance below this counts as ink for the despeckle
GRAY_CACHE_NAME = "canonical-gray.pdf"
GRAY_META_NAME = "canonical-gray.json"
BAND_ROWS = 1024  # rows per rasterization band: bounds the pixmap held in memory
_GS_ARGS = [
    "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite",
    "-dCompatibilityLevel=1.7", "-sColorConversionStrategy=Gray",
    "-dProcessColorModel=/DeviceGray", "-dAutoRotatePages=/None",
]  # fmt: skip
_ISOLATION_KERNEL = [[1, 1, 1], [1, 0, 1], [1, 1, 1]]


@dataclass(slots=True)
class PreparedSources:
    """Replacements for the ingested pages: a grey canonical PDF and/or per-page TIFFs."""

    pdf: Path | None = None
    tifs: dict[int, Path] = field(default_factory=dict)
    layers_rasterized: bool = False  # layers forced the vector pages to be rasterized
    gray_rasterized: bool = False  # no Ghostscript: the grey vector page was rasterized


def pixel_ops(color: ColorSettings) -> bool:
    """True when the colour settings change pixels (grey scale of a vector page does not)."""
    return color.mode is ColorMode.BW or color.invert or color.whiten_background


def needs_raster_pdf(settings: JobSettings) -> bool:
    """A vector document must be rasterized first (COL-03, COL-04, SAL-04)."""
    return pixel_ops(settings.color) or settings.output.rasterize


def needs_prepare(ingest: IngestResult, settings: JobSettings) -> bool:
    if ingest.kind == "pdf":
        return needs_raster_pdf(settings) or settings.color.mode is not ColorMode.COLOR
    return settings.color.mode is not ColorMode.COLOR or pixel_ops(settings.color)


# ------------------------------------------------------------------------ vector grey (COL-02)


@lru_cache(maxsize=1)
def _gs_version() -> str:
    try:
        out = subprocess.run(
            ["gs", "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_key(canonical: Path) -> dict[str, Any]:
    return {
        "size": canonical.stat().st_size,
        "sha256": _sha256(canonical),
        "gs_args": _GS_ARGS,
        "gs_version": _gs_version(),
    }


def _cache_valid(target: Path, meta: Path, key: dict[str, Any]) -> bool:
    if not (target.exists() and meta.exists()):
        return False
    try:
        return bool(json.loads(meta.read_text(encoding="utf-8")) == key)
    except (OSError, ValueError):
        return False


def grayscale_pdf(canonical: Path, cache: Path, scratch: Path) -> Path:
    """Grey copy of ``canonical`` through Ghostscript ``pdfwrite``.

    Reused while the sidecar (size + sha256 of the canonical PDF, gs arguments and version)
    matches; written to a unique temporary name and moved into place atomically.
    """
    target = cache / GRAY_CACHE_NAME
    meta = cache / GRAY_META_NAME
    key = _cache_key(canonical)
    if _cache_valid(target, meta, key):
        return target
    cache.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    partial = cache / f"{GRAY_CACHE_NAME}.{token}.part"
    partial_meta = cache / f"{GRAY_META_NAME}.{token}.part"
    cfg = get_config()
    try:
        run_tool(
            ["gs", *_GS_ARGS, f"-sOutputFile={partial}", "-f", canonical],
            scratch=scratch,
            timeout_s=float(cfg.job_timeout_s),
            memory_mb=cfg.job_memory_mb,
        )
        partial_meta.write_text(json.dumps(key), encoding="utf-8")
        os.replace(partial, target)
        os.replace(partial_meta, meta)
    finally:
        partial.unlink(missing_ok=True)
        partial_meta.unlink(missing_ok=True)
    return target


# ------------------------------------------------------------------------ pixel operations


def _luminance(im: Any) -> Any:
    if im.bands >= 3:
        return im.extract_band(0, n=3).colourspace("b-w").cast("uchar")
    return im.extract_band(0)


def whiten(im: Any) -> Any:
    """White point from the brightness distribution, a knee to paper white and despeckle."""
    grey = _luminance(im)
    f = max(1, int(math.sqrt(grey.width * grey.height / _STATS_PIXELS)))
    small = grey.shrink(f, f) if f > 1 else grey
    arr = np.frombuffer(small.write_to_memory(), dtype=np.uint8)
    hi = float(np.percentile(arr, _BACKGROUND_PERCENTILE))
    if 8.0 <= hi < 255.0:  # white point only: the ink keeps its darkness
        im = im.linear(255.0 / hi, 0).cast("uchar")
    grey = _luminance(im)
    im = (grey >= _KNEE).ifthenelse(255, im)
    # despeckle: ink pixels without any ink neighbour are dust
    dark = _luminance(im) < _DARK
    neighbours = dark.conv(vips.Image.new_from_array(_ISOLATION_KERNEL))
    isolated = dark & (neighbours == 0)
    result: Any = isolated.ifthenelse(255, im)
    return result.cast("uchar")


def apply_color(im: Any, color: ColorSettings) -> Any:
    """Grey / black and white / whiten / invert, in that order, on an 8-bit image."""
    if color.mode is not ColorMode.COLOR and im.bands > 1:
        im = _luminance(im)
    if color.whiten_background:
        im = whiten(im)
    if color.mode is ColorMode.BW:
        im = (im > color.threshold).cast("uchar")
    if color.invert:
        im = im.invert()
    result: Any = im
    return result


def _save_tiff(im: Any, path: Path, *, bilevel: bool, ppm: float) -> None:
    kw: dict[str, Any] = {"bitdepth": 1} if bilevel else {}
    im.copy(xres=ppm, yres=ppm).tiffsave(
        str(path),
        tile=True,
        tile_width=256,
        tile_height=256,
        compression="deflate",
        predictor="horizontal" if not bilevel else "none",
        **kw,
    )


# ------------------------------------------------------------------------------- preparation


def effective_dpi(w_pt: float, h_pt: float, dpi: float, max_pixels: int | None = None) -> float:
    """``dpi``, lowered so a ``w_pt`` x ``h_pt`` page stays within the pixel cap."""
    cap = MAX_RASTER_PIXELS if max_pixels is None else max_pixels
    area_in2 = w_pt * h_pt / 5184.0
    if area_in2 * dpi * dpi > cap:
        return math.sqrt(cap / area_in2)
    return float(dpi)


@contextmanager
def _open_layered(
    path: Path, layers: list[Layer] | None, hidden: list[str] | None, visible: list[str] | None
) -> Iterator[pymupdf.Document]:
    """``path`` with the effective layer state applied; OCGs are resolved by name, because
    ``path`` may be a Ghostscript copy whose xrefs differ from the canonical's."""
    doc = pymupdf.open(path)
    try:
        doc = reopen_with_layers(doc, layer_states(doc, layers or [], hidden or [], visible))
        yield doc
    finally:
        doc.close()


@contextmanager
def _min_line_width(min_mm: float | None, dpi: float) -> Iterator[None]:
    """COL-06: MuPDF's global minimum stroke width (in device pixels) for the enclosed drawing."""
    if not min_mm:
        yield
        return
    pymupdf.TOOLS.set_graphics_min_line_width(min_mm / MM_PER_INCH * dpi)
    try:
        yield
    finally:
        pymupdf.TOOLS.set_graphics_min_line_width(0)


def _rasterize_page(
    canonical: Path,
    pno: int,
    dpi: float,
    grey: bool,
    scratch: Path,
    *,
    layers: list[Layer] | None = None,
    hidden: list[str] | None = None,
    visible: list[str] | None = None,
    min_line_mm: float | None = None,
) -> tuple[Any, float]:
    """The whole canonical page as a libvips image, and the effective dpi it was drawn at.

    The resolution drops below ``dpi`` when the page would exceed ``MAX_RASTER_PIXELS``; callers
    must use the returned value. The page is drawn in horizontal bands (``clip=``) that are
    streamed to a raw file, so memory stays bounded; the returned image reads that file
    (``scratch`` must outlive it). Without libvips (no streaming) the bands are written into
    one in-memory array instead. ``hidden`` layers (ANA-07) are switched off and strokes are
    kept at least ``min_line_mm`` wide (COL-06) while drawing.
    """
    n = 1 if grey else 3
    with _open_layered(canonical, layers, hidden, visible) as doc:
        page = doc[pno]
        w_pt, h_pt = page.rect.width, page.rect.height
        eff = effective_dpi(w_pt, h_pt, dpi)
        z = eff / 72.0
        width, height = math.ceil(w_pt * z - 1e-6), math.ceil(h_pt * z - 1e-6)
        cs = pymupdf.csGRAY if grey else pymupdf.csRGB
        if backend_name() != "pyvips":
            return _rasterize_in_memory(page, z, width, height, n, cs, min_line_mm, eff), eff
        scratch.mkdir(parents=True, exist_ok=True)
        raw = scratch / f"raster-{uuid.uuid4().hex}.raw"
        with raw.open("wb") as fh, _min_line_width(min_line_mm, eff):
            fh.truncate(width * height * n)
            for y0 in range(0, height, BAND_ROWS):
                y1 = min(height, y0 + BAND_ROWS)
                clip = pymupdf.Rect(0, y0 / z, w_pt, min(h_pt, y1 / z))
                pix = page.get_pixmap(
                    matrix=pymupdf.Matrix(z, z), clip=clip, colorspace=cs, alpha=False
                )
                arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, n)
                band = np.full((pix.height, width, n), 255, np.uint8)
                x_from, x_to = max(0, pix.x), min(width, pix.x + pix.width)
                if x_to > x_from:
                    band[:, x_from:x_to] = arr[:, x_from - pix.x : x_to - pix.x]
                r0, r1 = max(0, pix.y), min(height, pix.y + pix.height)
                if r1 > r0:
                    fh.seek(r0 * width * n)
                    fh.write(band[r0 - pix.y : r1 - pix.y].tobytes())
                del pix, arr, band
    im: Any = vips.Image.rawload(str(raw), width, height, n)
    return im, eff


def _rasterize_in_memory(
    page: pymupdf.Page,
    z: float,
    width: int,
    height: int,
    n: int,
    cs: pymupdf.Colorspace,
    min_line_mm: float | None,
    eff: float,
) -> Any:
    """Same bands as the streaming path, collected in one array (backends without streaming)."""
    out = np.full((height, width, n), 255, np.uint8)
    with _min_line_width(min_line_mm, eff):
        for y0 in range(0, height, BAND_ROWS):
            y1 = min(height, y0 + BAND_ROWS)
            clip = pymupdf.Rect(0, y0 / z, page.rect.width, min(page.rect.height, y1 / z))
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(z, z), clip=clip, colorspace=cs, alpha=False
            )
            arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, n)
            x_from, x_to = max(0, pix.x), min(width, pix.x + pix.width)
            r0, r1 = max(0, pix.y), min(height, pix.y + pix.height)
            if x_to > x_from and r1 > r0:
                out[r0:r1, x_from:x_to] = arr[
                    r0 - pix.y : r1 - pix.y, x_from - pix.x : x_to - pix.x
                ]
            del pix, arr
    return vips.Image.new_from_memory(out, width, height, n, "uchar")


def _to_srgb8(im: Any) -> Any:
    """8-bit grey (1 band) or sRGB (3 bands) from whatever the source decoded to."""
    if im.hasalpha():
        im = im.flatten(background=255)
    interp = im.interpretation
    if interp == "grey16":
        im = (im / 257.0).cast("uchar")
    elif interp == "rgb16":
        im = im.colourspace("srgb")
    elif im.bands >= 3 and interp not in ("srgb", "rgb", "b-w"):
        im = im.colourspace("srgb")  # CMYK, Lab...: never read the bands as RGB blindly
    elif im.bands > 3:
        im = im.extract_band(0, n=3)
    if im.bands == 2:
        im = im.extract_band(0)
    if im.format != "uchar":
        im = im.cast("uchar")
    result: Any = im
    return result


def _page_ppm(page: Page) -> float:
    """Pixels per mm of an ingested raster page, from its ingest metadata (never guessed)."""
    if page.dpi is not None and page.dpi > 0:
        return float(page.dpi) / 25.4
    if page.px is not None and page.width_mm > 0:
        return page.px.w / page.width_mm
    raise DomainError(
        ErrorCode.INVALID_SETTINGS,
        f"page {page.index + 1} has no resolution metadata",
        errors=[{"loc": "source.dpi", "message": "page resolution is unknown"}],
    )


def _prepare_raster_page(src: Path, page: Page, settings: JobSettings, dst: Path) -> None:
    ppm = _page_ppm(page)
    im: Any = vips.Image.new_from_file(str(src), access="sequential")
    bps = im.get("bits-per-sample") if im.get_typeof("bits-per-sample") else 8
    one_bit = im.bands == 1 and bps == 1
    im = _to_srgb8(im)
    if settings.color.whiten_background:
        im = im.copy_memory()  # whiten reads the image twice
    out = apply_color(im, settings.color)
    keep_bits = one_bit and not settings.color.whiten_background
    bilevel = out.bands == 1 and (settings.color.mode is ColorMode.BW or keep_bits)
    _save_tiff(out, dst, bilevel=bilevel, ppm=ppm)


def _layers_active(ingest: IngestResult, selected: list[int], settings: JobSettings) -> bool:
    src = settings.source
    return any(
        has_layer_changes(ingest.pages[i].layers, src.hidden_layers, src.visible_layers)
        for i in selected
    )


def layers_force_raster(ingest: IngestResult, selected: list[int], settings: JobSettings) -> bool:
    """Layer changes that a vector output cannot honour (ANA-07): hidden content must never print.

    Grayscale vector goes through a Ghostscript copy, which bakes the layer state (content of
    layers that are OFF in the source is dropped, so ``visible_layers`` could not be honoured)
    and renumbers objects: when any layer changes state, the real document is rasterized.
    """
    if ingest.kind != "pdf":
        return False
    return settings.color.mode is ColorMode.GRAYSCALE and _layers_active(ingest, selected, settings)


def prepare_sources(
    doc_dir: Path, ingest: IngestResult, selected: list[int], settings: JobSettings, work: Path
) -> PreparedSources:
    """Build the colour-mode / rasterized variants of the selected pages inside ``work``."""
    prepared = PreparedSources()
    color = settings.color
    work.mkdir(parents=True, exist_ok=True)
    if ingest.kind == "pdf":
        assert ingest.canonical_pdf is not None
        canonical = doc_dir / ingest.canonical_pdf
        layers_forced = layers_force_raster(ingest, selected, settings)
        # No Ghostscript (browser build): a grey vector page is drawn as a grey raster instead.
        no_gs = color.mode is ColorMode.GRAYSCALE and not tool_available("gs")
        forced = layers_forced or no_gs
        if not needs_raster_pdf(settings) and not forced:
            if color.mode is ColorMode.GRAYSCALE:
                prepared.pdf = _cached_grey(canonical, doc_dir, work)
            return prepared
        prepared.layers_rasterized = layers_forced and not needs_raster_pdf(settings)
        prepared.gray_rasterized = no_gs and not layers_forced and not needs_raster_pdf(settings)
        source = canonical
        if (
            color.mode is ColorMode.GRAYSCALE
            and not no_gs
            and not _layers_active(ingest, selected, settings)
        ):
            source = _cached_grey(canonical, doc_dir, work)
        grey = color.mode is not ColorMode.COLOR
        for idx in selected:
            im, eff_dpi = _rasterize_page(
                source,
                idx,
                settings.output.dpi,
                grey,
                work / "raw",
                layers=ingest.pages[idx].layers,
                hidden=settings.source.hidden_layers,
                visible=settings.source.visible_layers,
                min_line_mm=color.min_line_width_mm,
            )
            out = apply_color(im, color)
            dst = work / f"page-{idx}.tif"
            _save_tiff(out, dst, bilevel=color.mode is ColorMode.BW, ppm=eff_dpi / 25.4)
            del im, out
            shutil.rmtree(work / "raw", ignore_errors=True)
            prepared.tifs[idx] = dst
        return prepared
    if color.mode is ColorMode.COLOR and not pixel_ops(color):
        return prepared  # rasterizing an image document changes nothing
    for idx in selected:
        dst = work / f"page-{idx}.tif"
        _prepare_raster_page(doc_dir / ingest.page_files[idx], ingest.pages[idx], settings, dst)
        prepared.tifs[idx] = dst
    return prepared


def _cached_grey(canonical: Path, doc_dir: Path, work: Path) -> Path:
    """Grey PDF cached in ``<doc_dir>/cache``; a read-only document dir falls back to ``work``."""
    scratch = work / "gs"
    try:
        return grayscale_pdf(canonical, doc_dir / "cache", scratch)
    except OSError:
        return grayscale_pdf(canonical, work / "cache", scratch)


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
