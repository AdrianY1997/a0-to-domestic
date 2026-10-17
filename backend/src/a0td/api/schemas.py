"""Request/response models of the HTTP API (SPEC 10) and row -> JSON conversion."""

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from a0td.domain.plan import Plan, PlanWarning
from a0td.domain.settings import JobSettings, OutputFormat
from a0td.ingest.models import Page
from a0td.storage import models as m
from a0td.storage.models import loads


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- requests


class PasswordBody(_In):
    password: str = Field(min_length=0, max_length=1024)


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


class PrintRequest(_In):
    """``POST /jobs/{id}/print``: send a finished render job to an IPP printer profile."""

    printer_id: str = Field(min_length=1, max_length=64)
    copies: int = Field(default=1, ge=1, le=99)
    # Sheet ids ("A1", "B3"); omitted = every page of the PDF (assembly plan included).
    sheets: list[str] | None = Field(default=None, min_length=1, max_length=2000)


class IppCapabilitiesRequest(_In):
    uri: str = Field(min_length=1, max_length=2000)


class JobCreate(_In):
    settings: JobSettings
    outputs: list[OutputFormat] | None = Field(
        default=None, min_length=1
    )  # overrides settings.output.formats
    test_sheet_only: bool = False
    preset_id: str | None = None


class PurgeRequest(_In):
    scope: str = Field(default="expired", pattern="^(expired|all)$")


# -------------------------------------------------------------------------- responses


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


class PurgeOut(BaseModel):
    documents: int
    jobs: int
    orphans: int


def document_out(doc: m.Document, job_id: str | None = None) -> DocumentOut:
    pages = [Page.model_validate(p) for p in loads(doc.pages_json, []) or []]
    return DocumentOut(
        id=doc.id,
        name=doc.name,
        format=doc.format,
        mime=doc.mime,
        size_bytes=doc.size_bytes,
        sha256=doc.sha256,
        status=doc.status,
        error_code=doc.error_code,
        error_detail=doc.error_detail,
        error_params=loads(doc.error_params_json, {}) or {},
        pages=pages,
        page_count=len(pages),
        warnings=[PlanWarning.model_validate(w) for w in loads(doc.warnings_json, []) or []],
        job_id=job_id,
        created_at=doc.created_at,
        expires_at=doc.expires_at,
    )


_PRINT_FIELDS = (
    "render_job_id",
    "printer_id",
    "copies",
    "pages",
    "printer_job_id",
    "printer_job_state",
)


def job_out(job: m.Job, *, detail: bool = True) -> JobOut:
    settings_raw = loads(job.settings_json)
    options = loads(job.options_json, {}) or {}
    printing = {k: options.get(k) for k in _PRINT_FIELDS} if job.kind == m.KIND_PRINT else None
    plans = loads(job.plans_json)
    return JobOut(
        id=job.id,
        document_id=job.document_id,
        kind=job.kind,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        settings=JobSettings.model_validate(settings_raw) if settings_raw else None,
        test_sheet_only=bool(options.get("test_sheet_only")),
        printing=printing,
        ink=options.get("ink"),
        plans=[Plan.model_validate(p) for p in plans] if detail and plans is not None else None,
        outputs=[OutputFile.model_validate(o) for o in loads(job.outputs_json, []) or []],
        warnings=loads(job.warnings_json, []) or [],
        error_code=job.error_code,
        error_detail=job.error_detail,
        error_params=loads(job.error_params_json, {}) or {},
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        expires_at=job.expires_at,
    )
