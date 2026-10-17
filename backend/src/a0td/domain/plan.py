"""The ``Plan`` model (SPEC 11.3): the single source of truth consumed by UI, CLI and render."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from a0td.domain.errors import Severity, WarningCode
from a0td.domain.papers import Orientation
from a0td.domain.settings import Margins

Vec2 = tuple[float, float]
Box4 = tuple[float, float, float, float]  # x0, y0, x1, y1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Edge(StrEnum):
    TOP = "top"
    RIGHT = "right"
    BOTTOM = "bottom"
    LEFT = "left"


class PaperRef(_Model):
    """The paper as printed: ``width_mm`` x ``height_mm`` already oriented."""

    id: str
    width_mm: float
    height_mm: float


class Grid(_Model):
    cols: int
    rows: int


class Range2(_Model):
    x: Vec2
    y: Vec2


class Neighbors(_Model):
    top: str | None = None
    right: str | None = None
    bottom: str | None = None
    left: str | None = None


class SheetPlan(_Model):
    id: str
    row: int
    col: int
    print_index: int | None  # 1-based among printed sheets; ``None`` when not printed
    src_rect_mm: Box4  # view space; may extend beyond the region (left blank)
    blank: bool
    selected: bool  # chosen by ``only_sheets`` (always true without it)
    neighbors: Neighbors
    cut_edges: list[Edge]
    receive_edges: list[Edge]


class Counts(_Model):
    total: int  # cells of the grid
    printed: int
    blank: int  # blank sheets among the selected ones
    deselected: int  # excluded by ``only_sheets``


class PlanWarning(_Model):
    code: WarningCode
    severity: Severity = Severity.WARNING
    params: dict[str, Any] = Field(default_factory=dict)


class Alternative(_Model):
    """Sheet count for the current scale on another paper / orientation (SPEC 5.11)."""

    paper_id: str
    orientation: Orientation
    cols: int
    rows: int
    total: int
    sheets: int  # printed sheets (blank ones skipped when ``skip_blank``)
    current: bool


class Saving(_Model):
    """A threshold scale below the current one that needs fewer sheets (SPEC 5.11)."""

    scale: float
    scale_label: str
    orientation: Orientation
    cols: int
    rows: int
    sheets: int
    saved: int  # sheets fewer than the current plan


class Plan(_Model):
    page_index: int
    view_size_mm: Vec2
    source_rect_mm: Box4
    orientation: Orientation  # never ``auto``
    paper: PaperRef
    margins_mm: Margins  # effective (landscape-safe when landscape)
    content_mm: Vec2
    content_origin_mm: Vec2  # where the usable rectangle starts on the sheet (incl. info band)
    overlap_mm: float
    step_mm: Vec2
    scale: float
    scale_label: str
    grid: Grid
    coverage_mm: Vec2
    origin_mm: Vec2
    offset_range_mm: Range2
    assembled_size_mm: Vec2
    effective_dpi: float | None
    sheets: list[SheetPlan]  # in print order
    counts: Counts
    alternatives: list[Alternative] | None = None
    savings: list[Saving] | None = None
    warnings: list[PlanWarning] = Field(default_factory=list)
