"""JobSettings (SPEC Appendix B), printer profiles and presets.

``JobSettings`` is shared by the API, the CLI and the presets. A preset is a *partial* settings
dict that is deep-merged over the defaults.
"""

import re
from copy import deepcopy
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from a0td.domain.naming import NamingScheme, SheetOrder
from a0td.domain.papers import CUSTOM_MAX_MM, CUSTOM_MIN_MM, Orientation, PaperId

SCHEMA_VERSION = 1
MIN_SCALE = 0.01
MAX_SCALE = 10.0
MIN_OVERLAP_MM = 3.0
MAX_OVERLAP_MM = 50.0

PartialSettings = dict[str, Any]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- enums


class SourceBox(StrEnum):
    CROP = "crop"
    TRIM = "trim"
    BLEED = "bleed"
    MEDIA = "media"


class InfoBandPosition(StrEnum):
    NONE = "none"
    TOP = "top"
    BOTTOM = "bottom"


class Align(StrEnum):
    START = "start"
    CENTER = "center"
    END = "end"


class JoinMethod(StrEnum):
    OVERLAP = "overlap"
    BUTT = "butt"


class BlankSensitivity(StrEnum):
    STRICT = "strict"
    NORMAL = "normal"
    TOLERANT = "tolerant"


class TestSquare(StrEnum):
    __test__ = False  # not a pytest test class
    NONE = "none"
    ASSEMBLY_PAGE = "assembly_page"
    FIRST_SHEET = "first_sheet"


class AssemblyPage(StrEnum):
    NONE = "none"
    FIRST = "first"
    LAST = "last"


class ColorMode(StrEnum):
    COLOR = "color"
    GRAYSCALE = "grayscale"
    BW = "bw"


class OutputFormat(StrEnum):
    PDF = "pdf"
    PNG_ZIP = "png_zip"
    JPEG_ZIP = "jpeg_zip"
    TIFF_ZIP = "tiff_zip"


# ------------------------------------------------------------------------ page ranges

_RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def _parse_tokens(spec: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for raw in spec.split(","):
        token = raw.strip()
        m = _RANGE_RE.match(token)
        if not m:
            raise ValueError(f"invalid page range: {token!r}")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b < a:
            raise ValueError(f"invalid page range: {token!r}")
        out.append((a, b))
    return out


def validate_page_spec(spec: str) -> str:
    """Check the syntax of ``"all"`` or ``"1,3-5"`` (1-based). Returns the normalized spec."""
    spec = spec.strip().lower()
    if spec == "all":
        return spec
    _parse_tokens(spec)
    return re.sub(r"\s+", "", spec)


def parse_page_ranges(spec: str, page_count: int) -> list[int]:
    """Expand a page spec to a sorted list of unique 1-based page numbers.

    Raises ``ValueError`` on bad syntax or pages beyond ``page_count``.
    """
    spec = validate_page_spec(spec)
    if spec == "all":
        return list(range(1, page_count + 1))
    pages: set[int] = set()
    for a, b in _parse_tokens(spec):
        if b > page_count:
            raise ValueError(f"page {b} is out of range (document has {page_count} pages)")
        pages.update(range(a, b + 1))
    return sorted(pages)


# ----------------------------------------------------------------------------- models


class RegionMm(_Model):
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class SourceSettings(_Model):
    pages: str = "all"
    box: SourceBox = SourceBox.CROP
    rotate: Literal[0, 90, 180, 270] = 0
    mirror: bool = False
    region_mm: RegionMm | None = None
    auto_trim: bool = False
    hidden_layers: list[str] = Field(default_factory=list)  # layers explicitly turned off
    # Appendix B addition: source layers that are OFF in the file stay OFF unless listed here.
    visible_layers: list[str] = Field(default_factory=list)

    @field_validator("pages")
    @classmethod
    def _check_pages(cls, v: str) -> str:
        return validate_page_spec(v)


class Margins(_Model):
    """Physical (non-printable) margins in mm, for the paper held in portrait."""

    top: float = Field(default=5.0, ge=0, le=100)
    right: float = Field(default=5.0, ge=0, le=100)
    bottom: float = Field(default=5.0, ge=0, le=100)
    left: float = Field(default=5.0, ge=0, le=100)

    @classmethod
    def uniform(cls, mm: float) -> Self:
        return cls(top=mm, right=mm, bottom=mm, left=mm)

    def landscape_safe(self) -> Self:
        """Safe margins for a landscape sheet whose rotation direction is unknown (SPEC 5.3)."""
        lr = max(self.left, self.right)
        tb = max(self.top, self.bottom)
        return type(self)(top=lr, bottom=lr, left=tb, right=tb)

    def for_orientation(self, orientation: Orientation) -> Self:
        return self.landscape_safe() if orientation is Orientation.LANDSCAPE else self

    def rotated_cw(self) -> Self:
        """Exact margins of a portrait page seen rotated 90 degrees clockwise (SPEC 5.3).

        Portrait top -> view right, left -> top, bottom -> left, right -> bottom. Used by
        ``landscape_as_rotated_portrait``, where the rotation direction is known.
        """
        return type(self)(top=self.left, right=self.top, bottom=self.right, left=self.bottom)


class CustomPaperMm(_Model):
    width: float
    height: float

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        lo, hi = sorted((self.width, self.height))
        (min_s, min_l), (max_s, max_l) = CUSTOM_MIN_MM, CUSTOM_MAX_MM
        if not (min_s <= lo <= max_s and min_l <= hi <= max_l):
            raise ValueError(
                f"custom paper must be {min_s:g}-{max_s:g} x {min_l:g}-{max_l:g} mm "
                f"(got {self.width:g} x {self.height:g})"
            )
        return self


ROLL_MIN_WIDTH_MM = 50.0
ROLL_MAX_WIDTH_MM = 1500.0
ROLL_MIN_LENGTH_MM = 100.0
ROLL_MAX_LENGTH_MM = 30000.0


class RollPaperMm(_Model):
    """Roll or banner paper (PAP-06): fixed ``width``, sheets up to ``max_length`` long."""

    width: float = Field(ge=ROLL_MIN_WIDTH_MM, le=ROLL_MAX_WIDTH_MM)
    max_length: float = Field(ge=ROLL_MIN_LENGTH_MM, le=ROLL_MAX_LENGTH_MM)


class InfoBand(_Model):
    position: InfoBandPosition = InfoBandPosition.NONE
    height_mm: float = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.position is InfoBandPosition.NONE:
            self.height_mm = 0
        elif self.height_mm <= 0:
            raise ValueError("info band height must be > 0 when position is not 'none'")
        return self


class TargetSettings(_Model):
    printer_id: str = "builtin-generic"
    paper: PaperId = PaperId.A4
    custom_paper_mm: CustomPaperMm | None = None
    # Roll / banner paper (PAP-06). When set it replaces ``paper``; always portrait.
    roll_mm: RollPaperMm | None = None
    orientation: Orientation = Orientation.AUTO
    margins_mm: Margins | None = None
    info_band: InfoBand = Field(default_factory=InfoBand)
    landscape_as_rotated_portrait: bool = False

    @model_validator(mode="after")
    def _check_custom(self) -> Self:
        if self.paper is PaperId.CUSTOM and self.custom_paper_mm is None:
            raise ValueError("paper CUSTOM requires custom_paper_mm")
        return self


class PercentScale(_Model):
    mode: Literal["percent"] = "percent"
    percent: float = Field(default=100, ge=MIN_SCALE * 100, le=MAX_SCALE * 100)


class FitSheetScale(_Model):
    mode: Literal["fit_sheet"] = "fit_sheet"


class FitGridScale(_Model):
    mode: Literal["fit_grid"] = "fit_grid"
    cols: int = Field(ge=1, le=100)
    rows: int = Field(ge=1, le=100)


class FitCountScale(_Model):
    mode: Literal["fit_count"] = "fit_count"
    max_sheets: int = Field(ge=1, le=10000)


class FinalSizeScale(_Model):
    mode: Literal["final_size"] = "final_size"
    width_mm: float | None = Field(default=None, gt=0)
    height_mm: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.width_mm is None) == (self.height_mm is None):
            raise ValueError("final_size requires exactly one of width_mm or height_mm")
        return self


class DrawingScale(_Model):
    mode: Literal["drawing_scale"] = "drawing_scale"
    from_denominator: float = Field(gt=0)
    to_denominator: float = Field(gt=0)

    @property
    def factor(self) -> float:
        """1:a -> 1:b gives ``a / b`` (1:50 -> 1:100 = 0.5)."""
        return self.from_denominator / self.to_denominator


ScaleSettings = Annotated[
    PercentScale | FitSheetScale | FitGridScale | FitCountScale | FinalSizeScale | DrawingScale,
    Field(discriminator="mode"),
]


class Offset(_Model):
    x: float = 0
    y: float = 0


class Alignment(_Model):
    x: Align = Align.CENTER
    y: Align = Align.CENTER


class TilingSettings(_Model):
    join: JoinMethod = JoinMethod.OVERLAP
    overlap_mm: float = Field(default=10, ge=0, le=MAX_OVERLAP_MM)
    align: Alignment = Field(default_factory=Alignment)
    offset_mm: Offset = Field(default_factory=Offset)
    order: SheetOrder = SheetOrder.ROWS
    naming: NamingScheme = NamingScheme.LETTER_NUMBER
    skip_blank: bool = True
    blank_sensitivity: BlankSensitivity = BlankSensitivity.NORMAL
    only_sheets: list[str] | None = None

    @model_validator(mode="after")
    def _check_overlap(self) -> Self:
        if self.join is JoinMethod.BUTT:
            self.overlap_mm = 0
        elif not (MIN_OVERLAP_MM <= self.overlap_mm <= MAX_OVERLAP_MM):
            raise ValueError(
                f"overlap_mm must be between {MIN_OVERLAP_MM:g} and {MAX_OVERLAP_MM:g} "
                "for the overlap join"
            )
        return self


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class MarksSettings(_Model):
    cut_lines: bool = True
    outer_cut_lines: bool = False
    alignment_marks: bool = True
    join_diamonds: bool = True
    labels: bool = True
    neighbor_hints: bool = True
    ruler: bool = True
    glue_shading: bool = False
    test_square: TestSquare = TestSquare.ASSEMBLY_PAGE
    assembly_page: AssemblyPage = AssemblyPage.FIRST
    color: str = "#808080"
    line_width_pt: float = Field(default=0.5, gt=0, le=5)
    marks_layer: bool = False

    @field_validator("color")
    @classmethod
    def _check_color(cls, v: str) -> str:
        if not _HEX_COLOR.match(v):
            raise ValueError("color must be #RRGGBB")
        return v.lower()


class ColorSettings(_Model):
    mode: ColorMode = ColorMode.COLOR
    threshold: int = Field(default=160, ge=0, le=255)
    invert: bool = False
    whiten_background: bool = False
    min_line_width_mm: float | None = Field(default=None, gt=0)


class OutputSettings(_Model):
    formats: list[OutputFormat] = Field(default_factory=lambda: [OutputFormat.PDF], min_length=1)
    rasterize: bool = False
    dpi: Literal[150, 200, 300, 600] = 300
    max_dpi: int = Field(default=600, ge=72, le=1200)
    jpeg_quality: int = Field(default=90, ge=1, le=100)
    filename_template: str = Field(
        default="{name}_{paper}-{orientation}_{cols}x{rows}_{scale}pct", min_length=1
    )
    include_reduced_copy: bool = False

    @field_validator("formats")
    @classmethod
    def _unique_formats(cls, v: list[OutputFormat]) -> list[OutputFormat]:
        return list(dict.fromkeys(v))


class JobSettings(_Model):
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1, le=SCHEMA_VERSION)
    document_id: UUID | None = None
    source: SourceSettings = Field(default_factory=SourceSettings)
    target: TargetSettings = Field(default_factory=TargetSettings)
    scale: ScaleSettings = Field(default_factory=PercentScale)
    tiling: TilingSettings = Field(default_factory=TilingSettings)
    marks: MarksSettings = Field(default_factory=MarksSettings)
    color: ColorSettings = Field(default_factory=ColorSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)


# ------------------------------------------------------------------------------ merge


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge ``overlay`` over ``base`` without mutating either.

    Dicts merge recursively, except discriminated ones (they carry ``mode``), which are
    replaced wholesale so stale fields of the old variant do not leak.
    """
    out = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and "mode" not in value:
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def default_settings_dict() -> dict[str, Any]:
    return JobSettings().model_dump(mode="json")


def merge_settings(partial: PartialSettings, base: JobSettings | None = None) -> JobSettings:
    """Apply a partial settings dict over ``base`` (or the defaults) and validate."""
    base_dict = (base or JobSettings()).model_dump(mode="json")
    return JobSettings.model_validate(deep_merge(base_dict, partial))


# ------------------------------------------------------------------- printers & presets


class PrinterKind(StrEnum):
    GENERIC = "generic"
    INKJET = "inkjet"
    LASER = "laser"
    BORDERLESS = "borderless"


def _all_fixed_papers() -> list[PaperId]:
    return [p for p in PaperId if p is not PaperId.CUSTOM]


class PrinterProfile(_Model):
    id: str
    name: str
    kind: PrinterKind = PrinterKind.GENERIC
    builtin: bool = False
    supported_papers: list[PaperId] = Field(default_factory=_all_fixed_papers)
    default_margins: Margins = Field(default_factory=Margins)
    paper_margins: dict[PaperId, Margins] = Field(default_factory=dict)
    borderless: bool = False
    color: bool = True
    # Calibration: k = measured / nominal, per axis.
    scale_kx: float = Field(default=1.0, ge=0.9, le=1.1)
    scale_ky: float = Field(default=1.0, ge=0.9, le=1.1)
    ipp_uri: str | None = None
    defaults: PartialSettings = Field(default_factory=dict)

    def margins_for(self, paper: PaperId) -> Margins:
        return self.paper_margins.get(paper, self.default_margins)


class Preset(_Model):
    id: str
    name: str
    builtin: bool = False
    description: str = ""
    settings: PartialSettings = Field(default_factory=dict)

    def apply(self, base: JobSettings | None = None) -> JobSettings:
        return merge_settings(self.settings, base)


BUILTIN_PRINTERS: tuple[PrinterProfile, ...] = (
    PrinterProfile(
        id="builtin-generic",
        name="Genérica (5 mm)",
        builtin=True,
        default_margins=Margins.uniform(5),
    ),
    PrinterProfile(
        id="builtin-inkjet",
        name="Inyección típica",
        kind=PrinterKind.INKJET,
        builtin=True,
        default_margins=Margins(top=3, right=3, bottom=5, left=3),
    ),
    PrinterProfile(
        id="builtin-laser",
        name="Láser típica",
        kind=PrinterKind.LASER,
        builtin=True,
        default_margins=Margins.uniform(4.5),
    ),
    PrinterProfile(
        id="builtin-borderless",
        name="Sin bordes",
        kind=PrinterKind.BORDERLESS,
        builtin=True,
        borderless=True,
        default_margins=Margins.uniform(0),
    ),
)

_NO_MARKS: PartialSettings = {
    "marks": {
        "cut_lines": False,
        "outer_cut_lines": False,
        "alignment_marks": False,
        "join_diamonds": False,
        "labels": False,
        "neighbor_hints": False,
        "ruler": False,
        "glue_shading": False,
        "test_square": "none",
    }
}


def _real_size(preset_id: str, label: str, paper: PaperId) -> Preset:
    return Preset(
        id=preset_id,
        name=f"Tamaño real en {label}",
        builtin=True,
        settings={"target": {"paper": paper.value}},
    )


def _reduced(preset_id: str, label: str, paper: PaperId) -> Preset:
    return Preset(
        id=preset_id,
        name=f"Reducido a una {label}",
        builtin=True,
        settings={
            "target": {"paper": paper.value},
            "scale": {"mode": "fit_sheet"},
            **deep_merge(_NO_MARKS, {"marks": {"assembly_page": "none"}}),
        },
    )


BUILTIN_PRESETS: tuple[Preset, ...] = (
    _real_size("builtin-real-a4", "A4", PaperId.A4),
    _real_size("builtin-real-letter", "Carta", PaperId.LETTER),
    _real_size("builtin-real-a3", "A3", PaperId.A3),
    _reduced("builtin-fit-a4", "A4", PaperId.A4),
    _reduced("builtin-fit-a3", "A3", PaperId.A3),
    Preset(
        id="builtin-poster-2x2-a3",
        name="Póster 2×2 A3",  # noqa: RUF001
        builtin=True,
        settings={
            "target": {"paper": "A3"},
            "scale": {"mode": "fit_grid", "cols": 2, "rows": 2},
        },
    ),
    Preset(
        id="builtin-sewing-a4",
        name="Patrón de costura 1:1 (A4, a tope)",
        builtin=True,
        settings={
            "target": {"paper": "A4"},
            "tiling": {"join": "butt", "skip_blank": True},
            "marks": {"join_diamonds": True, "test_square": "first_sheet"},
        },
    ),
)


def get_builtin_printer(printer_id: str) -> PrinterProfile | None:
    return next((p for p in BUILTIN_PRINTERS if p.id == printer_id), None)


def get_builtin_preset(preset_id: str) -> Preset | None:
    return next((p for p in BUILTIN_PRESETS if p.id == preset_id), None)
