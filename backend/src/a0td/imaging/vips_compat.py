"""Pillow + numpy stand-in for the subset of ``pyvips`` that a0td uses.

Used when libvips is not available (the in-browser Pyodide build, SPEC 17bis) or when forced with
``A0TD_IMAGING=pillow``. It mimics the *names and semantics* of the pyvips calls the ingest and
render code make (``Image.new_from_file``, ``resize``, ``flatten``, ``tiffsave``...), so those
modules have a single code path: ``from a0td.imaging import vips``.

Differences from libvips, by design:

* Images are eager numpy arrays ``(height, width, bands)``. Only the *header* of a file is read by
  :meth:`Image.new_from_file`; the pixels are decoded on first use, so size limits can be checked
  before a huge image is loaded. There is no streaming: the web build caps image sizes instead.
* TIFF output is written in strips (Pillow cannot write tiled TIFF); ``tile*``/``predictor``/
  ``strip`` options are accepted and ignored.
* ICC profiles are honoured only when Pillow was built with littlecms (``ImageCms``); CMYK is
  converted with the naive formula otherwise. See :func:`icc_supported`.
* Grey conversion matches libvips (gamma aware luminance), resampling kernels are Pillow's.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable
from typing import Any

import numpy as np
from PIL import Image as PILImage

__all__ = ["Error", "GValue", "Image", "PixelLimitError", "cache_set_max", "icc_supported"]

_ROW_CHUNK = 512
_state = {"no_deflate": False}  # libtiff without Deflate (Pyodide): use LZW instead


class Error(Exception):
    """Equivalent of ``pyvips.Error``."""


class PixelLimitError(Error):
    """The file declares more pixels than Pillow's decompression-bomb guard allows."""


class GValue:
    gint_type = 1  # only used as a token for ``Image.set_type``


def cache_set_max(_n: int) -> None:
    """No operation cache here."""


def icc_supported() -> bool:
    """Whether ICC profiles can be applied (Pillow built with littlecms)."""
    from PIL import features

    return bool(features.check("littlecms2"))


_FORMAT_DTYPE: dict[str, Any] = {
    "uchar": np.uint8,
    "ushort": np.uint16,
    "float": np.float32,
    "double": np.float64,
}
_DTYPE_FORMAT: dict[Any, str] = {np.dtype(v): k for k, v in _FORMAT_DTYPE.items()}

_EXIF_ORIENTATION = 0x0112


def _guess_interpretation(bands: int, dtype: Any) -> str:
    if bands == 1:
        return "b-w"
    if bands == 3:
        return "srgb"
    return "multiband"


# ---------------------------------------------------------------- colour conversion tables


def _build_tables() -> tuple[np.ndarray, np.ndarray]:
    v = np.arange(256, dtype=np.float64) / 255.0
    lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    n = 16384
    y = np.arange(n + 1, dtype=np.float64) / n
    enc = np.where(y <= 0.0031308, 12.92 * y, 1.055 * np.power(y, 1 / 2.4) - 0.055)
    return lin.astype(np.float32), np.rint(enc * 255.0).astype(np.uint8)


_LINEAR, _ENCODE = _build_tables()
_ENCODE_N = len(_ENCODE) - 1


def _srgb_to_grey(a: np.ndarray) -> np.ndarray:
    """Gamma-aware luminance of an 8-bit RGB array (libvips ``colourspace("b-w")``)."""
    h, w = a.shape[:2]
    out = np.empty((h, w, 1), np.uint8)
    for y0 in range(0, h, _ROW_CHUNK):
        blk = a[y0 : y0 + _ROW_CHUNK, :, :3]
        lum = (
            _LINEAR[blk[..., 0]] * np.float32(0.2126)
            + _LINEAR[blk[..., 1]] * np.float32(0.7152)
            + _LINEAR[blk[..., 2]] * np.float32(0.0722)
        )
        idx = (lum * _ENCODE_N + 0.5).astype(np.int32)
        np.clip(idx, 0, _ENCODE_N, out=idx)
        out[y0 : y0 + _ROW_CHUNK, :, 0] = _ENCODE[idx]
    return out


def _cmyk_to_rgb(a: np.ndarray) -> np.ndarray:
    """Naive CMYK -> RGB (no profile)."""
    nk = 255 - a[..., 3].astype(np.uint16)
    out = np.empty((*a.shape[:2], 3), np.uint8)
    for i in range(3):
        out[..., i] = ((255 - a[..., i].astype(np.uint16)) * nk + 127) // 255
    return out


# ------------------------------------------------------------------------ PIL conversions


def _to_pil(a: np.ndarray) -> PILImage.Image:
    a = np.ascontiguousarray(a)
    if a.dtype != np.uint8:
        a = np.clip(a, 0, 255).astype(np.uint8)
    bands = a.shape[2]
    if bands == 1:
        return PILImage.fromarray(a[:, :, 0])
    if bands in (3, 4):
        return PILImage.fromarray(a)
    raise Error(f"cannot encode an image with {bands} bands")


def _from_pil(img: PILImage.Image) -> np.ndarray:
    arr = np.asarray(img)
    return arr[:, :, None] if arr.ndim == 2 else arr


def _mode_info(img: PILImage.Image) -> tuple[int, Any, str, int | None, str]:
    """``(bands, dtype, interpretation, bits-per-sample, decode_mode)`` of an opened image."""
    mode = img.mode
    if mode == "1":
        return 1, np.uint8, "b-w", 1, "L"
    if mode == "L":
        return 1, np.uint8, "b-w", None, "L"
    if mode in ("LA", "La"):
        return 2, np.uint8, "b-w", None, "LA"
    if mode in ("P", "PA", "RGBA", "RGBa"):
        alpha = mode != "P" or "transparency" in img.info
        return (4, np.uint8, "srgb", None, "RGBA") if alpha else (3, np.uint8, "srgb", None, "RGB")
    if mode == "RGB":
        return 3, np.uint8, "srgb", None, "RGB"
    if mode == "CMYK":
        return 4, np.uint8, "cmyk", None, "CMYK"
    if mode in ("I;16", "I;16L", "I;16B", "I;16N", "I"):
        return 1, np.uint16, "grey16", None, "I16"
    return 3, np.uint8, "srgb", None, "RGB"


def _decode(img: PILImage.Image, decode: str) -> np.ndarray:
    img.load()
    if decode == "I16":
        arr = np.asarray(img)
        if arr.dtype != np.uint16:
            arr = np.clip(arr, 0, 65535).astype(np.uint16)
        return arr[:, :, None]
    if decode == "CMYK":
        return _from_pil(img)
    return _from_pil(img.convert(decode))


# -------------------------------------------------------------------------------- Image


class Image:
    """A (height, width, bands) array with libvips-like metadata and operations."""

    __hash__ = object.__hash__

    def __init__(
        self,
        array: np.ndarray | None = None,
        *,
        interpretation: str | None = None,
        meta: dict[str, Any] | None = None,
        xres: float = 1.0,
        yres: float = 1.0,
        header: tuple[int, int, int, Any] | None = None,
        loader: Callable[[], np.ndarray] | None = None,
    ) -> None:
        self._arr = array
        self._loader = loader
        if array is not None:
            h, w, b = array.shape
            self._header = (h, w, b, array.dtype)
        elif header is not None:
            self._header = header
        else:
            raise ValueError("an image needs pixels or a header")
        self._interp = interpretation or _guess_interpretation(self._header[2], self._header[3])
        self._meta: dict[str, Any] = dict(meta or {})
        self.xres = float(xres)
        self.yres = float(yres)

    # -- construction
    @classmethod
    def new_from_file(cls, filename: str, access: str = "random", page: int = 0, **_: Any) -> Image:
        try:
            with PILImage.open(filename) as img:
                n_pages = int(getattr(img, "n_frames", 1))
                if page:
                    if page >= n_pages:
                        raise Error(f"page {page} does not exist")
                    img.seek(page)
                bands, dtype, interp, bps, decode = _mode_info(img)
                width, height = img.size
                info = dict(img.info)
                orientation = None
                try:
                    orientation = img.getexif().get(_EXIF_ORIENTATION)
                except Exception:
                    orientation = None
        except Error:
            raise
        except PILImage.DecompressionBombError as exc:
            raise PixelLimitError(str(exc)) from exc
        except Exception as exc:  # unreadable, truncated or unsupported
            raise Error(f"unable to read {filename}: {exc}") from exc

        meta: dict[str, Any] = {}
        if bps:
            meta["bits-per-sample"] = bps
        if n_pages > 1 or str(filename).lower().endswith((".tif", ".tiff")):
            meta["n-pages"] = n_pages
        if isinstance(orientation, int) and orientation > 1:
            meta["orientation"] = orientation
        icc = info.get("icc_profile")
        if isinstance(icc, bytes) and icc:
            meta["icc-profile-data"] = icc
        xres = yres = 1.0
        dpi = info.get("dpi")
        try:
            if dpi and float(dpi[0]) > 0 and float(dpi[1]) > 0:
                xres, yres = float(dpi[0]) / 25.4, float(dpi[1]) / 25.4
        except (TypeError, ValueError, IndexError):
            pass

        def load() -> np.ndarray:
            try:
                with PILImage.open(filename) as im:
                    if page:
                        im.seek(page)
                    return _decode(im, decode)
            except Exception as exc:
                raise Error(f"unable to decode {filename}: {exc}") from exc

        return cls(
            header=(height, width, bands, np.dtype(dtype)),
            loader=load,
            interpretation=interp,
            meta=meta,
            xres=xres,
            yres=yres,
        )

    @classmethod
    def new_from_memory(cls, data: Any, width: int, height: int, bands: int, fmt: str) -> Image:
        dtype = np.dtype(_FORMAT_DTYPE[fmt])
        try:
            arr = np.frombuffer(data, dtype=dtype, count=width * height * bands)
        except ValueError as exc:
            raise Error(f"memory buffer too small: {exc}") from exc
        return cls(arr.reshape(height, width, bands))

    @classmethod
    def new_from_array(cls, rows: list[list[float]] | np.ndarray, **_: Any) -> Image:
        arr = np.asarray(rows, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[None, :]
        return cls(arr[:, :, None], interpretation="b-w")

    @classmethod
    def rawload(cls, filename: str, width: int, height: int, bands: int) -> Image:
        try:
            raw = np.fromfile(filename, dtype=np.uint8, count=width * height * bands)
        except OSError as exc:
            raise Error(f"unable to read {filename}: {exc}") from exc
        if raw.size != width * height * bands:
            raise Error("raw file too small")
        return cls(raw.reshape(height, width, bands))

    @classmethod
    def thumbnail(
        cls, filename: str, width: int, height: int | None = None, size: str = "both", **_: Any
    ) -> Image:
        src = cls.new_from_file(filename)
        box_h = height if height is not None else width
        scale = min(width / src.width, box_h / src.height)
        if scale >= 1 and size == "down":
            return src
        nw = max(1, math.floor(src.width * scale + 0.5))
        nh = max(1, math.floor(src.height * scale + 0.5))
        return src._resized(nw, nh, PILImage.Resampling.LANCZOS)

    # -- header
    @property
    def height(self) -> int:
        return int(self._header[0])

    @property
    def width(self) -> int:
        return int(self._header[1])

    @property
    def bands(self) -> int:
        return int(self._header[2])

    @property
    def format(self) -> str:
        return _DTYPE_FORMAT[np.dtype(self._header[3])]

    @property
    def interpretation(self) -> str:
        return self._interp

    @property
    def _a(self) -> np.ndarray:
        if self._arr is None:
            assert self._loader is not None
            arr = self._loader()
            if arr.shape[:2] != (self.height, self.width):  # pragma: no cover - defensive
                raise Error("image changed while it was being read")
            self._arr = arr
            self._loader = None
        return self._arr

    def hasalpha(self) -> bool:
        b, i = self.bands, self._interp
        return (
            (b == 2 and i in ("b-w", "grey16"))
            or (b == 4 and i in ("srgb", "rgb", "rgb16"))
            or (b == 5 and i == "cmyk")
        )

    # -- metadata
    def get_typeof(self, name: str) -> int:
        return 1 if name in self._meta else 0

    def get(self, name: str) -> Any:
        if name not in self._meta:
            raise Error(f"no such field: {name}")
        return self._meta[name]

    def set_type(self, _gtype: int, name: str, value: Any) -> None:
        self._meta[name] = value

    def _derive(self, arr: np.ndarray, **changes: Any) -> Image:
        kw: dict[str, Any] = {
            "interpretation": None,
            "meta": self._meta,
            "xres": self.xres,
            "yres": self.yres,
        }
        kw.update(changes)
        return Image(arr, **kw)

    def copy(
        self,
        interpretation: str | None = None,
        xres: float | None = None,
        yres: float | None = None,
        **_: Any,
    ) -> Image:
        return Image(
            self._a,
            interpretation=interpretation or self._interp,
            meta=self._meta,
            xres=self.xres if xres is None else xres,
            yres=self.yres if yres is None else yres,
        )

    def copy_memory(self) -> Image:
        return self._derive(np.array(self._a), interpretation=self._interp)

    # -- band and type operations
    def extract_band(self, band: int, n: int = 1) -> Image:
        return self._derive(self._a[:, :, band : band + n], interpretation=None)

    def bandjoin(self, other: Image | list[Image]) -> Image:
        parts = [self._a, *[o._a for o in (other if isinstance(other, list) else [other])]]
        return self._derive(np.concatenate(parts, axis=2), interpretation=self._interp)

    def cast(self, fmt: str) -> Image:
        dtype = np.dtype(_FORMAT_DTYPE[fmt])
        a = self._a
        if a.dtype == dtype:
            return self
        if np.issubdtype(dtype, np.integer):
            info = np.iinfo(dtype)
            a = np.clip(a, info.min, info.max)  # libvips: saturate, then truncate
        return self._derive(a.astype(dtype), interpretation=self._interp)

    def rint(self) -> Image:
        return self._derive(np.rint(self._a), interpretation=self._interp)

    def invert(self) -> Image:
        a = self._a
        if a.dtype == np.uint8:
            return self._derive(255 - a, interpretation=self._interp)
        if a.dtype == np.uint16:
            return self._derive(65535 - a, interpretation=self._interp)
        raise Error("invert needs an integer image")

    def linear(self, a: float | list[float], b: float | list[float]) -> Image:
        out = self._a.astype(np.float32) * np.float32(a) + np.float32(b)  # type: ignore[arg-type]
        return self._derive(out, interpretation=self._interp)

    def flatten(self, background: float | list[float] | None = None, **_: Any) -> Image:
        """Blend the last band (alpha) over ``background``."""
        a = self._a
        bg = np.asarray([0.0] if background is None else np.atleast_1d(background), np.float32)
        colour, alpha = a[:, :, :-1].astype(np.float32), a[:, :, -1:].astype(np.float32) / 255.0
        if bg.size == 1:
            bg = np.repeat(bg, colour.shape[2])
        out = np.rint(colour * alpha + bg[None, None, : colour.shape[2]] * (1.0 - alpha))
        return self._derive(out.astype(a.dtype), interpretation=self._interp)

    def colourspace(self, space: str, **_: Any) -> Image:
        a, interp = self._a, self._interp
        if interp in ("multiband", "histogram"):
            interp = _guess_interpretation(self.bands, a.dtype)
        if space == interp:
            return self
        if interp == "cmyk":
            rgb = _cmyk_to_rgb(a)
            return self._derive(
                rgb if space == "srgb" else _srgb_to_grey(rgb), interpretation=space
            )
        if interp in ("grey16", "rgb16"):
            a = np.rint(a.astype(np.float32) / 257.0).astype(np.uint8)
            interp = "b-w" if interp == "grey16" else "srgb"
            if space == interp:
                return self._derive(a, interpretation=space)
        if space == "b-w":
            if a.shape[2] >= 3:
                return self._derive(_srgb_to_grey(a), interpretation="b-w")
            return self._derive(a[:, :, :1], interpretation="b-w")
        if space in ("srgb", "rgb"):
            if a.shape[2] == 1:
                return self._derive(np.repeat(a, 3, axis=2), interpretation="srgb")
            return self._derive(a[:, :, :3], interpretation="srgb")
        raise Error(f"no known route from '{interp}' to '{space}'")

    def icc_transform(
        self,
        output_profile: str,
        input_profile: str | None = None,
        embedded: bool = False,
        **_: Any,
    ) -> Image:
        """CMYK -> sRGB; RGB profiles only when littlecms is available (else unchanged)."""
        a = self._a
        if self._interp == "cmyk" or (input_profile == "cmyk" and a.shape[2] >= 4):
            return self._derive(_cmyk_to_rgb(a), interpretation="srgb")
        profile = self._meta.get("icc-profile-data")
        if not (embedded and isinstance(profile, bytes) and icc_supported()):
            return self
        from PIL import ImageCms

        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(profile))
            dst = ImageCms.createProfile("sRGB")
            out = ImageCms.profileToProfile(_to_pil(a), src, dst, outputMode="RGB")
        except Exception as exc:
            raise Error(f"icc_transform failed: {exc}") from exc
        return self._derive(_from_pil(out), interpretation="srgb")

    def autorot(self) -> Image:
        a = self._a
        o = self._meta.get("orientation", 1)
        if o == 2:
            a = a[:, ::-1]
        elif o == 3:
            a = a[::-1, ::-1]
        elif o == 4:
            a = a[::-1]
        elif o == 5:
            a = a.transpose(1, 0, 2)
        elif o == 6:
            a = np.rot90(a, -1)
        elif o == 7:
            a = a[::-1, ::-1].transpose(1, 0, 2)
        elif o == 8:
            a = np.rot90(a, 1)
        meta = {k: v for k, v in self._meta.items() if k != "orientation"}
        return Image(
            np.ascontiguousarray(a),
            interpretation=self._interp,
            meta=meta,
            xres=self.xres,
            yres=self.yres,
        )

    # -- geometry
    def extract_area(self, left: int, top: int, width: int, height: int) -> Image:
        if left < 0 or top < 0 or left + width > self.width or top + height > self.height:
            raise Error("extract_area: bad extract area")
        return self._derive(
            self._a[top : top + height, left : left + width], interpretation=self._interp
        )

    def rot(self, angle: str) -> Image:
        k = {"d0": 0, "d90": -1, "d180": 2, "d270": 1}[angle]
        return self._derive(np.ascontiguousarray(np.rot90(self._a, k)), interpretation=self._interp)

    def fliphor(self) -> Image:
        return self._derive(self._a[:, ::-1], interpretation=self._interp)

    def shrink(self, hshrink: float, vshrink: float) -> Image:
        fx, fy = max(1, int(hshrink)), max(1, int(vshrink))
        a = self._a
        h, w = (a.shape[0] // fy) * fy, (a.shape[1] // fx) * fx
        if h == 0 or w == 0:
            return self
        blk = a[:h, :w].reshape(h // fy, fy, w // fx, fx, a.shape[2])
        out = blk.mean(axis=(1, 3), dtype=np.float32)
        if a.dtype != np.float32:
            out = np.rint(out).astype(a.dtype)
        return self._derive(out, interpretation=self._interp)

    def _resized(self, nw: int, nh: int, resample: Any) -> Image:
        a = self._a
        if a.dtype != np.uint8:
            raise Error("resize needs an 8-bit image")
        if (nw, nh) == (self.width, self.height):
            return self
        if a.shape[2] in (1, 3, 4):
            out = _from_pil(_to_pil(a).resize((nw, nh), resample))
        else:
            out = np.dstack(
                [
                    _from_pil(PILImage.fromarray(a[:, :, i]).resize((nw, nh), resample))
                    for i in range(a.shape[2])
                ]
            )
        return self._derive(out, interpretation=self._interp)

    def resize(self, scale: float, vscale: float | None = None, kernel: str = "lanczos3") -> Image:
        vs = scale if vscale is None else vscale
        nw, nh = max(1, round(self.width * scale)), max(1, round(self.height * vs))
        resample = {
            "nearest": PILImage.Resampling.NEAREST,
            "linear": PILImage.Resampling.BILINEAR,
            "cubic": PILImage.Resampling.BICUBIC,
            "lanczos2": PILImage.Resampling.LANCZOS,
            "lanczos3": PILImage.Resampling.LANCZOS,
        }.get(kernel, PILImage.Resampling.LANCZOS)
        return self._resized(nw, nh, resample)

    # -- arithmetic
    def _binary(self, other: Any, fn: Callable[[Any, Any], Any], out_dtype: Any = None) -> Image:
        b = other._a if isinstance(other, Image) else other
        res = fn(self._a, b)
        if out_dtype is not None:
            res = res.astype(out_dtype)
        return self._derive(res, interpretation=self._interp)

    def __truediv__(self, other: float) -> Image:
        return self._derive(
            self._a.astype(np.float32) / np.float32(other), interpretation=self._interp
        )

    def __lt__(self, other: Any) -> Image:  # type: ignore[override]
        return self._binary(other, lambda a, b: np.where(a < b, 255, 0), np.uint8)

    def __le__(self, other: Any) -> Image:  # type: ignore[override]
        return self._binary(other, lambda a, b: np.where(a <= b, 255, 0), np.uint8)

    def __gt__(self, other: Any) -> Image:  # type: ignore[override]
        return self._binary(other, lambda a, b: np.where(a > b, 255, 0), np.uint8)

    def __ge__(self, other: Any) -> Image:  # type: ignore[override]
        return self._binary(other, lambda a, b: np.where(a >= b, 255, 0), np.uint8)

    def __eq__(self, other: Any) -> Image:  # type: ignore[override]
        return self._binary(other, lambda a, b: np.where(a == b, 255, 0), np.uint8)

    def __and__(self, other: Any) -> Image:
        return self._binary(other, lambda a, b: a & b)

    def ifthenelse(self, then: Any, otherwise: Any) -> Image:
        """Where this (condition) image is non-zero take ``then``, else ``otherwise``."""
        cond = self._a != 0
        a = then._a if isinstance(then, Image) else then
        b = otherwise._a if isinstance(otherwise, Image) else otherwise
        ref = then if isinstance(then, Image) else otherwise
        dtype = ref._a.dtype if isinstance(ref, Image) else np.uint8
        out = np.where(cond, np.asarray(a, dtype), np.asarray(b, dtype))
        return self._derive(out, interpretation=ref._interp if isinstance(ref, Image) else "b-w")

    def conv(self, mask: Image) -> Image:
        """Correlation with ``mask`` (edges replicated, like libvips); returns a float image."""
        kern = mask._a[:, :, 0]
        kh, kw = kern.shape
        py, px = kh // 2, kw // 2
        a = self._a
        h, w, b = a.shape
        out = np.empty((h, w, b), np.float32)
        padded = np.pad(a, ((py, kh - 1 - py), (px, kw - 1 - px), (0, 0)), mode="edge")
        for y0 in range(0, h, _ROW_CHUNK):
            y1 = min(h, y0 + _ROW_CHUNK)
            acc = np.zeros((y1 - y0, w, b), np.float32)
            for dy in range(kh):
                for dx in range(kw):
                    coef = float(kern[dy, dx])
                    if coef:
                        acc += np.float32(coef) * padded[y0 + dy : y1 + dy, dx : dx + w]
            out[y0:y1] = acc
        return self._derive(out, interpretation=self._interp)

    # -- output
    def write_to_memory(self) -> bytes:
        return np.ascontiguousarray(self._a).tobytes()

    def _dpi(self) -> tuple[float, float]:
        return self.xres * 25.4, self.yres * 25.4

    def write_to_buffer(self, suffix: str, **kw: Any) -> bytes:
        ext = suffix.lower().lstrip(".")
        buf = io.BytesIO()
        img = _to_pil(self._a)
        try:
            if ext == "png":
                img.save(buf, format="PNG", compress_level=int(kw.get("compression", 6)),
                         dpi=self._dpi())  # fmt: skip
            elif ext in ("jpg", "jpeg"):
                img.save(
                    buf, format="JPEG", quality=int(kw.get("Q", 75)),
                    optimize=bool(kw.get("optimize_coding", False)), dpi=self._dpi(),
                )  # fmt: skip
            else:
                raise Error(f"unsupported output format '{suffix}'")
        except OSError as exc:
            raise Error(f"cannot encode the image: {exc}") from exc
        return buf.getvalue()

    def _tiff_codecs(self, kw: dict[str, Any]) -> list[str | None]:
        """Pillow codec names to try, best first (some libtiff builds lack Deflate: Pyodide)."""
        wanted = str(kw.get("compression", "none"))
        if int(kw.get("bitdepth", 8)) == 1 and wanted != "none":
            return ["group4", "tiff_lzw"]
        chain: dict[str, list[str | None]] = {
            "deflate": ["tiff_adobe_deflate", "tiff_lzw"],
            "lzw": ["tiff_lzw"],
            "ccittfax4": ["group4", "tiff_lzw"],
            "jpeg": ["jpeg"],
        }
        codecs = chain.get(wanted, [None])
        if "tiff_adobe_deflate" in codecs and _state["no_deflate"]:
            codecs = codecs[1:]
        return codecs

    def _save_tiff(self, target: Any, kw: dict[str, Any]) -> None:
        a = self._a
        if int(kw.get("bitdepth", 8)) == 1 and a.shape[2] == 1:
            img = PILImage.fromarray(np.where(a[:, :, 0] > 127, 255, 0).astype(np.uint8)).convert(
                "1", dither=PILImage.Dither.NONE
            )
        else:
            img = _to_pil(a)
        last: Exception | None = None
        for codec in self._tiff_codecs(kw):
            opts: dict[str, Any] = {"format": "TIFF", "dpi": self._dpi()}
            if codec is not None:
                opts["compression"] = codec
            try:
                if hasattr(target, "seek"):
                    target.seek(0)
                    target.truncate()
                img.save(target, **opts)
                return
            except (OSError, ValueError, KeyError) as exc:
                last = exc
                if codec == "tiff_adobe_deflate":
                    _state["no_deflate"] = True
        raise Error(f"cannot encode the TIFF: {last}")

    def tiffsave(self, filename: str, **kw: Any) -> None:
        try:
            self._save_tiff(filename, kw)
        except Error as exc:
            raise Error(f"cannot write {filename}: {exc}") from exc

    def tiffsave_buffer(self, **kw: Any) -> bytes:
        buf = io.BytesIO()
        self._save_tiff(buf, kw)
        return buf.getvalue()
