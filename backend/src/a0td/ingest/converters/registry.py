"""Converter registry (SPEC 7.2). New formats are added by registering a converter."""

from collections.abc import Iterable
from typing import Any

from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest.converters.base import Converter
from a0td.ingest.models import DocFormat


class ConverterRegistry:
    def __init__(self, converters: Iterable[Converter] = ()) -> None:
        self._converters: list[Converter] = []
        for conv in converters:
            self.register(conv)

    def register(self, converter: Converter) -> None:
        self._converters.append(converter)

    @property
    def converters(self) -> list[Converter]:
        return list(self._converters)

    def for_format(self, fmt: DocFormat) -> Converter | None:
        """First *available* converter that handles ``fmt``."""
        for conv in self._converters:
            if fmt in conv.handles and conv.available():
                return conv
        return None

    def resolve(self, fmt: DocFormat) -> Converter:
        """Converter for ``fmt`` or ``FORMAT_NOT_AVAILABLE_IN_IMAGE``."""
        conv = self.for_format(fmt)
        if conv is None:
            raise DomainError(
                ErrorCode.FORMAT_NOT_AVAILABLE_IN_IMAGE,
                f"format '{fmt.value}' is recognized but not available in this image",
                params={"format": fmt.value},
            )
        return conv

    def available_formats(self) -> frozenset[DocFormat]:
        out: set[DocFormat] = set()
        for conv in self._converters:
            if conv.available():
                out |= conv.handles
        return frozenset(out)

    def info(self) -> list[dict[str, Any]]:
        """Description for ``/api/v1/info`` and ``a0td formats``."""
        return [
            {
                "id": conv.id,
                "available": conv.available(),
                "formats": sorted(f.value for f in conv.handles),
            }
            for conv in self._converters
        ]


def default_registry() -> ConverterRegistry:
    """The converters shipped in this build (F1: PDF and common raster; F2: the rest; F3: CAD,
    HPGL and DjVu)."""
    from a0td.ingest.converters.cad import DwgConverter, DxfConverter
    from a0td.ingest.converters.djvu import DjvuConverter
    from a0td.ingest.converters.heif import HeifConverter
    from a0td.ingest.converters.hpgl import HpglConverter
    from a0td.ingest.converters.office import OfficeConverter
    from a0td.ingest.converters.pdf import PdfConverter
    from a0td.ingest.converters.postscript import PostScriptConverter
    from a0td.ingest.converters.raster import RasterConverter
    from a0td.ingest.converters.svg import SvgConverter
    from a0td.ingest.converters.xps import XpsConverter

    return ConverterRegistry(
        [
            PdfConverter(),
            RasterConverter(),
            HeifConverter(),
            SvgConverter(),
            PostScriptConverter(),
            XpsConverter(),
            OfficeConverter(),
            DxfConverter(),
            DwgConverter(),
            HpglConverter(),
            DjvuConverter(),
        ]
    )
