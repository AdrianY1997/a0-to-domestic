"""Pure request/response models shared by the HTTP API (SPEC 10) and the web bridge (SPEC 17bis).

No FastAPI and no SQLAlchemy here, so the in-browser build can use them. The HTTP layer
(:mod:`a0td.api.schemas`) re-exports them and adds the database row conversions.
"""

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from a0td.domain.plan import Plan, PlanWarning
from a0td.domain.settings import JobSettings
from a0td.ingest.models import Page


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PhysicalSizeBody(_In):
    """Exactly one of the four ways of fixing the physical size of a raster page (ANA-03)."""

    paper: str | None = None
    width_mm: float | None = Field(default=None, gt=0)
    height_mm: float | None = Field(default=None, gt=0)
    dpi: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        given = [v for v in (self.paper, self.width_mm, self.height_mm, self.dpi) if v is not None]
        if len(given) != 1:
            raise ValueError("exactly one of paper, width_mm, height_mm or dpi is required")
        return self


class PlanRequest(_In):
    settings: JobSettings
    page_index: int = Field(default=0, ge=0)
    include_alternatives: bool = False
    # Preset the settings came from (pattern/plan presets keep the scale exact).
    preset_id: str | None = None


class SheetPreviewRequest(_In):
    settings: JobSettings
    page_index: int = Field(default=0, ge=0)
    sheet_id: str = Field(min_length=1, max_length=32)
    max_px: int = Field(default=800, ge=64, le=8192)


class DocumentOut(BaseModel):
    id: str
    name: str
    format: str | None
    mime: str | None
    size_bytes: int
    sha256: str
    status: str
    error_code: str | None
    error_detail: str | None
    error_params: dict[str, Any]
    pages: list[Page]
    page_count: int
    warnings: list[PlanWarning]
    job_id: str | None  # latest ingest job (its events are the document's events)
    reused: bool = False  # ING-10: an identical ready document was returned instead
    created_at: datetime
    expires_at: datetime


class OutputFile(BaseModel):
    name: str
    kind: str
    size_bytes: int
    pages: int | None = None


class JobOut(BaseModel):
    id: str
    document_id: str
    kind: str
    status: str
    stage: str | None
    progress: float
    settings: JobSettings | None
    test_sheet_only: bool
    # kind "print" only: where it went and what the printer said (IMP-03)
    printing: dict[str, Any] | None = None
    ink: dict[str, Any] | None = None  # COL-07 ink estimate (render jobs)
    plans: list[Plan] | None  # only in the detail view
    outputs: list[OutputFile]
    warnings: list[dict[str, Any]]
    error_code: str | None
    error_detail: str | None
    error_params: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    expires_at: datetime
