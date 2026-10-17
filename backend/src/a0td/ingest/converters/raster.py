"""Common raster converter (png, jpeg, tiff, gif, bmp, webp) through libvips."""

from pathlib import Path

from a0td.ingest.canonical import canonicalize_raster
from a0td.ingest.converters.base import ConvertContext
from a0td.ingest.models import CanonicalDocument, DocFormat


class RasterConverter:
    id = "raster"
    handles = frozenset(
        {
            DocFormat.PNG,
            DocFormat.JPEG,
            DocFormat.TIFF,
            DocFormat.GIF,
            DocFormat.BMP,
            DocFormat.WEBP,
        }
    )

    def available(self) -> bool:
        try:
            import pyvips  # noqa: F401
        except (ImportError, OSError):  # missing libvips shared library
            return False
        return True

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        from a0td.ingest.detect import detect_format

        fmt = ctx.format or detect_format(src) or DocFormat.PNG
        return canonicalize_raster(src, work, ctx, fmt)
