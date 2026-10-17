"""Render jobs: create, list, detail, events, cancel, delete, output download and page thumbs."""

import asyncio
import hashlib
import io
import os
import uuid
from typing import Annotated
from urllib.parse import quote

import pymupdf
from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy import func, select

from a0td.api.deps import Services, ServicesDep
from a0td.api.planning import compute_plan, ready_document, settings_document_id
from a0td.api.schemas import JobCreate, JobOut, OutputFile, job_out
from a0td.api.sse import event_stream, sse_response
from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.settings import JobSettings, parse_page_ranges
from a0td.jobs.exact_scale import is_exact_scale
from a0td.storage import models as m
from a0td.storage import repo
from a0td.storage.files import like_pattern, new_id, valid_id
from a0td.storage.models import loads

router = APIRouter(prefix="/api/v1", tags=["jobs"])

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
THUMB_PX = 512


def content_disposition(disposition: str, filename: str) -> str:
    """``Content-Disposition`` with an ASCII fallback and the RFC 5987 ``filename*``."""
    clean = "".join(ch for ch in filename if ch >= " " and ch != "\x7f") or "download"
    ascii_name = (
        "".join(ch if (ch.isascii() and ch not in '"\\/;%') else "_" for ch in clean).strip()
        or "download"
    )
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(clean, safe='')}"


def _get_job(svc: Services, job_id: str) -> m.Job:
    canonical = valid_id(job_id)
    with svc.db.session() as s:
        job = s.get(m.Job, canonical) if canonical else None
        if job is None:
            raise DomainError(ErrorCode.NOT_FOUND, "job not found")
        return job


# ------------------------------------------------------------------------------ create


@router.post("/jobs", status_code=202, response_model=JobOut)
def create_job(body: JobCreate, response: Response, svc: ServicesDep) -> JobOut:
    """Validate the settings against the document and queue a render job."""
    doc, pages = ready_document(svc, settings_document_id(body.settings))
    settings = body.settings
    if body.outputs:
        data = settings.model_dump(mode="json")
        data["output"]["formats"] = [f.value for f in body.outputs]
        settings = JobSettings.model_validate(data)
    try:
        selected = parse_page_ranges(settings.source.pages, len(pages))
    except ValueError as exc:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            str(exc),
            errors=[{"field": "settings.source.pages", "message": str(exc), "type": "value"}],
        ) from exc
    if not selected:
        raise DomainError(ErrorCode.INVALID_SETTINGS, "no pages selected")
    printed = 0
    for page_number in selected:  # fail now, not in the worker: bad overlap, scale, too many sheets
        plan = compute_plan(svc, doc, pages, settings, page_number - 1, preset_id=body.preset_id)
        printed += plan.counts.printed
    if not body.test_sheet_only and printed > svc.config.max_sheets:
        raise DomainError(
            ErrorCode.TOO_MANY_SHEETS,
            f"the job would print {printed} sheets (limit {svc.config.max_sheets})",
            params={"sheets": printed, "max": svc.config.max_sheets},
        )
    job_id = new_id()
    with svc.db.session() as s:
        repo.create_job(
            s,
            svc.config,
            job_id=job_id,
            document_id=doc.id,
            kind=m.KIND_RENDER,
            settings=settings.model_dump(mode="json"),
            options={
                "test_sheet_only": body.test_sheet_only,
                "exact_scale": is_exact_scale(settings, body.preset_id),
                "preset_id": body.preset_id,
            },
        )
    svc.manager.enqueue(job_id)
    response.headers["Location"] = f"/api/v1/jobs/{job_id}"
    return job_out(_get_job(svc, job_id))


# -------------------------------------------------------------------------------- read


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    svc: ServicesDep,
    response: Response,
    kind: Annotated[str, Query(pattern="^(render|ingest|print|all)$")] = "render",
    document_id: str | None = None,
    status: Annotated[
        str | None, Query(pattern="^(queued|running|succeeded|failed|cancelled)$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[JobOut]:
    """Recent jobs, newest first (render jobs unless ``kind`` says otherwise).

    ``q`` matches the name of the job's document.
    """
    stmt = select(m.Job)
    if kind != "all":
        stmt = stmt.where(m.Job.kind == kind)
    if document_id is not None:
        stmt = stmt.where(m.Job.document_id == (valid_id(document_id) or document_id))
    if status is not None:
        stmt = stmt.where(m.Job.status == status)
    if q and q.strip():
        stmt = stmt.join(m.Document, m.Document.id == m.Job.document_id).where(
            m.Document.name.ilike(like_pattern(q), escape="\\")
        )
    with svc.db.session() as s:
        total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        response.headers["X-Total-Count"] = str(total)
        page = stmt.order_by(m.Job.created_at.desc()).limit(limit).offset(offset)
        return [job_out(j, detail=False) for j in s.scalars(page)]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, svc: ServicesDep) -> JobOut:
    return job_out(_get_job(svc, job_id))


@router.get(
    "/jobs/{job_id}/events",
    response_class=Response,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def job_events(job_id: str, svc: ServicesDep) -> Response:
    """SSE: ``progress`` events, then one ``done`` or ``error``."""
    job = _get_job(svc, job_id)
    return sse_response(event_stream(svc, job.id, "job"))


# ------------------------------------------------------------------------ cancel/delete


@router.post("/jobs/{job_id}/cancel", status_code=202, response_model=JobOut)
async def cancel_job(job_id: str, svc: ServicesDep) -> JobOut:
    """Cancel a queued or running job (idempotent: finished jobs are returned unchanged)."""
    job = await asyncio.to_thread(_get_job, svc, job_id)
    await svc.manager.cancel(job.id)
    return await asyncio.to_thread(lambda: job_out(_get_job(svc, job.id)))


def _delete_job_rows(svc: Services, job_id: str) -> None:
    """Blocking part of ``DELETE /jobs/{id}``: the row and the job directory."""
    with svc.db.session() as s:
        row = s.get(m.Job, job_id)
        if row is not None:
            s.delete(row)
    svc.files.remove_job(job_id)


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str, svc: ServicesDep) -> Response:
    job = await asyncio.to_thread(_get_job, svc, job_id)
    if job.status in m.ACTIVE_JOB_STATUSES:
        await svc.manager.cancel(job.id, wait=True)
    await asyncio.to_thread(_delete_job_rows, svc, job.id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------- outputs


def _output(svc: Services, job_id: str, name: str) -> tuple[m.Job, OutputFile]:
    job = _get_job(svc, job_id)
    if job.status != m.JOB_SUCCEEDED:
        raise DomainError(
            ErrorCode.DOCUMENT_NOT_READY, f"job is {job.status}", params={"status": job.status}
        )
    for raw in loads(job.outputs_json, []) or []:
        out = OutputFile.model_validate(raw)
        if out.name == name:
            return job, out
    raise DomainError(ErrorCode.NOT_FOUND, "output not found")


@router.get(
    "/jobs/{job_id}/outputs/{name}",
    response_class=FileResponse,
    responses={200: {"content": {"application/pdf": {}, "application/zip": {}}}},
)
def download_output(
    job_id: str, name: str, svc: ServicesDep, inline: Annotated[int, Query(ge=0, le=1)] = 0
) -> FileResponse:
    """Download an output file; ``?inline=1`` lets the browser show a PDF in its viewer."""
    job, out = _output(svc, job_id, name)
    path = svc.files.output_path(job.id, out.name)
    if not path.is_file():
        raise DomainError(ErrorCode.NOT_FOUND, "output file is gone")
    media = MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    show_inline = bool(inline) and media == "application/pdf"
    return FileResponse(
        path,
        media_type=media,
        headers={
            "Content-Disposition": content_disposition(
                "inline" if show_inline else "attachment", out.name
            ),
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get(
    "/jobs/{job_id}/outputs/{name}/pages/{n}/thumb",
    response_class=Response,
    responses={200: {"content": {"image/webp": {}}}},
)
def output_page_thumb(job_id: str, name: str, n: int, svc: ServicesDep) -> Response:
    """WebP thumbnail of page ``n`` (0-based) of an output PDF, cached next to the job."""
    job, out = _output(svc, job_id, name)
    path = svc.files.output_path(job.id, out.name)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise DomainError(ErrorCode.NOT_FOUND, "output has no pages to preview")
    key = hashlib.sha1(out.name.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    cache = svc.files.thumbs_dir(job.id) / f"{key}-{int(n)}.webp"
    headers = {"Cache-Control": "private, max-age=3600"}
    if cache.is_file():
        return FileResponse(cache, media_type="image/webp", headers=headers)
    try:
        with pymupdf.open(path) as pdf:
            if not 0 <= n < pdf.page_count:
                raise DomainError(ErrorCode.NOT_FOUND, f"page {n} does not exist")
            page = pdf.load_page(n)
            zoom = THUMB_PX / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False
            )
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(ErrorCode.CONVERSION_FAILED, "cannot render the page") from exc
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    tmp_cache = cache.with_name(f".{cache.name}.{uuid.uuid4().hex}.tmp")
    try:  # atomic: a concurrent GET never sees a half-written file
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp_cache.write_bytes(buf.getvalue())
        os.replace(tmp_cache, cache)
    except OSError:
        tmp_cache.unlink(missing_ok=True)
    return Response(buf.getvalue(), media_type="image/webp", headers=headers)
