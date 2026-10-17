"""Direct printing over IPP (IMP-03/04, PAP-07). Everything here needs ``A0TD_ENABLE_IPP``."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Response

from a0td.api.deps import Services, ServicesDep
from a0td.api.errors import ProblemError
from a0td.api.routers.jobs import _get_job
from a0td.api.schemas import IppCapabilitiesRequest, JobOut, OutputFile, PrintRequest, job_out
from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.plan import Plan
from a0td.domain.settings import JobSettings
from a0td.printing import discovery
from a0td.printing.ipp import (
    REJECTED,
    IppClient,
    IppError,
    capabilities,
    wanted_capability_attributes,
)
from a0td.printing.submit import is_monochrome, media_for, sheet_pages
from a0td.storage import models as m
from a0td.storage import repo
from a0td.storage.files import new_id
from a0td.storage.models import loads

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["printing"])


def _require_ipp(svc: Services) -> None:
    if not svc.config.enable_ipp:
        raise ProblemError(
            501, "IPP_DISABLED", "direct printing is disabled (set A0TD_ENABLE_IPP=true)"
        )


def _unreachable(exc: IppError) -> DomainError:
    """One generic answer for every failure (refused, timeout, 404, blocked host...)."""
    log.warning("ipp-capabilities failed (%s): %s", exc.reason, exc.detail)
    return DomainError(ErrorCode.PRINTER_UNREACHABLE, "the printer cannot be reached")


@router.post("/printers/ipp-capabilities")
def ipp_capabilities(body: IppCapabilitiesRequest, svc: ServicesDep) -> dict[str, Any]:
    """Get-Printer-Attributes: papers, margins and formats to prefill a profile (PAP-07)."""
    _require_ipp(svc)
    try:
        client = IppClient(
            body.uri,
            timeout=svc.config.ipp_timeout_s,
            verify_tls=not svc.config.ipp_insecure_tls,
        )
        attrs = client.get_printer_attributes(wanted_capability_attributes())
    except IppError as exc:
        if exc.reason == REJECTED and exc.status is None:  # bad URI
            raise DomainError(ErrorCode.INVALID_SETTINGS, "unsupported printer URI") from exc
        raise _unreachable(exc) from exc
    return capabilities(attrs, client.printer_uri)


@router.get("/printers/discover")
async def discover_printers(svc: ServicesDep) -> list[dict[str, Any]]:
    """mDNS browse (IMP-04). 501 when it cannot work (no zeroconf, no multicast)."""
    _require_ipp(svc)
    try:
        return await asyncio.to_thread(discovery.discover)
    except discovery.DiscoveryUnavailableError as exc:
        raise ProblemError(
            501,
            "DISCOVERY_UNAVAILABLE",
            f"{exc}. mDNS discovery needs the zeroconf package and host networking "
            "(Docker network_mode: host on Linux); enter the printer URI by hand instead.",
        ) from exc


def _create_print_job(svc: Services, render_id: str, body: PrintRequest) -> str:
    render = _get_job(svc, render_id)
    if render.kind != m.KIND_RENDER or render.status != m.JOB_SUCCEEDED:
        raise DomainError(
            ErrorCode.DOCUMENT_NOT_READY,
            "only a finished render job can be printed",
            params={"status": render.status},
        )
    with svc.db.session() as s:
        printer = repo.get_printer(s, body.printer_id)
    if printer is None:
        raise DomainError(ErrorCode.NOT_FOUND, "printer not found")
    if not printer.ipp_uri:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            "this printer profile has no IPP URI",
            errors=[{"field": "printer_id", "message": "no ipp_uri", "type": "value"}],
        )
    outputs = [OutputFile.model_validate(o) for o in loads(render.outputs_json, []) or []]
    pdf = next((o for o in outputs if o.kind == "pdf"), None)
    if pdf is None:
        raise DomainError(ErrorCode.INVALID_SETTINGS, "the job has no PDF output to print")
    plans = [Plan.model_validate(p) for p in loads(render.plans_json, []) or []]
    if not plans:
        raise DomainError(ErrorCode.DOCUMENT_NOT_READY, "the job has no plans")
    settings = JobSettings.model_validate(loads(render.settings_json, {}))
    test_only = bool((loads(render.options_json, {}) or {}).get("test_sheet_only"))
    pages = None
    if body.sheets:
        pages = sheet_pages(plans, settings, body.sheets, test_sheet_only=test_only)
    job_id = new_id()
    with svc.db.session() as s:
        repo.create_job(
            s,
            svc.config,
            job_id=job_id,
            document_id=render.document_id,
            kind=m.KIND_PRINT,
            options={
                "render_job_id": render.id,
                "output": pdf.name,
                "printer_id": printer.id,
                "ipp_uri": printer.ipp_uri,
                "media": media_for(plans[0]),
                "copies": body.copies,
                "monochrome": is_monochrome(settings, printer),
                "pages": pages,
                "job_name": pdf.name.rsplit(".", 1)[0],
            },
        )
    return job_id


@router.post("/jobs/{job_id}/print", status_code=202, response_model=JobOut)
def print_job(job_id: str, body: PrintRequest, response: Response, svc: ServicesDep) -> JobOut:
    """Queue a ``print`` job that sends the render job's PDF to the printer profile (IMP-03)."""
    _require_ipp(svc)
    new = _create_print_job(svc, job_id, body)
    svc.manager.enqueue(new)
    response.headers["Location"] = f"/api/v1/jobs/{new}"
    return job_out(_get_job(svc, new))
