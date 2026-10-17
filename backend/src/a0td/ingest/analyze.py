"""Page analysis (SPEC 6.2 ANA-01..03, 11.1 ``Page``)."""

import math
from pathlib import Path

import numpy as np
import pymupdf

from a0td.domain.errors import DomainError, ErrorCode, Severity, WarningCode
from a0td.domain.papers import (
    PAPERS,
    SOURCE_SIZES,
    Orientation,
    SizeMatch,
    SourceSize,
    match_source_size,
)
from a0td.domain.plan import PlanWarning
from a0td.domain.settings import SourceBox
from a0td.domain.units import MM_PER_INCH
from a0td.ingest.models import (
    CanonicalDocument,
    CanonicalPage,
    DpiSource,
    Layer,
    Page,
    PageKind,
    PxSize,
    StandardMatch,
)
from a0td.ingest.stats import page_stats

# ANA-03: a physical side outside this range is not believable.
PLAUSIBLE_MIN_MM = 50.0
PLAUSIBLE_MAX_MM = 5000.0
ASSUMED_PAPER_ID = "A0"
_A0_FALLBACK = (841.0, 1189.0)

# ANA-02 thresholds.
RASTER_COVERAGE = 0.90
VECTOR_COVERAGE = 0.10
RASTER_MAX_PATHS = 10
RASTER_MAX_TEXT = 10
_COVERAGE_GRID = 128


def _standard_match(width_mm: float, height_mm: float) -> StandardMatch | None:
    m: SizeMatch[SourceSize] | None = match_source_size(width_mm, height_mm)
    if m is None:
        return None
    return StandardMatch(
        id=m.standard.id,
        orientation=Orientation.LANDSCAPE if m.landscape else Orientation.PORTRAIT,
        delta_mm=round(m.deviation_mm, 2),
    )


def _size_warnings(
    match: StandardMatch | None, *, assumed: bool, assumed_paper: str = ASSUMED_PAPER_ID
) -> list[PlanWarning]:
    out: list[PlanWarning] = []
    if assumed:
        out.append(
            PlanWarning(
                code=WarningCode.PHYSICAL_SIZE_ASSUMED,
                severity=Severity.WARNING,
                params={"paper": assumed_paper},
            )
        )
    if match is None:
        out.append(PlanWarning(code=WarningCode.NONSTANDARD_SIZE, severity=Severity.INFO))
    return out


# ------------------------------------------------------------------------------- raster


def _source_size(paper_id: str) -> tuple[float, float]:
    """Portrait size of a source size or output paper by id (case-insensitive)."""
    wanted = paper_id.strip().upper()
    for s in SOURCE_SIZES:
        if s.id.upper() == wanted:
            return (s.width_mm, s.height_mm)
    for pid, p in PAPERS.items():
        if pid.upper() == wanted:
            return (p.width_mm, p.height_mm)
    raise DomainError(
        ErrorCode.INVALID_SETTINGS,
        f"unknown paper '{paper_id}'",
        errors=[{"loc": ["paper"], "msg": "unknown paper"}],
    )


def _fit_mm_per_px(px_w: float, px_h: float, paper: tuple[float, float]) -> float:
    """mm per pixel that fits the image inside ``paper`` keeping the image orientation."""
    short_p, long_p = sorted(paper)
    short_px, long_px = sorted((px_w, px_h))
    return min(short_p / short_px, long_p / long_px)


def _plausible(width_mm: float, height_mm: float) -> bool:
    longest = max(width_mm, height_mm)
    return PLAUSIBLE_MIN_MM <= longest <= PLAUSIBLE_MAX_MM


def resolve_raster_size(
    px_w: int, px_h: int, dpi_meta: float | tuple[float, float] | None
) -> tuple[float, float, float, DpiSource, str | None]:
    """ANA-03. Returns ``(width_mm, height_mm, dpi, source, assumed_paper)``.

    Missing or implausible metadata -> the image is assumed to fill an A0 sheet in its own
    orientation (``assumed_paper`` is then ``"A0"``).
    """
    if dpi_meta:
        dpi_x, dpi_y = (dpi_meta, dpi_meta) if isinstance(dpi_meta, int | float) else dpi_meta
        w, h = px_w * MM_PER_INCH / dpi_x, px_h * MM_PER_INCH / dpi_y
        if _plausible(w, h):
            return w, h, dpi_x, DpiSource.METADATA, None  # Page.dpi reports the horizontal DPI
    a0 = next(((s.width_mm, s.height_mm) for s in SOURCE_SIZES if s.id == ASSUMED_PAPER_ID), None)
    mm_per_px = _fit_mm_per_px(px_w, px_h, a0 or _A0_FALLBACK)
    return (
        px_w * mm_per_px,
        px_h * mm_per_px,
        MM_PER_INCH / mm_per_px,
        DpiSource.ASSUMED,
        ASSUMED_PAPER_ID,
    )


def fit_to_a0_mm_per_px(px_w: float, px_h: float) -> float:
    """mm per pixel that makes an image / SVG of ``px_w`` x ``px_h`` fill A0 in its orientation."""
    a0 = next(((s.width_mm, s.height_mm) for s in SOURCE_SIZES if s.id == ASSUMED_PAPER_ID), None)
    return _fit_mm_per_px(px_w, px_h, a0 or _A0_FALLBACK)


def _check_page_mm(width_mm: float, height_mm: float, max_page_mm: float, index: int) -> None:
    if max(width_mm, height_mm) > max_page_mm:
        raise DomainError(
            ErrorCode.PAGE_TOO_LARGE,
            f"page {index + 1} would be {max(width_mm, height_mm):.0f} mm, "
            f"the limit is {max_page_mm:.0f} mm",
            params={"page": index + 1, "limit_mm": max_page_mm},
        )


def _raster_page(
    index: int,
    px: tuple[int, int],
    width_mm: float,
    height_mm: float,
    dpi: float,
    source: DpiSource,
    assumed_paper: str | None,
) -> Page:
    w, h = round(width_mm, 3), round(height_mm, 3)
    match = _standard_match(w, h)
    return Page(
        index=index,
        width_mm=w,
        height_mm=h,
        boxes_mm={SourceBox.CROP: (0.0, 0.0, w, h), SourceBox.MEDIA: (0.0, 0.0, w, h)},
        kind=PageKind.RASTER,
        standard_match=match,
        px=PxSize(w=px[0], h=px[1]),
        dpi=round(dpi, 3),
        dpi_source=source,
        has_text=False,
        warnings=_size_warnings(
            match, assumed=source is DpiSource.ASSUMED, assumed_paper=assumed_paper or ""
        ),
    )


def analyze_raster_page(cp: CanonicalPage, max_page_mm: float) -> Page:
    assert cp.px is not None
    w, h, dpi, source, assumed = resolve_raster_size(cp.px[0], cp.px[1], cp.dpi_meta)
    _check_page_mm(w, h, max_page_mm, cp.index)
    return _raster_page(cp.index, cp.px, w, h, dpi, source, assumed)


def apply_physical_size(
    page: Page,
    *,
    paper: str | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    dpi: float | None = None,
    max_page_mm: float = 10000.0,
) -> Page:
    """User override of a raster page's physical size (ANA-03): by ``paper``, ``width_mm``,
    ``height_mm`` or ``dpi`` (exactly one). Returns a new page with ``dpi_source = user``.
    """
    given = [v for v in (paper, width_mm, height_mm, dpi) if v is not None]
    if len(given) != 1:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            "exactly one of paper, width_mm, height_mm or dpi is required",
        )
    if page.px is None:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS, "the physical size can only be set on rasters"
        )
    px_w, px_h = page.px.w, page.px.h
    if paper is not None:
        mm_per_px = _fit_mm_per_px(px_w, px_h, _source_size(paper))
    else:
        value = float(next(v for v in (width_mm, height_mm, dpi) if v is not None))
        if not math.isfinite(value) or value <= 0:
            raise DomainError(ErrorCode.INVALID_SETTINGS, "the value must be greater than 0")
        if width_mm is not None:
            mm_per_px = value / px_w
        elif height_mm is not None:
            mm_per_px = value / px_h
        else:
            mm_per_px = MM_PER_INCH / value
    w, h = px_w * mm_per_px, px_h * mm_per_px
    _check_page_mm(w, h, max_page_mm, page.index)
    out = _raster_page(
        page.index, (px_w, px_h), w, h, MM_PER_INCH / mm_per_px, DpiSource.USER, None
    )
    out.layers = list(page.layers)
    return out


# --------------------------------------------------------------------------------- PDF


def _coverage(rects: list[tuple[float, float, float, float]], page: pymupdf.Rect) -> float:
    """Fraction of the page covered by the union of ``rects`` (coarse grid, good to ~1 %)."""
    if page.width <= 0 or page.height <= 0 or not rects:
        return 0.0
    n = _COVERAGE_GRID
    grid = np.zeros((n, n), dtype=bool)
    for x0, y0, x1, y1 in rects:
        c0 = max(0, math.floor((x0 - page.x0) / page.width * n + 0.5))
        c1 = min(n, math.ceil((x1 - page.x0) / page.width * n - 0.5))
        r0 = max(0, math.floor((y0 - page.y0) / page.height * n + 0.5))
        r1 = min(n, math.ceil((y1 - page.y0) / page.height * n - 0.5))
        if c1 > c0 and r1 > r0:
            grid[r0:r1, c0:c1] = True
    return float(grid.mean())


def classify_pdf_page(page: pymupdf.Page) -> tuple[PageKind, bool]:
    """ANA-02 on a canonical PDF page. Returns ``(kind, has_text)``."""
    log = page.get_bboxlog()
    images: list[tuple[float, float, float, float]] = []
    paths = visible_text = 0
    has_text = False
    for name, bbox in log:
        if name in ("fill-image", "fill-imgmask"):
            images.append(tuple(bbox))
        elif name in ("fill-path", "stroke-path", "fill-shade"):
            paths += 1
        elif name in ("fill-text", "stroke-text"):
            visible_text += 1
            has_text = True
        elif name == "ignore-text":
            has_text = True
    cover = _coverage(images, page.rect)
    if cover >= RASTER_COVERAGE and paths <= RASTER_MAX_PATHS and visible_text <= RASTER_MAX_TEXT:
        return PageKind.RASTER, has_text
    if cover < VECTOR_COVERAGE:
        return PageKind.VECTOR, has_text
    return PageKind.MIXED, has_text


def _layers(doc: pymupdf.Document) -> list[Layer]:
    try:
        ocgs = doc.get_ocgs() or {}
    except Exception:
        return []
    return [
        Layer(xref=int(x), name=str(info.get("name", "")), on=bool(info.get("on", True)))
        for x, info in sorted(ocgs.items())
    ]


def analyze_pdf(pdf_path: Path, canon_pages: list[CanonicalPage]) -> list[Page]:
    pages: list[Page] = []
    with pymupdf.open(pdf_path) as doc:
        layers = _layers(doc)
        for cp in canon_pages:
            assert cp.width_mm is not None
            assert cp.height_mm is not None
            pdf_page = doc.load_page(cp.index)
            kind, has_text = classify_pdf_page(pdf_page)
            stats = page_stats(pdf_page) if has_text or kind is not PageKind.RASTER else None
            match = _standard_match(cp.width_mm, cp.height_mm)
            warnings = _size_warnings(
                match, assumed=cp.size_assumed, assumed_paper=ASSUMED_PAPER_ID
            )
            if layers:
                warnings.append(
                    PlanWarning(
                        code=WarningCode.LAYERS_PRESENT,
                        severity=Severity.INFO,
                        params={"count": len(layers)},
                    )
                )
            pages.append(
                Page(
                    index=cp.index,
                    width_mm=cp.width_mm,
                    height_mm=cp.height_mm,
                    boxes_mm=dict(cp.boxes_mm),
                    kind=kind,
                    standard_match=match,
                    layers=list(layers),
                    has_text=has_text,
                    font_min_mm=stats.font_min_mm if stats else None,
                    font_p10_mm=stats.font_p10_mm if stats else None,
                    line_min_mm=stats.line_min_mm if stats else None,
                    detected_scale=stats.detected_scale if stats else None,
                    stats_incomplete=stats.incomplete if stats else False,
                    warnings=warnings,
                )
            )
    return pages


def analyze_document(canon: CanonicalDocument, max_page_mm: float = 10000.0) -> list[Page]:
    """Page models for a canonical document."""
    if canon.kind == "pdf":
        assert canon.pdf_path is not None
        return analyze_pdf(canon.pdf_path, canon.pages)
    return [analyze_raster_page(cp, max_page_mm) for cp in canon.pages]
