"""Planning endpoints: the pure plan and the single-sheet preview (SPEC 10)."""

import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Response

from a0td.api.deps import ServicesDep
from a0td.api.errors import ProblemError
from a0td.api.planning import compute_plan, ready_document, settings_document_id
from a0td.api.schemas import PlanRequest, SheetPreviewRequest
from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.plan import Plan
from a0td.jobs.render_loader import RenderUnavailableError, engine_function
from a0td.storage import repo

router = APIRouter(prefix="/api/v1", tags=["plan"])

_preview_slots = threading.BoundedSemaphore(2)  # sheet previews are CPU heavy; run at most two


@router.post("/plan", response_model=Plan)
def post_plan(body: PlanRequest, svc: ServicesDep) -> Plan:
    """Tiling plan of one page. Pure: nothing is rendered or stored."""
    doc, pages = ready_document(svc, settings_document_id(body.settings))
    return compute_plan(
        svc,
        doc,
        pages,
        body.settings,
        body.page_index,
        include_alternatives=body.include_alternatives,
        preset_id=body.preset_id,
    )


@router.post(
    "/plan/sheet-preview",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def sheet_preview(body: SheetPreviewRequest, svc: ServicesDep) -> Response:
    """PNG of one sheet at low resolution (needs the render engine; 501 without it)."""
    doc, pages = ready_document(svc, settings_document_id(body.settings))
    if body.page_index >= len(pages):
        raise DomainError(ErrorCode.NOT_FOUND, f"page {body.page_index} does not exist")
    try:
        render: Callable[..., bytes] = engine_function("render_sheet_preview")  # type: ignore[assignment]
    except RenderUnavailableError as exc:
        raise ProblemError(501, "RENDER_UNAVAILABLE", str(exc)) from exc
    max_px = min(body.max_px, svc.config.preview_max_px)
    with svc.db.session() as s:
        printer = repo.get_printer(s, body.settings.target.printer_id)
    kwargs: dict[str, Any] = {"printer": printer}
    with _preview_slots:
        png = render(
            svc.files.doc_dir(doc.id),
            body.settings,
            body.page_index,
            body.sheet_id,
            max_px,
            **kwargs,
        )
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})
