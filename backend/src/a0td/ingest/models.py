"""Shared ingest types: detected formats, the ``Page`` model (SPEC 11.1) and the ingest result."""

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from a0td.domain.page import Box, PageGeometry
from a0td.domain.papers import Orientation
from a0td.domain.plan import PlanWarning
from a0td.domain.settings import SourceBox

META_FILENAME = "meta.json"
CANONICAL_PDF = "canonical.pdf"
SCHEMA_VERSION = 1


class DocFormat(StrEnum):
    """Stable format ids returned by detection (SPEC Appendix E). Also the ids in ``handles``."""

    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    TIFF = "tiff"
    GIF = "gif"
    BMP = "bmp"
    WEBP = "webp"
    HEIF = "heif"
    AVIF = "avif"
    JP2 = "jp2"
    PS = "ps"
    EPS = "eps"
    AI = "ai"
    SVG = "svg"
    SVGZ = "svgz"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    VSDX = "vsdx"
    ODT = "odt"
    ODS = "ods"
    ODP = "odp"
    ODG = "odg"
    DOC = "doc"
    XLS = "xls"
    PPT = "ppt"
    VSD = "vsd"
    PUB = "pub"
    RTF = "rtf"
    EMF = "emf"
    WMF = "wmf"
    XPS = "xps"
    DWG = "dwg"
    DXF = "dxf"
    CDR = "cdr"
    HPGL = "hpgl"
    DJVU = "djvu"


MIME_TYPES: dict[DocFormat, str] = {
    DocFormat.PDF: "application/pdf",
    DocFormat.PNG: "image/png",
    DocFormat.JPEG: "image/jpeg",
    DocFormat.TIFF: "image/tiff",
    DocFormat.GIF: "image/gif",
    DocFormat.BMP: "image/bmp",
    DocFormat.WEBP: "image/webp",
    DocFormat.HEIF: "image/heif",
    DocFormat.AVIF: "image/avif",
    DocFormat.JP2: "image/jp2",
    DocFormat.PS: "application/postscript",
    DocFormat.EPS: "application/postscript",
    DocFormat.AI: "application/illustrator",
    DocFormat.SVG: "image/svg+xml",
    DocFormat.SVGZ: "image/svg+xml",
    DocFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    DocFormat.PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    DocFormat.VSDX: "application/vnd.ms-visio.drawing",
    DocFormat.ODT: "application/vnd.oasis.opendocument.text",
    DocFormat.ODS: "application/vnd.oasis.opendocument.spreadsheet",
    DocFormat.ODP: "application/vnd.oasis.opendocument.presentation",
    DocFormat.ODG: "application/vnd.oasis.opendocument.graphics",
    DocFormat.DOC: "application/msword",
    DocFormat.XLS: "application/vnd.ms-excel",
    DocFormat.PPT: "application/vnd.ms-powerpoint",
    DocFormat.VSD: "application/vnd.visio",
    DocFormat.PUB: "application/vnd.ms-publisher",
    DocFormat.RTF: "application/rtf",
    DocFormat.EMF: "image/emf",
    DocFormat.WMF: "image/wmf",
    DocFormat.XPS: "application/oxps",
    DocFormat.DWG: "image/vnd.dwg",
    DocFormat.DXF: "image/vnd.dxf",
    DocFormat.CDR: "application/vnd.corel-draw",
    DocFormat.HPGL: "application/vnd.hp-hpgl",
    DocFormat.DJVU: "image/vnd.djvu",
}


class PageKind(StrEnum):
    VECTOR = "vector"
    RASTER = "raster"
    MIXED = "mixed"


class DpiSource(StrEnum):
    METADATA = "metadata"
    ASSUMED = "assumed"
    USER = "user"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StandardMatch(_Model):
    """Nearest standard source size (ANA-01), e.g. ``A0`` portrait."""

    id: str
    orientation: Orientation
    delta_mm: float


class PxSize(_Model):
    w: int
    h: int


class Layer(_Model):
    xref: int
    name: str
    on: bool


class CadColor(StrEnum):
    """Colour policy of CAD drawings (SPEC 7.1): keep colours, force black, or grayscale."""

    COLOR = "color"
    BLACK = "black"
    GRAY = "gray"


class CadOptions(_Model):
    """User choices for DXF / DWG (SPEC 7.1). ``None`` means automatic."""

    layout: str | None = Field(default=None, max_length=256)  # layout name ("Model", "Layout1"...)
    units: str | None = None  # model-space drawing units (override of $INSUNITS), e.g. "mm", "in"
    scale: float | None = Field(default=None, gt=0, le=1_000_000)  # N of 1:N (model space only)
    color: CadColor = CadColor.COLOR

    @field_validator("units")
    @classmethod
    def _known_units(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        from a0td.domain.errors import DomainError
        from a0td.ingest.converters.cad import normalize_unit

        try:
            return normalize_unit(value)
        except DomainError as exc:
            raise ValueError(exc.detail) from exc


class CadLayout(_Model):
    name: str
    kind: Literal["model", "paper"]
    width_mm: float | None = None  # paper size declared by the layout (paper space)
    height_mm: float | None = None


class CadInfo(_Model):
    """What the CAD converter found and chose, stored in ``meta.json`` for the options dialog."""

    layouts: list[CadLayout]
    layout: str  # layout that was rendered
    units: str  # model-space units used
    units_source: Literal["insunits", "assumed", "user"]
    scale: float | None = None  # N of 1:N for model space (None for paper space)
    scale_auto: bool = False
    color: CadColor = CadColor.COLOR


class Page(_Model):
    """One canonical page (SPEC 11.1). Boxes use the canonical frame (origin = CropBox corner)."""

    index: int = Field(ge=0)
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    boxes_mm: dict[SourceBox, Box]
    kind: PageKind
    standard_match: StandardMatch | None = None
    px: PxSize | None = None
    dpi: float | None = None
    dpi_source: DpiSource | None = None
    layers: list[Layer] = Field(default_factory=list)
    has_text: bool = False
    # ANA-08 legibility stats (mm at the canonical size) and ANA-09 detected drawing scale (1:N).
    font_min_mm: float | None = None
    font_p10_mm: float | None = None
    line_min_mm: float | None = None
    detected_scale: int | None = None
    stats_incomplete: bool = False  # stats skipped: page too heavy or out of time budget
    warnings: list[PlanWarning] = Field(default_factory=list)

    def has_warning(self, code: str) -> bool:
        return any(w.code == code for w in self.warnings)

    def to_geometry(self, page_count: int = 1) -> PageGeometry:
        """The planner's view of this page."""
        from a0td.domain.errors import WarningCode

        return PageGeometry(
            index=self.index,
            page_count=max(1, page_count),
            width_mm=self.width_mm,
            height_mm=self.height_mm,
            boxes_mm=dict(self.boxes_mm),
            px=(self.px.w, self.px.h) if self.px else None,
            physical_size_assumed=self.has_warning(WarningCode.PHYSICAL_SIZE_ASSUMED),
            font_min_mm=self.font_min_mm,
            font_p10_mm=self.font_p10_mm,
            line_min_mm=self.line_min_mm,
        )


@dataclass(frozen=True, slots=True)
class Progress:
    """Progress event, shaped like the worker's JSON line (SPEC 9.5)."""

    stage: str
    pct: float
    message_key: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[Progress], None]


@dataclass(slots=True)
class CanonicalPage:
    """A page as produced by canonicalization, before analysis.

    Raster pages carry no physical size yet: ``analyze`` resolves it from ``dpi_meta``.
    """

    index: int
    boxes_mm: dict[SourceBox, Box] = field(default_factory=dict)
    width_mm: float | None = None
    height_mm: float | None = None
    px: tuple[int, int] | None = None
    dpi_meta: tuple[float, float] | None = None  # (x, y) DPI
    file: Path | None = None
    # Vector page without a real physical size (SVG without mm/in units): ``PHYSICAL_SIZE_ASSUMED``.
    size_assumed: bool = False


@dataclass(slots=True)
class CanonicalDocument:
    """Result of a converter (SPEC 7.3)."""

    kind: Literal["pdf", "raster"]
    source_format: DocFormat
    pages: list[CanonicalPage]
    pdf_path: Path | None = None
    warnings: list[PlanWarning] = field(default_factory=list)
    cad: CadInfo | None = None


class IngestResult(_Model):
    """What ``ingest_document`` produced. Paths are relative to the document directory."""

    schema_version: int = SCHEMA_VERSION
    format: DocFormat
    mime: str
    kind: Literal["pdf", "raster"]
    canonical_pdf: str | None = None
    page_files: list[str] = Field(default_factory=list)
    pages: list[Page]
    warnings: list[PlanWarning] = Field(default_factory=list)
    cad: CadInfo | None = None

    def save(self, workdir: Path) -> Path:
        path = workdir / META_FILENAME
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(
                json.dumps(self.model_dump(mode="json"), indent=1, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    @classmethod
    def load(cls, workdir: Path) -> "IngestResult":
        return cls.model_validate_json((workdir / META_FILENAME).read_text(encoding="utf-8"))


def page_dir(workdir: Path, index: int) -> Path:
    """``pages/{n}/`` of a document directory (``n`` = 0-based page index)."""
    return workdir / "pages" / str(index)
