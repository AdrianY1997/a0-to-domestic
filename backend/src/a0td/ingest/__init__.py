"""Ingest (SPEC 6.1, 6.2, 7): detect -> convert -> canonical -> analyze -> occupancy -> preview.

Public API: :func:`ingest_document` (the whole pipeline), :func:`set_physical_size` (user
override of a raster's physical size), :func:`detect_format`, the converter registry and the
``Page`` model.
"""

from a0td.ingest.analyze import apply_physical_size
from a0td.ingest.converters.registry import ConverterRegistry, default_registry
from a0td.ingest.detect import Detection, detect, detect_format, detect_or_raise
from a0td.ingest.models import (
    CadColor,
    CadInfo,
    CadLayout,
    CadOptions,
    DocFormat,
    DpiSource,
    IngestResult,
    Page,
    PageKind,
    Progress,
    ProgressCallback,
    page_dir,
)
from a0td.ingest.occupancy import PixelOccupancy, load_occupancy
from a0td.ingest.pipeline import SizeOverride, ingest_document, set_physical_size

__all__ = [
    "CadColor",
    "CadInfo",
    "CadLayout",
    "CadOptions",
    "ConverterRegistry",
    "Detection",
    "DocFormat",
    "DpiSource",
    "IngestResult",
    "Page",
    "PageKind",
    "PixelOccupancy",
    "Progress",
    "ProgressCallback",
    "SizeOverride",
    "apply_physical_size",
    "default_registry",
    "detect",
    "detect_format",
    "detect_or_raise",
    "ingest_document",
    "load_occupancy",
    "page_dir",
    "set_physical_size",
]
