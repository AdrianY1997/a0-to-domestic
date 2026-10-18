"""Modern raster converter: HEIC / HEIF / AVIF (pillow-heif) and JPEG 2000 (Pillow / OpenJPEG).

The image is decoded once into an intermediate PNG (orientation applied, ICC and DPI kept) and
then follows the regular raster path, so the result is the same ``page-0.tif``.
"""

from pathlib import Path

from PIL import Image, ImageOps

from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest.canonical import canonicalize_raster
from a0td.ingest.converters.base import ConvertContext
from a0td.ingest.converters.common import fresh_dir
from a0td.ingest.models import CanonicalDocument, DocFormat
from a0td.runtime import is_web

_EXIF_XRES, _EXIF_YRES, _EXIF_UNIT = 282, 283, 296


def _register() -> None:
    import pillow_heif

    pillow_heif.register_heif_opener()
    if hasattr(pillow_heif, "register_avif_opener"):
        pillow_heif.register_avif_opener()


def _exif_dpi(img: Image.Image) -> tuple[float, float] | None:
    try:
        exif = img.getexif()
        x, y = float(exif.get(_EXIF_XRES)), float(exif.get(_EXIF_YRES))
        unit = int(exif.get(_EXIF_UNIT, 2))
    except (TypeError, ValueError, AttributeError):
        return None
    if x <= 0 or y <= 0:
        return None
    return (x * 2.54, y * 2.54) if unit == 3 else (x, y)


def _web_handles() -> frozenset[DocFormat]:
    """Formats the browser build can decode: its Pillow has no OpenJPEG and its libheif no AV1."""
    from PIL import features

    handles = {DocFormat.HEIF}
    av1 = features.check("avif")
    try:
        import pillow_heif

        decoders = pillow_heif.libheif_info().get("decoders", {})
        av1 = av1 or any(name in decoders for name in ("dav1d", "aom", "libgav1", "svt"))
    except (ImportError, OSError, AttributeError):
        pass
    if av1:
        handles.add(DocFormat.AVIF)
    if features.check("jpg_2000"):
        handles.add(DocFormat.JP2)
    return frozenset(handles)


class HeifConverter:
    id = "heif"
    _ALL = frozenset({DocFormat.HEIF, DocFormat.AVIF, DocFormat.JP2})

    @property
    def handles(self) -> frozenset[DocFormat]:  # type: ignore[override]
        return _web_handles() if is_web() else self._ALL

    def available(self) -> bool:
        try:
            import pillow_heif  # noqa: F401
        except (ImportError, OSError):
            return False
        return True

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        fmt = ctx.format or DocFormat.HEIF
        scratch = ctx.scratch_dir(work)
        limit_px = ctx.config.max_megapixels * 1_000_000
        _register()
        mid = fresh_dir(scratch / "heif") / "decoded.png"
        try:
            with Image.open(src) as img:
                if img.width * img.height > limit_px:
                    raise DomainError(
                        ErrorCode.PIXEL_LIMIT_EXCEEDED,
                        f"image is {img.width}x{img.height} px, "
                        f"the limit is {ctx.config.max_megapixels} Mpx",
                        params={"limit_mpx": ctx.config.max_megapixels},
                    )
                dpi = _exif_dpi(img)
                icc = img.info.get("icc_profile")
                img.load()
                out = ImageOps.exif_transpose(img)
                if out.mode not in ("1", "L", "LA", "RGB", "RGBA"):
                    out = out.convert("RGBA" if "A" in out.getbands() else "RGB")
                save: dict[str, object] = {"compress_level": 1}
                if dpi:
                    save["dpi"] = dpi
                if icc:
                    save["icc_profile"] = icc
                out.save(mid, format="PNG", **save)  # type: ignore[arg-type]
        except DomainError:
            raise
        except Image.DecompressionBombError as exc:
            raise DomainError(ErrorCode.PIXEL_LIMIT_EXCEEDED, "image has too many pixels") from exc
        except Exception as exc:
            raise DomainError(ErrorCode.CORRUPT_FILE, f"cannot decode the image: {exc}") from exc
        canon = canonicalize_raster(mid, work, ctx, DocFormat.PNG)
        canon.source_format = fmt
        return canon
