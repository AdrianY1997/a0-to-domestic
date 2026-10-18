"""Request/response models of the HTTP API (SPEC 10) and row -> JSON conversion."""

from pydantic import BaseModel, Field

from a0td.domain.plan import Plan, PlanWarning
from a0td.domain.settings import JobSettings, OutputFormat
from a0td.ingest.models import Page
from a0td.io_models import (
    DocumentOut,
    JobOut,
    OutputFile,
    PhysicalSizeBody,
    PlanRequest,
    SheetPreviewRequest,
    _In,
)
from a0td.storage import models as m
from a0td.storage.models import loads

__all__ = [
    "DocumentOut",
    "IppCapabilitiesRequest",
    "JobCreate",
    "JobOut",
    "OutputFile",
    "PasswordBody",
    "PhysicalSizeBody",
    "PlanRequest",
    "PrintRequest",
    "PurgeOut",
    "PurgeRequest",
    "SheetPreviewRequest",
    "document_out",
    "job_out",
]


# --------------------------------------------------------------------------- requests


class PasswordBody(_In):
    password: str = Field(min_length=0, max_length=1024)


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
