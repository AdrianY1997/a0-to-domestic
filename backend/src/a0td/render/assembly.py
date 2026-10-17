"""Assembly plan page (SPEC 5.10, MAR-03, Appendix D).

One extra page of the same paper as the tiles: miniature of the source with the grid, sheet ids and
crossed-out skipped sheets; a summary; 50 mm and 2 in test squares and a 100 mm ruler; and the
printing / assembly instructions. Wide papers use two columns, tall papers a single stack.
"""

from dataclasses import dataclass
from datetime import datetime

import pymupdf

from a0td.domain.geometry import Rect
from a0td.domain.plan import Plan
from a0td.domain.settings import DrawingScale, JobSettings, JoinMethod, PrinterProfile
from a0td.domain.units import MM_PER_INCH, MM_PER_PT
from a0td.render.canvas import Canvas, Color, pt_to_mm_size, text_width_mm
from a0td.render.geometry import PageView, ViewMap, visible_rect
from a0td.render.raster import RasterSource, place_raster
from a0td.render.strings import get_strings
from a0td.render.vector import place_vector
from a0td.version import __version__

BLACK: Color = (0.0, 0.0, 0.0)
INK: Color = (0.1, 0.1, 0.1)
CROSS: Color = (0.75, 0.1, 0.1)
GRID: Color = (0.35, 0.35, 0.35)
GAP_MM = 3.0
TITLE_MM = 11.0
SQUARE_MM = 50.0
SQUARE_IN_MM = 2 * MM_PER_INCH
TEST_GAP_MM = 6.0
RULER_MM = 100.0
MIN_MINIATURE_MM = 40.0
INSTRUCTION_FONTS = (7.0, 6.5, 6.0, 5.5, 5.0)
SUMMARY_FONT_PT = 8.0
MINIATURE_DPI = 150.0


@dataclass(slots=True)
class AssemblySource:
    """Where the miniature takes its pixels from (one of both is set)."""

    vector: pymupdf.Document | None = None
    pno: int = 0
    raster: RasterSource | None = None  # opened with ``sequential=True``


@dataclass(frozen=True, slots=True)
class _Line:
    x: float
    text: str
    bold: bool
    gap_before: float = 0.0


# --------------------------------------------------------------------------------- text


def _wrap(text: str, size_pt: float, width_mm: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else word
        if cur and text_width_mm(trial, size_pt, bold) > width_mm:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _paragraph_lines(
    paragraphs: list[tuple[str, str]], size_pt: float, width_mm: float
) -> tuple[list[_Line], float]:
    """Lay out ``(kind, text)`` paragraphs (``h`` heading, ``n`` hanging item) -> lines, height."""
    lh = pt_to_mm_size(size_pt) * 1.28
    lines: list[_Line] = []
    height = 0.0
    for kind, text in paragraphs:
        bold = kind == "h"
        gap = 1.2 if kind == "h" and lines else 0.4 if lines else 0.0
        indent = 3.2 if kind == "n" else 0.0
        wrapped = _wrap(text, size_pt, width_mm - indent, bold)
        for i, ln in enumerate(wrapped):
            lines.append(
                _Line(indent if (kind == "n" and i > 0) else 0.0, ln, bold, gap if i == 0 else 0.0)
            )
        height += gap + lh * len(wrapped)
    return lines, height


def _draw_lines(cv: Canvas, lines: list[_Line], x: float, y: float, size_pt: float) -> None:
    lh = pt_to_mm_size(size_pt) * 1.28
    for ln in lines:
        y += ln.gap_before
        cv.text(x + ln.x, y + lh * 0.8, ln.text, size_pt, bold=ln.bold, color=INK)
        y += lh


def _instructions(plan: Plan, texts: dict[str, str], butt: bool) -> list[tuple[str, str]]:
    orient = texts[plan.orientation.value]
    paper = f"{plan.paper.id} ({orient})"
    items: list[tuple[str, str]] = [
        ("h", texts["print_title"]),
        ("n", "1. " + texts["print_1"]),
        ("n", "2. " + texts["print_2"].format(paper=paper)),
        ("n", "3. " + texts["print_3"]),
    ]
    if butt:
        items += [("h", texts["butt_title"]), ("p", texts["butt_1"])]
    else:
        items += [("h", texts["overlap_title"])]
        items += [("n", f"{i}. " + texts[f"overlap_{i}"]) for i in range(1, 5)]
    return items


def _summary(
    plan: Plan,
    settings: JobSettings,
    printer: PrinterProfile,
    texts: dict[str, str],
    doc_name: str,
    page_count: int,
    now: datetime,
) -> list[tuple[str, str]]:
    sheets = plan.sheets
    blank = sum(1 for s in sheets if s.selected and s.print_index is None)
    off = sum(1 for s in sheets if not s.selected)
    orient = texts[plan.orientation.value]
    scale = plan.scale_label
    if isinstance(settings.scale, DrawingScale):
        scale = f"{scale} ({plan.scale * 100:.2f} %)"
    document = doc_name
    if page_count > 1:
        document += f" ({texts['sum_page']} {plan.page_index + 1}/{page_count})"
    join = (
        texts["join_butt"]
        if plan.overlap_mm == 0
        else texts["join_overlap"].format(o=plan.overlap_mm)
    )
    printer_txt = printer.name
    if printer.scale_kx != 1.0 or printer.scale_ky != 1.0:
        printer_txt += " - " + texts["correction"].format(kx=printer.scale_kx, ky=printer.scale_ky)
    fw, fh = plan.assembled_size_mm
    return [
        (texts["sum_document"], document),
        (
            texts["sum_paper"],
            f"{plan.paper.id} {orient} ({plan.paper.width_mm:g} x {plan.paper.height_mm:g} mm)",
        ),
        (texts["sum_scale"], scale),
        (texts["sum_grid"], f"{plan.grid.cols} x {plan.grid.rows} = {plan.counts.total}"),
        (
            texts["sum_sheets"],
            texts["sheets_detail"].format(printed=plan.counts.printed, blank=blank, off=off),
        ),
        (texts["sum_final"], f"{fw:.1f} x {fh:.1f} mm"),
        (texts["sum_join"], join),
        (texts["sum_printer"], printer_txt),
        (texts["sum_generated"], f"{now:%Y-%m-%d} - a0-to-domestic {__version__}"),
    ]


def _summary_height(n: int, size_pt: float = SUMMARY_FONT_PT) -> float:
    return n * pt_to_mm_size(size_pt) * 1.4 + 1.0


def _draw_summary(
    cv: Canvas, rows: list[tuple[str, str]], x: float, y: float, width: float
) -> None:
    size = SUMMARY_FONT_PT
    lh = pt_to_mm_size(size) * 1.4
    label_w = max(text_width_mm(f"{k}:", size, True) for k, _ in rows) + 2.0
    for i, (key, value) in enumerate(rows):
        base = y + lh * (i + 0.8)
        cv.text(x, base, f"{key}:", size, bold=True, color=INK)
        fitted = value
        while fitted and text_width_mm(fitted, size) > width - label_w:
            fitted = fitted[:-1]
        if fitted != value:
            fitted = fitted[:-3] + "..."
        cv.text(x + label_w, base, fitted, size, color=INK)


# ----------------------------------------------------------------------------- tests


def _tests_size(width: float) -> tuple[float, bool]:
    """Height of the test block and whether both squares fit side by side."""
    side_by_side = width >= SQUARE_MM + TEST_GAP_MM + SQUARE_IN_MM
    squares = SQUARE_IN_MM if side_by_side else SQUARE_MM + TEST_GAP_MM + SQUARE_IN_MM
    return squares + 5.0 + 11.0, side_by_side


def _draw_tests(cv: Canvas, texts: dict[str, str], x: float, y: float, width: float) -> None:
    """50 mm and 2 in squares (black 0.5 pt) and a 100 mm ruler, to check the printed scale."""
    _, side_by_side = _tests_size(width)
    w = 0.5
    sq1 = Rect(x, y, x + SQUARE_MM, y + SQUARE_MM)
    if side_by_side:
        sq2 = Rect(sq1.x1 + TEST_GAP_MM, y, sq1.x1 + TEST_GAP_MM + SQUARE_IN_MM, y + SQUARE_IN_MM)
    else:
        sq2 = Rect(x, sq1.y1 + TEST_GAP_MM, x + SQUARE_IN_MM, sq1.y1 + TEST_GAP_MM + SQUARE_IN_MM)
    for sq, label in ((sq1, texts["test_square_50"]), (sq2, texts["test_square_2in"])):
        cv.rect(sq, color=BLACK, width_pt=w)
        cv.text(sq.x0 + 2.0, sq.y0 + 4.5, label, 6.5, color=INK)
    y_cap = max(sq1.y1, sq2.y1) + 3.5
    cv.text(x, y_cap, texts["test_caption"], 6.0, color=INK)
    if width >= RULER_MM + 4.0:
        y_r = y_cap + 8.5
        cv.line((x, y_r), (x + RULER_MM, y_r), color=BLACK, width_pt=w)
        for mm in range(int(RULER_MM) + 1):
            ln = 4.0 if mm % 10 == 0 else 2.5 if mm % 5 == 0 else 1.5
            cv.line((x + mm, y_r), (x + mm, y_r - ln), color=BLACK, width_pt=0.4)
        for mm in range(0, int(RULER_MM) + 1, 10):
            label = "100 mm" if mm == int(RULER_MM) else str(mm)
            lw = text_width_mm(label, 5.0)
            lx = x + mm - lw / 2 if mm < RULER_MM else x + mm - lw
            cv.text(lx, y_r - 4.6, label, 5.0, color=INK)


def draw_test_block(page: pymupdf.Page, area: Rect, locale: str) -> bool:
    """Test squares and ruler over a white backing at the top-left of ``area`` (MAR-04).

    Returns ``False`` (nothing drawn) when the block does not fit.
    """
    width = min(area.width, SQUARE_MM + TEST_GAP_MM + SQUARE_IN_MM + 2.0)
    height, _ = _tests_size(width)
    if width < SQUARE_MM + 2.0 or height > area.height:
        return False
    cv = Canvas(page, BLACK, 0.5)
    cv.rect(
        Rect(area.x0, area.y0, area.x0 + width, area.y0 + height),
        fill=(1.0, 1.0, 1.0),
        stroke=False,
    )
    _draw_tests(cv, get_strings(locale), area.x0 + 1.0, area.y0 + 1.0, width - 2.0)
    cv.commit()
    return True


# -------------------------------------------------------------------------- miniature


def _draw_miniature(
    page: pymupdf.Page,
    cv: Canvas,
    area: Rect,
    plan: Plan,
    pv: PageView,
    source: AssemblySource,
    mark_color: Color,
    jpeg_quality: int,
) -> None:
    kx, ky = plan.coverage_mm
    gx, gy = plan.origin_mm
    s = plan.scale
    m = min(area.width / kx, area.height / ky, 1.0)
    x0 = area.center[0] - kx * m / 2
    y0 = area.center[1] - ky * m / 2
    region = Rect(*plan.source_rect_mm)
    vmap = ViewMap(x0 - gx * m, y0 - gy * m, region.x0, region.y0, s * m)

    vis = visible_rect(region, region, pv.page_view)
    if vis is not None:
        if source.vector is not None:
            place_vector(page, source.vector, source.pno, pv.transform, vis, vmap.rect(vis))
        elif source.raster is not None:
            place_raster(
                page,
                source.raster,
                pv.transform,
                vis,
                vmap,
                max_dpi=MINIATURE_DPI,
                jpeg_quality=jpeg_quality,
            )
    cv.rect(vmap.rect(region), color=BLACK, width_pt=0.5)

    cw, ch = plan.content_mm
    dx, dy = plan.step_mm
    for sh in plan.sheets:
        tile = Rect.from_xywh(x0 + (sh.col * dx) * m, y0 + (sh.row * dy) * m, cw * m, ch * m)
        # tiles are positioned from the grid origin: (gx + col*dx) - gx == col*dx
        printed = sh.print_index is not None
        cv.rect(tile, color=mark_color if printed else CROSS, width_pt=0.4)
        if not printed:
            cv.line((tile.x0, tile.y0), (tile.x1, tile.y1), color=CROSS, width_pt=0.5)
            cv.line((tile.x0, tile.y1), (tile.x1, tile.y0), color=CROSS, width_pt=0.5)
        size = min(14.0, 0.3 * min(tile.width, tile.height) / MM_PER_PT)
        if size >= 3.5:
            tw = text_width_mm(sh.id, size, True)
            cx, cy = tile.center
            halo = Rect(cx - tw / 2 - 0.8, cy - pt_to_mm_size(size) * 0.8,
                        cx + tw / 2 + 0.8, cy + pt_to_mm_size(size) * 0.35)  # fmt: skip
            cv.rect(halo, fill=(1.0, 1.0, 1.0), fill_opacity=0.75, stroke=False)
            cv.text(
                cx - tw / 2,
                cy + pt_to_mm_size(size) * 0.3,
                sh.id,
                size,
                bold=True,
                color=INK if printed else CROSS,
            )


# ------------------------------------------------------------------------------ page


def draw_assembly_page(
    page: pymupdf.Page,
    *,
    plan: Plan,
    settings: JobSettings,
    printer: PrinterProfile,
    pv: PageView,
    source: AssemblySource,
    doc_name: str,
    page_count: int,
    locale: str,
    now: datetime,
) -> None:
    """Draw the assembly page on ``page`` (already created with the sheets' paper size)."""
    texts = get_strings(locale)
    m = plan.margins_mm
    area = Rect(m.left, m.top, plan.paper.width_mm - m.right, plan.paper.height_mm - m.bottom)
    cv = Canvas(page, BLACK, 0.5)
    mark_color = GRID
    butt = settings.tiling.join is JoinMethod.BUTT

    # title
    cv.text(area.x0, area.y0 + 5.0, texts["assembly_title"], 14.0, bold=True, color=INK)
    sub = doc_name if page_count <= 1 else f"{doc_name} ({plan.page_index + 1}/{page_count})"
    cv.text(area.x0, area.y0 + 9.0, sub, 8.0, color=INK)
    top = area.y0 + TITLE_MM

    rows = _summary(plan, settings, printer, texts, doc_name, page_count, now)
    instr = _instructions(plan, texts, butt)
    summary_h = _summary_height(len(rows))
    quality = settings.output.jpeg_quality
    wide = area.width > area.height * 1.25

    if wide:
        right_w = min(SQUARE_MM + TEST_GAP_MM + SQUARE_IN_MM + 6.0, area.width * 0.45)
        rx = area.x1 - right_w
        mini = Rect(area.x0, top + GAP_MM, rx - GAP_MM, area.y1)
        tests_h, _ = _tests_size(right_w)
        free = area.y1 - top - summary_h - tests_h - 3 * GAP_MM
        col_w, col_x = right_w, rx
    else:
        col_w, col_x = area.width, area.x0
        tests_h, _ = _tests_size(col_w)
        free = area.y1 - top - summary_h - tests_h - 4 * GAP_MM - MIN_MINIATURE_MM

    # instruction font: the largest that fits the free height (else none)
    chosen: tuple[float, list[_Line], float] | None = None
    for size in INSTRUCTION_FONTS:
        lines, h = _paragraph_lines(instr, size, col_w)
        if h <= free:
            chosen = (size, lines, h)
            break

    y = top + GAP_MM
    if wide:
        _draw_miniature(page, cv, mini, plan, pv, source, mark_color, quality)
        _draw_summary(cv, rows, col_x, y, col_w)
        y += summary_h + GAP_MM
        _draw_tests(cv, texts, col_x, y, col_w)
        y += tests_h + GAP_MM
        if chosen is not None:
            _draw_lines(cv, chosen[1], col_x, y, chosen[0])
    else:
        used = summary_h + tests_h + 3 * GAP_MM + (chosen[2] + GAP_MM if chosen else 0.0)
        mini_h = max(MIN_MINIATURE_MM, area.y1 - y - used)
        mini = Rect(area.x0, y, area.x1, y + mini_h)
        _draw_miniature(page, cv, mini, plan, pv, source, mark_color, quality)
        y = mini.y1 + GAP_MM
        _draw_summary(cv, rows, col_x, y, col_w)
        y += summary_h + GAP_MM
        _draw_tests(cv, texts, col_x, y, col_w)
        y += tests_h + GAP_MM
        if chosen is not None:
            _draw_lines(cv, chosen[1], col_x, y, chosen[0])
    cv.commit()
