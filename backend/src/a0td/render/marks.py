"""Marks, labels and ruler of a tile sheet (SPEC 5.9, 5.8).

Everything is drawn inside the usable rectangle ``C`` of the sheet (so inside the printable area),
with vector primitives and the base-14 Helvetica fonts. Arrows in the neighbour hints are drawn as
small triangles because Helvetica has no arrow glyphs.
"""

from dataclasses import dataclass

import pymupdf

from a0td.domain.geometry import Rect
from a0td.domain.plan import Edge, Plan, SheetPlan
from a0td.domain.settings import MarksSettings
from a0td.domain.units import MM_PER_PT, mm_to_pt
from a0td.render.canvas import (
    Canvas,
    Color,
    Frame,
    dash_pattern,
    parse_color,
    pt_to_mm_size,
    text_width_mm,
)
from a0td.render.strings import get_strings

CUT_DASH_ON_MM = 3.0
CUT_DASH_OFF_MM = 2.0
MIN_STRIP_LABEL_MM = 6.0  # SPEC 5.9: below this the label moves to the corner / band
RULER_MM = 50.0
CORNER_FONT_PT = 6.0
COMPACT_FONT_PT = 5.0
DIAMOND_HALF_MM = 5.0
DIAMOND_FONT_PT = 5.0
SHADING_OPACITY = 0.10
DOC_NAME_MAX = 32

_DIR_OF_EDGE = {Edge.TOP: "up", Edge.RIGHT: "right", Edge.BOTTOM: "down", Edge.LEFT: "left"}

Pt = tuple[float, float]


@dataclass(frozen=True, slots=True)
class MarksContext:
    plan: Plan
    sheet: SheetPlan
    marks: MarksSettings
    doc_name: str = ""
    locale: str = "es"
    band: Rect | None = None  # info band rectangle on the sheet (mm), if any

    @property
    def content(self) -> Rect:
        ox, oy = self.plan.content_origin_mm
        cw, ch = self.plan.content_mm
        return Rect(ox, oy, ox + cw, oy + ch)


# ------------------------------------------------------------------------------- helpers


def _neighbor(ctx: MarksContext, edge: Edge) -> str | None:
    value: str | None = getattr(ctx.sheet.neighbors, edge.value)
    return value


def _fit_text(text: str, size_pt: float, max_mm: float, bold: bool = False) -> str:
    if max_mm <= 0:
        return ""
    if text_width_mm(text, size_pt, bold) <= max_mm:
        return text
    while text and text_width_mm(text + "...", size_pt, bold) > max_mm:
        text = text[:-1]
    return text + "..." if text else ""


def _edge_line(c: Rect, edge: Edge, inset: float) -> tuple[Pt, Pt]:
    if edge is Edge.LEFT:
        return (c.x0 + inset, c.y0), (c.x0 + inset, c.y1)
    if edge is Edge.RIGHT:
        return (c.x1 - inset, c.y0), (c.x1 - inset, c.y1)
    if edge is Edge.TOP:
        return (c.x0, c.y0 + inset), (c.x1, c.y0 + inset)
    return (c.x0, c.y1 - inset), (c.x1, c.y1 - inset)


# ----------------------------------------------------------------- lines and alignment


def _draw_cut_lines(cv: Canvas, ctx: MarksContext, inset: float) -> None:
    c = ctx.content
    dashes = dash_pattern(CUT_DASH_ON_MM, CUT_DASH_OFF_MM)
    edges: list[Edge] = []
    if ctx.marks.cut_lines:
        edges += ctx.sheet.cut_edges
    if ctx.marks.outer_cut_lines:
        edges += [e for e in Edge if _neighbor(ctx, e) is None and e not in edges]
    for edge in edges:
        a, b = _edge_line(c, edge, inset)
        cv.line(a, b, dashes=dashes)


def _draw_alignment(cv: Canvas, ctx: MarksContext, inset: float) -> None:
    c = ctx.content
    o = ctx.plan.overlap_mm
    t = min(2.5, max(1.0, o - 0.8))
    pad = t / 2 + 1.0
    for edge in ctx.sheet.receive_edges:
        if edge is Edge.RIGHT:
            xa = c.x1 - o
            cv.line((xa, c.y0 + inset), (xa, c.y1 - inset))
            for yc in (c.y0 + pad, c.y1 - pad):
                cv.polygon([(xa, yc), (xa + t, yc - t / 2), (xa + t, yc + t / 2)], fill=cv.color)
        elif edge is Edge.BOTTOM:
            ya = c.y1 - o
            cv.line((c.x0 + inset, ya), (c.x1 - inset, ya))
            for xc in (c.x0 + pad, c.x1 - pad):
                cv.polygon([(xc, ya), (xc - t / 2, ya + t), (xc + t / 2, ya + t)], fill=cv.color)


def _draw_glue_shading(cv: Canvas, ctx: MarksContext) -> None:
    c = ctx.content
    o = ctx.plan.overlap_mm
    for edge in ctx.sheet.receive_edges:
        strip = (
            Rect(c.x1 - o, c.y0, c.x1, c.y1)
            if edge is Edge.RIGHT
            else Rect(c.x0, c.y1 - o, c.x1, c.y1)
        )
        cv.rect(strip, fill=cv.color, fill_opacity=SHADING_OPACITY, stroke=False)


def _draw_diamonds(cv: Canvas, ctx: MarksContext, inset: float) -> None:
    """Half diamonds at the midpoint of every interior edge (butt join)."""
    c = ctx.content
    xm, ym = c.center
    a = min(DIAMOND_HALF_MM, c.width / 4, c.height / 4)
    d = a
    size = DIAMOND_FONT_PT
    sid = ctx.sheet.id
    for edge in ctx.sheet.cut_edges:
        other = _neighbor(ctx, edge)
        if other is None:
            continue
        if edge is Edge.LEFT:
            x = c.x0 + inset
            pts = [(x, ym - a), (x, ym + a), (x + d, ym)]
            label, lx, ly = f"{other}|{sid}", x + d + 1.5, ym + pt_to_mm_size(size) * 0.35
        elif edge is Edge.RIGHT:
            x = c.x1 - inset
            pts = [(x, ym - a), (x, ym + a), (x - d, ym)]
            label = f"{sid}|{other}"
            lx = x - d - 1.5 - text_width_mm(label, size)
            ly = ym + pt_to_mm_size(size) * 0.35
        elif edge is Edge.TOP:
            y = c.y0 + inset
            pts = [(xm - a, y), (xm + a, y), (xm, y + d)]
            label, lx, ly = f"{other}|{sid}", xm + a + 1.5, y + pt_to_mm_size(size) + 0.8
        else:
            y = c.y1 - inset
            pts = [(xm - a, y), (xm + a, y), (xm, y - d)]
            label, lx, ly = f"{sid}|{other}", xm + a + 1.5, y - 0.8
        cv.polygon(pts, fill=cv.color)
        if ctx.marks.labels:
            cv.text(lx, ly, label, size)


# ------------------------------------------------------------------------------ labels


def _neighbor_items(ctx: MarksContext) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for edge in (Edge.TOP, Edge.RIGHT, Edge.BOTTOM, Edge.LEFT):
        other = _neighbor(ctx, edge)
        if other is not None:
            items.append((_DIR_OF_EDGE[edge], other))
    return items


def _items_width(items: list[tuple[str, str]], size_pt: float) -> float:
    s = pt_to_mm_size(size_pt)
    return sum(s + 0.8 + text_width_mm(name, size_pt) + 2.5 for _, name in items) - 2.5


def _arrow(direction: str, cx: float, cy: float, r: float) -> list[Pt]:
    if direction == "up":
        return [(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)]
    if direction == "down":
        return [(cx, cy + r), (cx - r, cy - r), (cx + r, cy - r)]
    if direction == "left":
        return [(cx - r, cy), (cx + r, cy - r), (cx + r, cy + r)]
    return [(cx + r, cy), (cx - r, cy - r), (cx - r, cy + r)]


def _draw_items(
    cv: Canvas, fr: Frame, u: float, v: float, items: list[tuple[str, str]], size_pt: float
) -> None:
    """Neighbour hints (arrow + id per neighbour) from ``u`` on, baseline ``v``."""
    s = pt_to_mm_size(size_pt)
    r = s * 0.42
    for direction, name in items:
        cx, cy = fr.to_page(u + s / 2, v - s * 0.33)
        cv.polygon(_arrow(direction, cx, cy, r), fill=cv.color)
        u += s + 0.8
        fr.text(cv, u, v, name, size_pt)
        u += text_width_mm(name, size_pt) + 2.5


def _draw_ruler(cv: Canvas, fr: Frame, u0: float, v_line: float) -> None:
    """50 mm ruler: ticks every mm / 5 mm / 10 mm, numbers every 10 mm, in the frame ``fr``."""
    fr.line(cv, (u0, v_line), (u0 + RULER_MM, v_line))
    for mm in range(int(RULER_MM) + 1):
        ln = 2.2 if mm % 10 == 0 else 1.4 if mm % 5 == 0 else 0.7
        fr.line(cv, (u0 + mm, v_line), (u0 + mm, v_line - ln), width_pt=cv.width_pt * 0.6)
    size = 4.5
    for mm in range(0, int(RULER_MM) + 1, 10):
        text = "50 mm" if mm == int(RULER_MM) else str(mm)
        w = text_width_mm(text, size)
        u = u0 + mm - w / 2 if mm < RULER_MM else u0 + mm - w + 1.0
        fr.text(cv, u, v_line - 2.5, text, size)


def _index_text(sheet: SheetPlan) -> str:
    """Print position k of a sheet, or an em dash when it is not part of the printed run."""
    return "—" if sheet.print_index is None else str(sheet.print_index)


def _draw_compact_label(cv: Canvas, fr: Frame, ctx: MarksContext, width: float) -> None:
    """Sheet ID only (5 pt), hidden under the neighbouring sheet once assembled."""
    size = COMPACT_FONT_PT
    fr.text(cv, 1.5, width / 2 + pt_to_mm_size(size) * 0.36, ctx.sheet.id, size, bold=True)


def _draw_label_block(
    cv: Canvas, fr: Frame, ctx: MarksContext, length: float, width: float, *, ruler: bool
) -> None:
    """ID, "Sheet k/N", doc name and neighbour hints in a strip ``length`` x ``width`` (mm)."""
    texts = get_strings(ctx.locale)
    sheet, plan = ctx.sheet, ctx.plan
    wpt = mm_to_pt(width)
    id_size = min(16.0, max(5.0, 0.6 * wpt))
    small = min(6.5, max(4.0, (wpt - 4.0) / 2.6))
    s_mm = pt_to_mm_size(small)
    items = _neighbor_items(ctx) if ctx.marks.neighbor_hints else []

    u = 2.0
    fr.text(cv, u, width / 2 + pt_to_mm_size(id_size) * 0.36, sheet.id, id_size, bold=True)
    u += text_width_mm(sheet.id, id_size, True) + 3.0

    reserve = 0.0
    ruler_u0 = 0.0
    if ruler:
        reserve = RULER_MM + 4.0
        ruler_u0 = length - 2.0 - RULER_MM - 1.0
        # the alignment triangle of the right edge sits in the last o mm of a bottom strip
        if Edge.RIGHT in sheet.receive_edges:
            shift = plan.overlap_mm + 1.0
            ruler_u0 -= shift
            reserve += shift
    avail = length - u - 2.0 - reserve
    head = f"{texts['sheet']} {_index_text(sheet)}/{plan.counts.printed}"
    if ctx.doc_name:
        head += f"  {ctx.doc_name[:DOC_NAME_MAX]}"
    head = _fit_text(head, small, avail)
    lh = s_mm * 1.3
    if items:
        top = width / 2 - lh
        base1, base2 = top + lh * 0.85, top + lh * 1.95
    else:
        base1, base2 = width / 2 + s_mm * 0.35, 0.0
    if head:
        fr.text(cv, u, base1, head, small)
    if items:
        _draw_items(cv, fr, u, base2, items, small)
    if ruler and ruler_u0 >= u + 20.0:
        _draw_ruler(cv, fr, ruler_u0, width - 1.3)


def _draw_corner_label(cv: Canvas, ctx: MarksContext) -> None:
    """Last sheet (or tiny overlap): 6 pt label in the bottom-right corner of the usable area."""
    texts = get_strings(ctx.locale)
    c = ctx.content
    size = CORNER_FONT_PT
    s_mm = pt_to_mm_size(size)
    head = f"{ctx.sheet.id}  {texts['sheet']} {_index_text(ctx.sheet)}/{ctx.plan.counts.printed}"
    if ctx.doc_name:
        head += f"  {ctx.doc_name[:DOC_NAME_MAX]}"
    head = _fit_text(head, size, c.width - 4.0)
    items = _neighbor_items(ctx) if ctx.marks.neighbor_hints else []
    fr = Frame(0.0, 0.0, 0)
    y2 = c.y1 - 1.3
    if items:
        w2 = _items_width(items, size)
        _draw_items(cv, fr, c.x1 - 1.5 - w2, y2, items, size)
        y1 = y2 - s_mm * 1.3
    else:
        y1 = y2
    w1 = text_width_mm(head, size)
    cv.text(c.x1 - 1.5 - w1, y1, head, size)


def _draw_label(cv: Canvas, ctx: MarksContext) -> None:
    c = ctx.content
    o = ctx.plan.overlap_mm
    receive = ctx.sheet.receive_edges
    if o >= MIN_STRIP_LABEL_MM and Edge.BOTTOM in receive:
        fr = Frame(c.x0, c.y1 - o, 0)
        _draw_label_block(cv, fr, ctx, c.width, o, ruler=ctx.marks.ruler)
    elif o >= MIN_STRIP_LABEL_MM and Edge.RIGHT in receive:
        fr = Frame(c.x1 - o, c.y1, 90)
        _draw_label_block(cv, fr, ctx, c.height, o, ruler=ctx.marks.ruler)
    elif 0 < o < MIN_STRIP_LABEL_MM and Edge.BOTTOM in receive:
        _draw_compact_label(cv, Frame(c.x0, c.y1 - o, 0), ctx, o)
    elif 0 < o < MIN_STRIP_LABEL_MM and Edge.RIGHT in receive:
        _draw_compact_label(cv, Frame(c.x1 - o, c.y1, 90), ctx, o)
    elif ctx.band is not None:
        b = ctx.band
        _draw_label_block(cv, Frame(b.x0, b.y0, 0), ctx, b.width, b.height, ruler=False)
    else:
        _draw_corner_label(cv, ctx)


# -------------------------------------------------------------------------------- entry


def draw_marks(page: pymupdf.Page, ctx: MarksContext, oc: int = 0) -> None:
    """Draw every enabled mark of ``ctx.sheet`` on ``page`` (inside the usable area).

    ``oc`` is the xref of the optional content group (MAR-05) the marks are drawn into.
    """
    m = ctx.marks
    color: Color = parse_color(m.color)
    cv = Canvas(page, color, m.line_width_pt, oc)
    inset = m.line_width_pt * MM_PER_PT / 2

    if m.glue_shading:
        _draw_glue_shading(cv, ctx)
    if m.cut_lines or m.outer_cut_lines:
        _draw_cut_lines(cv, ctx, inset)
    if m.alignment_marks:
        _draw_alignment(cv, ctx, inset)
    if m.join_diamonds and ctx.plan.overlap_mm == 0:
        _draw_diamonds(cv, ctx, inset)
    if m.labels:
        _draw_label(cv, ctx)
    cv.commit()
