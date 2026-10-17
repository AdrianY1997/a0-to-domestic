"""Planning helpers shared by ``POST /plan`` and ``POST /jobs`` (pure, in-process)."""

import threading
from collections import OrderedDict

from a0td.api.deps import Services
from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.plan import Plan
from a0td.domain.planner import plan as make_plan
from a0td.domain.settings import BlankSensitivity, JobSettings
from a0td.ingest.models import Page
from a0td.ingest.occupancy import OCCUPANCY_FILES, PixelOccupancy, load_occupancy
from a0td.jobs.exact_scale import is_exact_scale
from a0td.storage import models as m
from a0td.storage import repo
from a0td.storage.files import valid_id
from a0td.storage.models import loads

_CACHE_SIZE = 16
_occ_cache: OrderedDict[tuple[str, int, int, float, float], PixelOccupancy] = OrderedDict()
_occ_lock = threading.Lock()


def get_document_row(services: Services, doc_id: str) -> m.Document:
    canonical = valid_id(doc_id)
    with services.db.session() as s:
        doc = s.get(m.Document, canonical) if canonical else None
        if doc is None:
            raise DomainError(ErrorCode.NOT_FOUND, "document not found")
        return doc


def ready_document(services: Services, doc_id: str) -> tuple[m.Document, list[Page]]:
    """The document and its pages, or ``DOCUMENT_NOT_READY`` (409) / ``NOT_FOUND`` (404)."""
    doc = get_document_row(services, doc_id)
    if doc.status != m.DOC_READY:
        raise DomainError(
            ErrorCode.DOCUMENT_NOT_READY,
            f"document is {doc.status}",
            params={"status": doc.status},
        )
    return doc, [Page.model_validate(p) for p in loads(doc.pages_json, []) or []]


def settings_document_id(settings: JobSettings) -> str:
    if settings.document_id is None:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            "settings.document_id is required",
            errors=[{"field": "settings.document_id", "message": "required", "type": "missing"}],
        )
    return str(settings.document_id)


def _occupancy(
    services: Services, doc_id: str, page: Page, settings: JobSettings
) -> PixelOccupancy | None:
    pdir = services.files.page_dir(doc_id, page.index)
    sens = settings.tiling.blank_sensitivity
    path = pdir / OCCUPANCY_FILES[sens]
    if not path.exists():
        path = pdir / OCCUPANCY_FILES[BlankSensitivity.NORMAL]
    if not path.exists():
        return None
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size, page.width_mm, page.height_mm)
    with _occ_lock:
        hit = _occ_cache.get(key)
        if hit is not None:
            _occ_cache.move_to_end(key)
            return hit
    occ = load_occupancy(pdir, page, sens)
    with _occ_lock:
        _occ_cache[key] = occ
        while len(_occ_cache) > _CACHE_SIZE:
            _occ_cache.popitem(last=False)
    return occ


def compute_plan(
    services: Services,
    doc: m.Document,
    pages: list[Page],
    settings: JobSettings,
    page_index: int,
    *,
    include_alternatives: bool = False,
    preset_id: str | None = None,
    with_occupancy: bool = True,
) -> Plan:
    if not 0 <= page_index < len(pages):
        raise DomainError(ErrorCode.NOT_FOUND, f"page {page_index} does not exist")
    page = pages[page_index]
    with services.db.session() as s:
        printer = repo.get_printer(s, settings.target.printer_id)
    occ = _occupancy(services, doc.id, page, settings) if with_occupancy else None
    return make_plan(
        page.to_geometry(len(pages)),
        settings,
        printer=printer,
        occupancy=occ,
        include_alternatives=include_alternatives,
        exact_scale=is_exact_scale(settings, preset_id),
        max_sheets=services.config.max_sheets,
    )
