"""Documents: streaming upload, password retry, physical size, previews, events, delete."""

import asyncio
import hashlib
import io
import json
import shutil
import threading
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import ValidationError
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import func, select, update

from a0td.api.deps import Services, ServicesDep
from a0td.api.planning import get_document_row, ready_document
from a0td.api.schemas import DocumentOut, PasswordBody, PhysicalSizeBody, document_out
from a0td.api.sse import event_stream, sse_response
from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest import CadInfo, CadOptions, IngestResult, set_physical_size
from a0td.ingest.models import Page
from a0td.ingest.preview import PREVIEW_FILE, THUMB_FILE
from a0td.render.layers import layered_preview_webp
from a0td.storage import models as m
from a0td.storage import repo
from a0td.storage.files import display_name, like_pattern, new_id, safe_ext
from a0td.storage.models import dumps, utcnow

router = APIRouter(prefix="/api/v1", tags=["documents"])

_OVERHEAD_BYTES = 1024 * 1024  # multipart framing and small fields on top of the file size
_MAX_PASSWORD_BYTES = 4096
_MAX_CAD_FIELD_BYTES = 512
CAD_EXTENSIONS = ("dxf", "dwg")
CAD_FIELDS = frozenset({"cad_layout", "cad_units", "cad_scale", "cad_color"})
_PIXEL_LOCK = threading.Lock()  # one physical-size change at a time (it rewrites meta.json)

_UPLOAD_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "password": {"type": "string"},
                        "cad_layout": {"type": "string"},
                        "cad_units": {"type": "string"},
                        "cad_scale": {"type": "number", "exclusiveMinimum": 0},
                        "cad_color": {"type": "string", "enum": ["color", "black", "gray"]},
                    },
                    "required": ["file"],
                }
            }
        },
    }
}


# ------------------------------------------------------------------------------ upload


class _Upload:
    """State of one streaming multipart parse: the file goes straight to disk."""

    def __init__(self, svc: Services, doc_id: str, max_bytes: int) -> None:
        self.svc = svc
        self.doc_id = doc_id
        self.max_bytes = max_bytes
        self.sha = hashlib.sha256()
        self.size = 0
        self.raw_name: str | None = None
        self.password = bytearray()
        self.cad_fields: dict[str, bytearray] = {}  # DXF / DWG options sent as form fields
        self.has_file = False
        self._fh: io.BufferedWriter | None = None
        self._mode: str | None = None  # "file", "password" or None (ignored part)
        self._hf = bytearray()
        self._hv = bytearray()
        self._headers: dict[bytes, bytes] = {}

    # parser callbacks
    def on_part_begin(self) -> None:
        self._mode = None
        self._headers = {}
        self._hf = bytearray()
        self._hv = bytearray()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._hf += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._hv += data[start:end]

    def on_header_end(self) -> None:
        self._headers[bytes(self._hf).lower()] = bytes(self._hv)
        self._hf = bytearray()
        self._hv = bytearray()

    def on_headers_finished(self) -> None:
        _, opts = parse_options_header(self._headers.get(b"content-disposition", b""))
        name = opts.get(b"name", b"")
        if name == b"file" and not self.has_file:
            self.has_file = True
            self._mode = "file"
            filename = opts.get(b"filename")
            self.raw_name = filename.decode("utf-8", "replace") if filename else None
            path = self.svc.files.original_path(self.doc_id, safe_ext(self.raw_name))
            self._fh = path.open("wb")
        elif name == b"password":
            self._mode = "password"
        elif name.decode("ascii", "replace") in CAD_FIELDS:
            self._mode = name.decode("ascii")
            self.cad_fields.setdefault(self._mode, bytearray())

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if self._mode == "file" and self._fh is not None:
            self.size += len(chunk)
            if self.size > self.max_bytes:
                raise too_large(self.svc.config.max_upload_mb)
            self.sha.update(chunk)
            self._fh.write(chunk)
        elif self._mode == "password":
            if len(self.password) + len(chunk) > _MAX_PASSWORD_BYTES:
                raise DomainError(ErrorCode.INVALID_SETTINGS, "password is too long")
            self.password += chunk
        elif self._mode in CAD_FIELDS:
            field = self.cad_fields[self._mode]
            if len(field) + len(chunk) > _MAX_CAD_FIELD_BYTES:
                raise DomainError(ErrorCode.INVALID_SETTINGS, f"{self._mode} is too long")
            field += chunk

    def cad_options(self) -> CadOptions | None:
        """The CAD options sent with the upload (``None`` when there are none)."""
        values = {k: bytes(v).decode().strip() for k, v in self.cad_fields.items()}
        raw: dict[str, Any] = {k.removeprefix("cad_"): v for k, v in values.items() if v}
        if not raw:
            return None
        try:
            return CadOptions.model_validate(raw)
        except ValidationError as exc:
            raise DomainError(
                ErrorCode.INVALID_SETTINGS,
                "invalid CAD options",
                errors=[
                    {
                        "field": "cad_" + ".".join(map(str, e["loc"])),
                        "message": e["msg"],
                        "type": e["type"],
                    }
                    for e in exc.errors()
                ],
            ) from exc

    def on_part_end(self) -> None:
        self.close()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def too_large(max_mb: int) -> DomainError:
    return DomainError(
        ErrorCode.FILE_TOO_LARGE,
        f"the file exceeds the {max_mb} MB limit",
        params={"max_mb": max_mb},
    )


async def _receive_upload(request: Request, svc: Services, doc_id: str) -> _Upload:
    max_bytes = svc.config.max_upload_mb * 1024 * 1024
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes + _OVERHEAD_BYTES:
        raise too_large(svc.config.max_upload_mb)
    ctype, opts = parse_options_header(request.headers.get("content-type", "").encode("latin-1"))
    boundary = opts.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            "expected multipart/form-data",
            errors=[{"field": "file", "message": "multipart/form-data required", "type": "type"}],
        )
    up = _Upload(svc, doc_id, max_bytes)
    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": up.on_part_begin,
            "on_header_field": up.on_header_field,
            "on_header_value": up.on_header_value,
            "on_header_end": up.on_header_end,
            "on_headers_finished": up.on_headers_finished,
            "on_part_data": up.on_part_data,
            "on_part_end": up.on_part_end,
        },
    )
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > max_bytes + _OVERHEAD_BYTES:
                raise too_large(svc.config.max_upload_mb)
            parser.write(chunk)
        parser.finalize()
    finally:
        up.close()
    if not up.has_file:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            "the 'file' part is missing",
            errors=[{"field": "file", "message": "field required", "type": "missing"}],
        )
    return up


@router.post(
    "/documents",
    status_code=202,
    response_model=DocumentOut,
    openapi_extra=_UPLOAD_BODY,
    summary="Upload a document (multipart: file, optional password)",
)
async def upload_document(request: Request, response: Response, svc: ServicesDep) -> DocumentOut:
    doc_id = new_id()
    svc.files.ensure_dirs()
    svc.files.doc_dir(doc_id).mkdir(parents=True)
    try:
        up = await _receive_upload(request, svc, doc_id)
        cad = up.cad_options()
        name = display_name(up.raw_name)
        (svc.files.doc_dir(doc_id) / "document.json").write_text(
            json.dumps({"id": doc_id, "name": name}, ensure_ascii=False), encoding="utf-8"
        )
        job_id = new_id()
        with svc.db.session() as s:
            repo.create_document(
                s,
                svc.config,
                doc_id=doc_id,
                name=name,
                size_bytes=up.size,
                sha256=up.sha.hexdigest(),
            ).was_encrypted = bool(up.password)
            s.flush()
            repo.create_job(
                s,
                svc.config,
                job_id=job_id,
                document_id=doc_id,
                kind=m.KIND_INGEST,
                options=_cad_job_options(cad),
            )
    except BaseException:
        shutil.rmtree(svc.files.doc_dir(doc_id), ignore_errors=True)
        raise
    password = up.password.decode("utf-8", "replace") if up.password else None
    if not password and cad is None and safe_ext(up.raw_name) not in CAD_EXTENSIONS:
        existing_id = await asyncio.to_thread(_reuse_document, svc, doc_id, up.sha.hexdigest())
        if existing_id is not None:
            response.headers["Location"] = f"/api/v1/documents/{existing_id}"
            out = _document_response(svc, existing_id)
            out.reused = True
            return out
    svc.manager.enqueue(job_id, password=password)
    response.headers["Location"] = f"/api/v1/documents/{doc_id}"
    return _document_response(svc, doc_id)


def _cad_job_options(cad: CadOptions | None) -> dict[str, object] | None:
    return {"cad": cad.model_dump(mode="json")} if cad is not None else None


def _reuse_document(svc: Services, new_id_: str, sha256: str) -> str | None:
    """ING-10: drop the fresh upload and return a ready, unexpired, never-encrypted twin."""
    now = utcnow()
    with svc.db.session() as s:
        existing = s.scalars(
            select(m.Document)
            .where(
                m.Document.sha256 == sha256,
                m.Document.status == m.DOC_READY,
                m.Document.was_encrypted.is_(False),
                # DXF / DWG are never shared: a twin may have been re-ingested with other options.
                m.Document.format.not_in(CAD_EXTENSIONS),
                m.Document.expires_at > now,
                m.Document.id != new_id_,
            )
            .order_by(m.Document.created_at.desc())
            .limit(1)
        ).first()
        if existing is None or not (svc.files.doc_dir(existing.id) / "meta.json").is_file():
            return None
        existing.expires_at = max(
            existing.expires_at, now + timedelta(hours=svc.config.retention_hours)
        )
        existing_id = existing.id
        # The new row is not committed yet, so only its files and rows need cleaning.
        fresh = s.get(m.Document, new_id_)
        if fresh is not None:
            s.delete(fresh)
    shutil.rmtree(svc.files.doc_dir(new_id_), ignore_errors=True)
    return existing_id


def _document_response(svc: Services, doc_id: str) -> DocumentOut:
    with svc.db.session() as s:
        doc = s.get(m.Document, doc_id)
        if doc is None:
            raise DomainError(ErrorCode.NOT_FOUND, "document not found")
        job = repo.latest_ingest_job(s, doc_id)
        return document_out(doc, job.id if job else None)


# --------------------------------------------------------------------------------- read


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    svc: ServicesDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[DocumentOut]:
    """Recent documents, newest first; ``q`` matches the name (case-insensitive substring)."""
    stmt = select(m.Document)
    if q and q.strip():
        stmt = stmt.where(m.Document.name.ilike(like_pattern(q), escape="\\"))
    with svc.db.session() as s:
        total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        response.headers["X-Total-Count"] = str(total)
        docs = s.scalars(
            stmt.order_by(m.Document.created_at.desc()).limit(limit).offset(offset)
        ).all()
        out = []
        for doc in docs:
            job = repo.latest_ingest_job(s, doc.id)
            out.append(document_out(doc, job.id if job else None))
        return out


@router.get("/documents/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: str, svc: ServicesDep) -> DocumentOut:
    get_document_row(svc, doc_id)
    return _document_response(svc, doc_id)


@router.post("/documents/{doc_id}/password", status_code=202, response_model=DocumentOut)
def submit_password(doc_id: str, body: PasswordBody, svc: ServicesDep) -> DocumentOut:
    """Retry the ingest with a PDF password. A wrong one leaves the document waiting."""
    doc = get_document_row(svc, doc_id)
    if doc.status != m.DOC_PASSWORD_REQUIRED:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            "the document is not waiting for a password",
            params={"status": doc.status},
        )
    job_id = new_id()
    with svc.db.session() as s:
        # Atomic claim: only one of several concurrent retries sees the status still waiting.
        claimed = s.execute(
            update(m.Document)
            .where(m.Document.id == doc.id, m.Document.status == m.DOC_PASSWORD_REQUIRED)
            .values(
                status=m.DOC_PROCESSING,
                was_encrypted=True,
                error_code=None,
                error_detail=None,
                error_params_json=None,
            )
        )
        if claimed.rowcount != 1:  # type: ignore[attr-defined]
            raise DomainError(
                ErrorCode.INVALID_SETTINGS,
                "the document is not waiting for a password",
                params={"status": m.DOC_PROCESSING},
            )
        repo.create_job(s, svc.config, job_id=job_id, document_id=doc.id, kind=m.KIND_INGEST)
    svc.files.reset_document_artifacts(doc.id)
    svc.manager.enqueue(job_id, password=body.password)
    return _document_response(svc, doc.id)


@router.get("/documents/{doc_id}/cad", response_model=CadInfo)
def get_cad_info(doc_id: str, svc: ServicesDep) -> CadInfo:
    """Layouts, units, scale and colour policy of a DXF / DWG document (for the options dialog)."""
    doc, _ = ready_document(svc, doc_id)
    try:
        info = IngestResult.load(svc.files.doc_dir(doc.id)).cad
    except (OSError, ValueError):
        info = None
    if info is None:
        raise DomainError(ErrorCode.NOT_FOUND, "the document is not a CAD drawing")
    return info


@router.post("/documents/{doc_id}/cad-options", status_code=202, response_model=DocumentOut)
def set_cad_options(doc_id: str, body: CadOptions, svc: ServicesDep) -> DocumentOut:
    """Re-ingest a DXF / DWG with another layout, units, scale or colour policy."""
    doc = get_document_row(svc, doc_id)
    if doc.format not in CAD_EXTENSIONS or doc.status != m.DOC_READY:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            "CAD options only apply to a ready DXF or DWG document",
            params={"status": doc.status, "format": doc.format},
        )
    job_id = new_id()
    with svc.db.session() as s:
        # Atomic claim, as in the password retry.
        claimed = s.execute(
            update(m.Document)
            .where(m.Document.id == doc.id, m.Document.status == m.DOC_READY)
            .values(
                status=m.DOC_PROCESSING,
                error_code=None,
                error_detail=None,
                error_params_json=None,
            )
        )
        if claimed.rowcount != 1:  # type: ignore[attr-defined]
            raise DomainError(
                ErrorCode.INVALID_SETTINGS,
                "the document is being processed",
                params={"status": m.DOC_PROCESSING},
            )
        repo.create_job(
            s,
            svc.config,
            job_id=job_id,
            document_id=doc.id,
            kind=m.KIND_INGEST,
            options={**(_cad_job_options(body) or {}), "reingest": True},
        )
    # The previous rendering stays in place: the job renders into a staging directory and swaps
    # it in only on success (a failure leaves the document READY with the error on the job).
    svc.manager.enqueue(job_id)
    return _document_response(svc, doc.id)


@router.post("/documents/{doc_id}/pages/{n}/physical-size", response_model=Page)
def set_page_physical_size(doc_id: str, n: int, body: PhysicalSizeBody, svc: ServicesDep) -> Page:
    """Fix the physical size of a raster page (paper, width, height or dpi)."""
    doc, pages = ready_document(svc, doc_id)
    if not 0 <= n < len(pages):
        raise DomainError(ErrorCode.NOT_FOUND, f"page {n} does not exist")
    with _PIXEL_LOCK:
        result: IngestResult = set_physical_size(
            svc.files.doc_dir(doc.id),
            n,
            paper=body.paper,
            width_mm=body.width_mm,
            height_mm=body.height_mm,
            dpi=body.dpi,
            config=svc.config,
        )
        with svc.db.session() as s:
            row = s.get(m.Document, doc.id)
            assert row is not None
            row.pages_json = dumps([p.model_dump(mode="json") for p in result.pages])
    return result.pages[n]


def _doc_and_active_jobs(svc: Services, doc_id: str) -> tuple[m.Document, list[str]]:
    doc = get_document_row(svc, doc_id)
    with svc.db.session() as s:
        return doc, repo.active_job_ids(s, doc.id)


def _delete_document_rows(svc: Services, doc_id: str) -> None:
    """Blocking part of ``DELETE /documents/{id}``: rows plus the job and document directories."""
    with svc.db.session() as s:
        job_ids = list(s.scalars(select(m.Job.id).where(m.Job.document_id == doc_id)))
        row = s.get(m.Document, doc_id)
        if row is not None:
            s.delete(row)  # jobs go with it (ON DELETE CASCADE)
    for job_id in job_ids:
        svc.files.remove_job(job_id)
    svc.files.remove_document(doc_id)


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, svc: ServicesDep) -> Response:
    doc, active = await asyncio.to_thread(_doc_and_active_jobs, svc, doc_id)
    for job_id in active:
        await svc.manager.cancel(job_id, wait=True)
    await asyncio.to_thread(_delete_document_rows, svc, doc.id)
    return Response(status_code=204)


# ------------------------------------------------------------------------ images, events

_IMMUTABLE = {"Cache-Control": "private, max-age=3600"}


def _page_image(svc: Services, doc_id: str, n: int, filename: str, max_px: int | None) -> Response:
    doc, pages = ready_document(svc, doc_id)
    if not 0 <= n < len(pages):
        raise DomainError(ErrorCode.NOT_FOUND, f"page {n} does not exist")
    path = svc.files.page_dir(doc.id, n) / filename
    if not path.is_file():
        raise DomainError(ErrorCode.NOT_FOUND, "image not found")
    if max_px is not None:
        max_px = min(max_px, svc.config.preview_max_px)
        with Image.open(path) as img:
            if max(img.size) > max_px:
                resized = img.convert("RGB")
                resized.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                resized.save(buf, format="WEBP", quality=85)
                return Response(buf.getvalue(), media_type="image/webp", headers=_IMMUTABLE)
    return FileResponse(path, media_type="image/webp", headers=_IMMUTABLE)


@router.get(
    "/documents/{doc_id}/pages/{n}/preview",
    response_class=Response,
    responses={200: {"content": {"image/webp": {}}}},
)
def page_preview(
    doc_id: str,
    n: int,
    svc: ServicesDep,
    max_px: Annotated[int | None, Query(ge=64, le=16384)] = None,
    hidden_layers: Annotated[str | None, Query(max_length=2000)] = None,
    visible_layers: Annotated[str | None, Query(max_length=2000)] = None,
) -> Response:
    """Page preview; ``hidden_layers`` / ``visible_layers`` (comma separated xrefs or names)
    render a PDF page with those layers off / forced on, as the output will be (ANA-07).
    The mirror (ESC-06) is applied by the UI."""
    hidden = [h.strip() for h in (hidden_layers or "").split(",") if h.strip()]
    visible = [h.strip() for h in (visible_layers or "").split(",") if h.strip()]
    canonical = svc.files.doc_dir(doc_id) / "canonical.pdf"
    if (not hidden and not visible) or not canonical.is_file():
        # the stored preview already shows the source's own layer state
        return _page_image(svc, doc_id, n, PREVIEW_FILE, max_px)
    _doc, pages = ready_document(svc, doc_id)
    if not 0 <= n < len(pages):
        raise DomainError(ErrorCode.NOT_FOUND, f"page {n} does not exist")
    px = min(max_px or svc.config.preview_max_px, svc.config.preview_max_px)
    data = layered_preview_webp(canonical, n, pages[n].layers, hidden, visible, px)
    return Response(data, media_type="image/webp", headers=_IMMUTABLE)


@router.get(
    "/documents/{doc_id}/pages/{n}/thumb",
    response_class=Response,
    responses={200: {"content": {"image/webp": {}}}},
)
def page_thumb(doc_id: str, n: int, svc: ServicesDep) -> Response:
    return _page_image(svc, doc_id, n, THUMB_FILE, None)


@router.get(
    "/documents/{doc_id}/events",
    response_class=Response,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def document_events(doc_id: str, svc: ServicesDep) -> Response:
    """SSE of the latest ingest of the document (``progress``, then ``done`` or ``error``)."""
    doc = get_document_row(svc, doc_id)
    with svc.db.session() as s:
        job = repo.latest_ingest_job(s, doc.id)
    if job is None:
        raise DomainError(ErrorCode.NOT_FOUND, "the document has no ingest job")
    return sse_response(event_stream(svc, job.id, "document"))
