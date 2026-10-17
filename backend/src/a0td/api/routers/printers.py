"""Printer profiles: built-ins are read-only, user profiles are editable (SPEC 10, PAP-04, CAL)."""

import logging
import uuid
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from a0td.api.deps import ServicesDep
from a0td.domain.calibration import apply_calibration, compute_calibration
from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.papers import PaperId
from a0td.domain.settings import Margins, PartialSettings, PrinterKind, PrinterProfile
from a0td.printing.ipp import IppError, normalize_uri
from a0td.printing.netpolicy import HostNotAllowedError, resolve_allowed
from a0td.render.calibration import render_calibration_pdf, ruler_lengths_mm
from a0td.storage import repo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["printers"])


class PrinterIn(BaseModel):
    """Editable fields of a user profile (id, ``builtin`` are server side)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: PrinterKind = PrinterKind.GENERIC
    supported_papers: list[PaperId] | None = None
    default_margins: Margins = Field(default_factory=Margins)
    paper_margins: dict[PaperId, Margins] = Field(default_factory=dict)
    borderless: bool = False
    color: bool = True
    scale_kx: float = Field(default=1.0, ge=0.9, le=1.1)
    scale_ky: float = Field(default=1.0, ge=0.9, le=1.1)
    ipp_uri: str | None = None
    defaults: PartialSettings = Field(default_factory=dict)

    @field_validator("ipp_uri")
    @classmethod
    def _check_ipp_uri(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if (
            urlsplit(v).scheme.lower() not in {"ipp", "ipps", "http", "https"}
            or not urlsplit(v).hostname
        ):
            raise ValueError("ipp_uri must be an ipp://, ipps://, http:// or https:// URI")
        try:
            http_url, _ = normalize_uri(v)
            parts = urlsplit(http_url)
            assert parts.hostname is not None
            assert parts.port is not None
            resolve_allowed(parts.hostname, parts.port)
        except IppError as exc:
            raise ValueError("ipp_uri is not a valid printer URI") from exc
        except HostNotAllowedError as exc:
            if exc.unresolved:  # an offline printer may be saved; the policy runs again on use
                return v
            log.warning("ipp_uri refused: %s", exc.detail)
            raise ValueError("ipp_uri points to a host that is not allowed") from exc
        return v

    def to_profile(self, printer_id: str) -> PrinterProfile:
        data: dict[str, Any] = self.model_dump(exclude_none=True)
        data["id"] = printer_id
        data["name"] = self.name.strip()
        return PrinterProfile.model_validate({**data, "builtin": False})


class CalibrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper: PaperId
    measured_x_mm: float = Field(gt=0, le=2000)
    measured_y_mm: float = Field(gt=0, le=2000)
    nominal_x_mm: float = Field(gt=0, le=2000)
    nominal_y_mm: float = Field(gt=0, le=2000)
    margins_mm: Margins


def _get(svc: ServicesDep, printer_id: str) -> PrinterProfile:
    with svc.db.session() as s:
        printer = repo.get_printer(s, printer_id)
    if printer is None:
        raise DomainError(ErrorCode.NOT_FOUND, "printer not found")
    return printer


def _editable(svc: ServicesDep, printer_id: str) -> PrinterProfile:
    printer = _get(svc, printer_id)
    if printer.builtin:
        raise DomainError(ErrorCode.PRINTER_READONLY, "built-in profiles cannot be modified")
    return printer


@router.get("/printers", response_model=list[PrinterProfile])
def list_printers(svc: ServicesDep) -> list[PrinterProfile]:
    with svc.db.session() as s:
        return repo.list_printers(s)


@router.post("/printers", response_model=PrinterProfile, status_code=201)
def create_printer(body: PrinterIn, svc: ServicesDep) -> PrinterProfile:
    profile = body.to_profile(f"user-{uuid.uuid4().hex[:12]}")
    with svc.db.session() as s:
        return repo.save_printer(s, profile)


@router.get("/printers/{printer_id}", response_model=PrinterProfile)
def get_printer(printer_id: str, svc: ServicesDep) -> PrinterProfile:
    return _get(svc, printer_id)


@router.put("/printers/{printer_id}", response_model=PrinterProfile)
def update_printer(printer_id: str, body: PrinterIn, svc: ServicesDep) -> PrinterProfile:
    _editable(svc, printer_id)
    with svc.db.session() as s:
        return repo.save_printer(s, body.to_profile(printer_id))


@router.delete("/printers/{printer_id}", status_code=204)
def delete_printer(printer_id: str, svc: ServicesDep) -> Response:
    _editable(svc, printer_id)
    with svc.db.session() as s:
        repo.delete_printer(s, printer_id)
    return Response(status_code=204)


@router.get(
    "/printers/{printer_id}/calibration.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def calibration_pdf(
    printer_id: str,
    svc: ServicesDep,
    paper: Annotated[PaperId, Query()] = PaperId.A4,
    locale: Annotated[str, Query(max_length=16)] = "es",
) -> Response:
    printer = _get(svc, printer_id)
    if paper is PaperId.CUSTOM:
        raise DomainError(ErrorCode.INVALID_SETTINGS, "choose a catalog paper")
    data = render_calibration_pdf(paper, printer_name=printer.name, locale=locale)
    return Response(
        data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="calibration-{paper.value}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/printers/{printer_id}/calibration", response_model=PrinterProfile)
def calibrate(printer_id: str, body: CalibrationIn, svc: ServicesDep) -> PrinterProfile:
    """Store ``k = measured / nominal`` and the measured margins + 0.5 mm for ``body.paper``."""
    printer = _editable(svc, printer_id)
    if body.paper is PaperId.CUSTOM:
        raise DomainError(ErrorCode.INVALID_SETTINGS, "choose a catalog paper")
    ruler_x, ruler_y = ruler_lengths_mm(body.paper)
    if abs(body.nominal_x_mm - ruler_x) > 0.01 or abs(body.nominal_y_mm - ruler_y) > 0.01:
        raise DomainError(ErrorCode.INVALID_SETTINGS, "nominal lengths do not match the ruler")
    result = compute_calibration(
        measured_x_mm=body.measured_x_mm,
        measured_y_mm=body.measured_y_mm,
        nominal_x_mm=body.nominal_x_mm,
        nominal_y_mm=body.nominal_y_mm,
        margins_mm=body.margins_mm,
    )
    updated = apply_calibration(printer, body.paper, result)
    with svc.db.session() as s:
        return repo.save_printer(s, updated)
