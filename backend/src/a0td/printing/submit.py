"""Direct printing over IPP (SPEC IMP-03): format negotiation, PWG Raster fallback, job status.

The pure functions here are used by the API (to resolve sheet ids to PDF pages and to build the
job options) and by the worker (:func:`submit_pdf`, which talks to the printer).
"""

import contextlib
import logging
import signal
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any

import pymupdf

from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.papers import pwg_media_name
from a0td.domain.plan import Plan
from a0td.domain.settings import AssemblyPage, ColorMode, JobSettings, PrinterProfile
from a0td.ingest.converters.base import run_tool
from a0td.printing.ipp import (
    JOB_ABORTED,
    JOB_CANCELED,
    JOB_COMPLETED,
    JOB_STATE_NAMES,
    REJECTED,
    UNREACHABLE,
    IppClient,
    IppError,
    job_template,
)

log = logging.getLogger(__name__)

PDF = "application/pdf"
PWG_RASTER = "image/pwg-raster"
DEFAULT_RASTER_DPI = 300
MAX_RASTER_DPI = 600
POLL_INTERVAL_S = 2.0
POLL_TIMEOUT_S = 180.0

Progress = Callable[[str, float, str], None]


# ------------------------------------------------------------------ API-side helpers


def sheet_pages(
    plans: Sequence[Plan],
    settings: JobSettings,
    wanted: Sequence[str],
    *,
    test_sheet_only: bool = False,
) -> list[int]:
    """1-based pages of the rendered PDF that hold the ``wanted`` sheet ids.

    Mirrors the page order of ``render_job``: per source page an optional assembly page (first or
    last) and the printed sheets in print order. Unknown ids raise ``INVALID_SETTINGS``.
    """
    if test_sheet_only:
        raise DomainError(ErrorCode.INVALID_SETTINGS, "a test-sheet job has no sheets to choose")
    pos = settings.marks.assembly_page
    index: dict[str, int] = {}
    page_no = 0
    for plan in plans:
        if pos is AssemblyPage.FIRST:
            page_no += 1
        for sheet in plan.sheets:
            if sheet.print_index is None:
                continue
            page_no += 1
            index[sheet.id.upper()] = page_no
        if pos is AssemblyPage.LAST:
            page_no += 1
    pages: list[int] = []
    unknown: list[str] = []
    for raw in wanted:
        key = raw.strip().upper()
        found = index.get(key)
        if found is None and len(plans) == 1:  # tolerate a "P1-" prefix on single-page jobs
            found = index.get(key.split("-", 1)[-1]) if key.startswith("P") else None
        if found is None:
            unknown.append(raw)
        else:
            pages.append(found)
    if unknown:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            f"unknown or unprinted sheets: {', '.join(unknown)}",
            params={"sheets": unknown},
        )
    return sorted(set(pages))


def media_for(plan: Plan) -> str:
    """PWG ``media`` name of the sheet paper (catalog name, or a self-describing custom one)."""
    return pwg_media_name(plan.paper.id, plan.paper.width_mm, plan.paper.height_mm)


def is_monochrome(settings: JobSettings, printer: PrinterProfile) -> bool:
    return (not printer.color) or settings.color.mode in (ColorMode.GRAYSCALE, ColorMode.BW)


# ----------------------------------------------------------------------- worker side


@dataclass(slots=True)
class SubmitOutcome:
    job_id: int
    state: str  # final job-state name, or "unknown" when the printer could not tell
    warnings: list[dict[str, Any]] = field(default_factory=list)


def _pick_dpi(attrs: dict[str, list[Any]]) -> int:
    best: int | None = None
    for res in attrs.get("pwg-raster-document-resolution-supported", []):
        if not (isinstance(res, tuple) and len(res) == 3):
            continue
        x, y, unit = res
        if unit == 4:  # dots per cm
            x, y = round(x * 2.54), round(y * 2.54)
        if x != y or x > MAX_RASTER_DPI:
            continue
        if best is None or abs(x - DEFAULT_RASTER_DPI) < abs(best - DEFAULT_RASTER_DPI):
            best = int(x)
    return best or DEFAULT_RASTER_DPI


def _select_pages(pdf: Path, pages: Sequence[int], dest: Path) -> Path:
    with pymupdf.open(pdf) as doc:
        doc.select([p - 1 for p in pages if 1 <= p <= doc.page_count])
        doc.save(dest)
    return dest


def _to_pwg_raster(pdf: Path, dest: Path, dpi: int, scratch: Path) -> Path:
    """Ghostscript ``pwgraster`` device (through the hardened subprocess helper)."""
    run_tool(
        [
            "gs",
            "-q",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=pwgraster",
            f"-r{dpi}",
            f"-sOutputFile={dest}",
            str(pdf),
        ],
        scratch=scratch,
        timeout_s=300,
        memory_mb=2048,
    )
    return dest


class _Terminated(BaseException):
    """SIGTERM while waiting for the printer: the job is cancelled."""


def submit_pdf(
    client: IppClient,
    pdf: Path,
    *,
    scratch: Path,
    media: str,
    copies: int,
    monochrome: bool,
    pages: Sequence[int] | None,
    job_name: str,
    progress: Progress | None = None,
    poll_timeout_s: float = POLL_TIMEOUT_S,
    poll_interval_s: float = POLL_INTERVAL_S,
) -> SubmitOutcome:
    """Send ``pdf`` to the printer and follow the job until it ends.

    ``pages`` (1-based) selects part of the PDF. The document is sent as PDF when the printer
    accepts it, otherwise converted to PWG Raster with Ghostscript. Raises
    :class:`DomainError` ``PRINTER_UNREACHABLE`` / ``PRINT_REJECTED``.
    """

    def say(key: str, pct: float) -> None:
        if progress is not None:
            progress("print", pct, key)

    try:
        say("print.connect", 5)
        try:
            attrs = client.get_printer_attributes(
                [
                    "document-format-supported",
                    "page-ranges-supported",
                    "pwg-raster-document-resolution-supported",
                ]
            )
        except IppError as exc:
            if exc.reason == UNREACHABLE:
                raise
            attrs = {}  # some printers refuse the query: assume PDF
        formats = [str(f) for f in attrs.get("document-format-supported", [])]
        ranges_ok = bool(attrs.get("page-ranges-supported", [False])[0]) if attrs else False
        warnings: list[dict[str, Any]] = []

        send_pages: Sequence[int] | None = pages
        source = pdf
        if formats and PDF not in formats:
            if PWG_RASTER not in formats:
                raise DomainError(
                    ErrorCode.PRINT_REJECTED,
                    "the printer accepts neither PDF nor PWG Raster",
                    params={"formats": formats},
                )
            say("print.convert", 15)
            if pages:
                source = _select_pages(pdf, pages, scratch / "selected.pdf")
                send_pages = None
            dpi = _pick_dpi(attrs)
            source = _to_pwg_raster(source, scratch / "job.pwg", dpi, scratch)
            document_format = PWG_RASTER
        else:
            document_format = PDF
            if pages and not ranges_ok:
                source = _select_pages(pdf, pages, scratch / "selected.pdf")
                send_pages = None

        say("print.send", 40)
        result = client.print_job(
            source,
            document_format=document_format,
            job_name=job_name,
            job_attrs=job_template(
                media=media, copies=copies, monochrome=monochrome, pages=send_pages
            ),
        )
        if result.unsupported:
            warnings.append(
                {"code": "PRINT_ATTRIBUTES_IGNORED", "params": {"attributes": result.unsupported}}
            )
        say("print.printing", 60)
        state = _follow(client, result.job_id, result.state, say, poll_timeout_s, poll_interval_s)
        if state == "unknown":
            warnings.append({"code": "PRINT_STATUS_UNKNOWN", "params": {}})
        return SubmitOutcome(result.job_id, state, warnings)
    except IppError as exc:
        code = (
            ErrorCode.PRINTER_UNREACHABLE if exc.reason == UNREACHABLE else ErrorCode.PRINT_REJECTED
        )
        log.warning("ipp print failed (%s): %s", exc.reason, exc.detail)
        raise DomainError(
            code,
            "the printer cannot be reached"
            if exc.reason == UNREACHABLE
            else "the printer rejected the job",
            params={"ipp_status": exc.status, "job_state": exc.job_state},
        ) from exc


def _follow(
    client: IppClient,
    job_id: int,
    first_state: int | None,
    say: Callable[[str, float], None],
    timeout_s: float,
    interval_s: float,
) -> str:
    """Poll Get-Job-Attributes until a final state. ``"unknown"`` if the printer cannot say."""

    def on_term(_signum: int, _frame: FrameType | None) -> None:
        raise _Terminated

    previous = None
    try:
        previous = signal.signal(signal.SIGTERM, on_term)
    except ValueError:  # not the main thread (tests): no cancel support
        previous = None
    state = first_state
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            if state in (JOB_COMPLETED, JOB_CANCELED, JOB_ABORTED):
                break
            if time.monotonic() >= deadline:
                return "unknown"
            time.sleep(interval_s)
            try:
                state, reasons = client.job_state(job_id)
            except IppError:
                return "unknown"
            if state is None:
                return "unknown"
            say("print.printing", 60 + 35 * min(1.0, 1 - (deadline - time.monotonic()) / timeout_s))
            log.info("ipp job %s: %s %s", job_id, JOB_STATE_NAMES.get(state), reasons)
    except _Terminated:
        with contextlib.suppress(IppError):
            client.cancel_job(job_id)
        raise
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if state == JOB_COMPLETED:
        return "completed"
    name = JOB_STATE_NAMES.get(state or 0, "aborted")
    raise IppError(REJECTED, f"the printer ended the job as {name}", job_state=state)
