"""Tiling planner (SPEC 5): a pure function from page geometry + settings to a :class:`Plan`.

No I/O. All coordinates of the result are in view space (SPEC 5.1/5.2) unless stated otherwise.

Notes on scope:

* ``source.mirror`` flips the view horizontally (:class:`ViewTransform`), so occupancy follows it.
* ``target.landscape_as_rotated_portrait`` only changes the margins of landscape frames (exact,
  rotated 90 degrees clockwise instead of the safe ones); the render rotates the finished sheet.
* ``target.roll_mm`` (roll / banner paper): portrait only, the grid has one column per roll width
  and, when the strip fits in a single sheet, the sheet is as long as needed (never more than
  ``max_length``).
* The occupancy map is expressed in the canonical page frame; the planner maps view-space
  rectangles into it (see :mod:`a0td.domain.occupancy`).
"""

from dataclasses import dataclass
from typing import NamedTuple

from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.domain.geometry import Rect
from a0td.domain.legibility import legibility_warnings
from a0td.domain.naming import SheetOrder, sheet_id
from a0td.domain.occupancy import OccupancyMap
from a0td.domain.page import PageGeometry, ViewTransform
from a0td.domain.papers import Orientation, PaperId, get_paper, oriented
from a0td.domain.plan import (
    Alternative,
    Counts,
    Edge,
    Grid,
    Neighbors,
    PaperRef,
    Plan,
    PlanWarning,
    Range2,
    Saving,
    SheetPlan,
    Vec2,
)
from a0td.domain.settings import (
    MAX_SCALE,
    MIN_OVERLAP_MM,
    MIN_SCALE,
    Align,
    DrawingScale,
    FinalSizeScale,
    FitCountScale,
    FitGridScale,
    FitSheetScale,
    InfoBandPosition,
    JobSettings,
    JoinMethod,
    Margins,
    PercentScale,
    PrinterProfile,
    ScaleSettings,
    SourceBox,
    get_builtin_printer,
)
from a0td.domain.units import MM_PER_INCH, ceil_eps, floor_eps

MIN_CONTENT_MM = 30.0  # SPEC 5.3: smallest usable area per axis
OVERLAP_HEADROOM_MM = 20.0  # SPEC 5.3: o <= min(C) - 20
AUTO_TRIM_PAD_MM = 2.0  # SPEC 5.2 step 5
LOW_DPI = 150.0  # SPEC ANA-04
VERY_LOW_DPI = 100.0
MANY_SHEETS_THRESHOLD = 50  # SPEC 10: MANY_SHEETS when printed sheets > 50
DEFAULT_MAX_SHEETS = 500  # A0TD_MAX_SHEETS
MAX_GRID_CELLS = 20_000  # hard guard so absurd scales never build huge grids
SAVINGS_FLOOR = 0.8  # SPEC 5.11: thresholds down to 80 % of the current scale
MAX_SAVINGS = 3
_REL_TIE = 1e-9
_ORIENTATIONS = (Orientation.PORTRAIT, Orientation.LANDSCAPE)


class Affine(NamedTuple):
    """PDF-style affine matrix: ``x' = a*x + c*y + e``, ``y' = b*x + d*y + f``."""

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)


def printer_correction_matrix(page_w: float, page_h: float, kx: float, ky: float) -> Affine:
    """Printer calibration correction (SPEC 5.5).

    ``k = measured / nominal`` per axis. The whole sheet (content and marks) is scaled by
    ``1/kx, 1/ky`` around the page centre. ``page_w``/``page_h`` and the translation share a unit
    (mm or pt) chosen by the caller.
    """
    if kx <= 0 or ky <= 0:
        raise ValueError("calibration factors must be > 0")
    sx, sy = 1.0 / kx, 1.0 / ky
    cx, cy = page_w / 2.0, page_h / 2.0
    return Affine(sx, 0.0, 0.0, sy, cx * (1.0 - sx), cy * (1.0 - sy))


# ----------------------------------------------------------------------------- helpers


def _z(x: float) -> float:
    """Normalize ``-0.0`` to ``0.0``."""
    return x + 0.0


def _fmt_percent(s: float) -> str:
    return f"{s * 100:.2f}".rstrip("0").rstrip(".") + " %"


def scale_label(scale: ScaleSettings, s: float) -> str:
    """``"100 %"`` for most modes, ``"1:100"`` for a drawing scale."""
    if isinstance(scale, DrawingScale):
        return f"1:{scale.to_denominator:g}"
    return _fmt_percent(s)


def _invalid(field: str, message: str) -> DomainError:
    return DomainError(
        ErrorCode.INVALID_SETTINGS,
        message,
        errors=[{"loc": field, "message": message}],
    )


# --------------------------------------------------------------------------- source


class _ViewOccupancy:
    """Occupancy queried in view space (maps to the canonical frame underneath)."""

    def __init__(self, occ: OccupancyMap, tf: ViewTransform) -> None:
        self._occ = occ
        self._tf = tf

    def any_in(self, rect: Rect) -> bool:
        return self._occ.any_in(self._tf.to_canonical(rect))

    def content_bbox(self) -> Rect | None:
        bb = self._occ.content_bbox()
        return None if bb is None else self._tf.to_view(bb)


@dataclass(frozen=True, slots=True)
class _Source:
    view: Rect  # whole page in view space (origin 0,0)
    region: Rect  # R
    occ: _ViewOccupancy | None
    warnings: tuple[PlanWarning, ...]


def _resolve_source(
    page: PageGeometry, settings: JobSettings, occupancy: OccupancyMap | None
) -> _Source:
    src = settings.source
    warnings: list[PlanWarning] = []
    box = page.box(src.box)
    if box is None:
        box = page.box(SourceBox.CROP)
        assert box is not None  # crop always resolves
        warnings.append(
            PlanWarning(code=WarningCode.BOX_FALLBACK, params={"requested": src.box.value})
        )
    tf = ViewTransform(box, src.rotate, src.mirror)
    vw, vh = tf.view_size
    view = Rect(0.0, 0.0, vw, vh)
    occ = None if occupancy is None else _ViewOccupancy(occupancy, tf)

    region = view
    if src.region_mm is not None:
        r = src.region_mm
        clipped = Rect.from_xywh(r.x, r.y, r.width, r.height).intersect(view)
        if clipped is None:
            raise _invalid("source.region_mm", "region lies outside the page")
        region = clipped
    if src.auto_trim and occ is not None:
        bbox = occ.content_bbox()
        if bbox is not None:
            trimmed = bbox.inflate(AUTO_TRIM_PAD_MM).intersect(region)
            if trimmed is not None:
                region = trimmed
    return _Source(view, region, occ, tuple(warnings))


# ----------------------------------------------------------------------------- sheet


@dataclass(frozen=True, slots=True)
class _Frame:
    """Paper + margins + usable area for one orientation."""

    orientation: Orientation
    paper: Vec2  # oriented
    margins: Margins  # effective
    content: Vec2  # C
    origin: Vec2  # where the usable rectangle starts on the sheet


def _make_frame(
    paper_portrait: Vec2,
    margins: Margins,
    orientation: Orientation,
    band_mm: float,
    band_top: bool,
    overlap: float,
    butt: bool,
    rotated_portrait: bool = False,
    fixed_width: bool = False,
) -> _Frame:
    if fixed_width:  # roll: the physical width is fixed, never reorder (width, length)
        pw, ph = paper_portrait
    else:
        pw, ph = oriented(paper_portrait[0], paper_portrait[1], orientation)
    if rotated_portrait and orientation is Orientation.LANDSCAPE:
        m = margins.rotated_cw()  # exact: the sheet is printed as a portrait page
    else:
        m = margins.for_orientation(orientation)
    cw = pw - m.left - m.right
    ch = ph - m.top - m.bottom - band_mm
    if cw < MIN_CONTENT_MM - 1e-9 or ch < MIN_CONTENT_MM - 1e-9:
        raise DomainError(
            ErrorCode.CONTENT_AREA_TOO_SMALL,
            f"usable area {cw:.1f} x {ch:.1f} mm is below {MIN_CONTENT_MM:g} mm",
            {"content_mm": [cw, ch], "min_mm": MIN_CONTENT_MM, "orientation": orientation.value},
        )
    if not butt:
        max_o = min(cw, ch) - OVERLAP_HEADROOM_MM
        if overlap < MIN_OVERLAP_MM - 1e-9:
            raise _invalid("tiling.overlap_mm", f"overlap must be >= {MIN_OVERLAP_MM:g} mm")
        if overlap > max_o + 1e-9:
            raise DomainError(
                ErrorCode.OVERLAP_TOO_LARGE,
                f"overlap {overlap:g} mm exceeds the maximum {max_o:.1f} mm for this sheet",
                {"overlap_mm": overlap, "max_mm": max_o, "orientation": orientation.value},
            )
    origin_y = m.top + (band_mm if band_top else 0.0)
    return _Frame(orientation, (pw, ph), m, (cw, ch), (m.left, origin_y))


# ----------------------------------------------------------------------------- layout


@dataclass(frozen=True, slots=True)
class _Axis:
    n: int
    step: float
    target: float
    coverage: float
    slack: float
    origin: float
    offset_range: Vec2


def _axis(target: float, content: float, overlap: float, align: Align, delta: float) -> _Axis:
    step = content - overlap
    n = max(1, ceil_eps((target - overlap) / step))
    coverage = n * step + overlap
    slack = max(0.0, coverage - target)
    g0 = {Align.START: 0.0, Align.CENTER: -slack / 2.0, Align.END: -slack}[align]
    origin = min(0.0, max(-slack, g0 + delta))
    return _Axis(n, step, target, coverage, slack, _z(origin), (_z(-slack - g0), _z(-g0)))


@dataclass(frozen=True, slots=True)
class _Layout:
    x: _Axis
    y: _Axis

    @property
    def cols(self) -> int:
        return self.x.n

    @property
    def rows(self) -> int:
        return self.y.n

    @property
    def cells(self) -> int:
        return self.x.n * self.y.n


class _Ctx(NamedTuple):
    """Everything about the source and tiling choices that does not depend on the paper."""

    size: Vec2  # S
    region: Rect
    occ: _ViewOccupancy | None
    overlap: float
    butt: bool
    settings: JobSettings


def _layout(ctx: _Ctx, frame: _Frame, s: float) -> _Layout:
    til = ctx.settings.tiling
    tw, th = ctx.size[0] * s, ctx.size[1] * s
    cw, ch = frame.content
    return _Layout(
        _axis(tw, cw, ctx.overlap, til.align.x, til.offset_mm.x),
        _axis(th, ch, ctx.overlap, til.align.y, til.offset_mm.y),
    )


def _tile_src(ctx: _Ctx, frame: _Frame, layout: _Layout, s: float, r: int, c: int) -> Rect:
    """Source rectangle (view space) of tile ``(r, c)`` (SPEC 5.5)."""
    x = layout.x.origin + c * layout.x.step
    y = layout.y.origin + r * layout.y.step
    cw, ch = frame.content
    x0, y0 = ctx.region.x0, ctx.region.y0
    return Rect(x0 + x / s, y0 + y / s, x0 + (x + cw) / s, y0 + (y + ch) / s)


def _blank_grid(ctx: _Ctx, frame: _Frame, layout: _Layout, s: float) -> list[list[bool]] | None:
    """``blank[r][c]``: no content in the tile's full source rectangle (overlap included)."""
    occ = ctx.occ
    if occ is None or layout.cells > MAX_GRID_CELLS:
        return None
    grid: list[list[bool]] = []
    for r in range(layout.rows):
        row: list[bool] = []
        for c in range(layout.cols):
            inter = _tile_src(ctx, frame, layout, s, r, c).intersect(ctx.region)
            row.append(inter is None or not occ.any_in(inter))
        grid.append(row)
    return grid


@dataclass(frozen=True, slots=True)
class _Candidate:
    frame: _Frame
    scale: float
    layout: _Layout
    blank: list[list[bool]] | None
    printed: int

    @property
    def coverage_area(self) -> float:
        return self.layout.x.coverage * self.layout.y.coverage


def _fit_count_scale(ctx: _Ctx, frame: _Frame, n: int) -> float:
    """Best ``fit_grid(c, r)`` over all ``c * r <= n`` (``r = n // c`` is optimal for each c)."""
    sw, sh = ctx.size
    o = ctx.overlap
    dw, dh = frame.content[0] - o, frame.content[1] - o
    return max(min((c * dw + o) / sw, ((n // c) * dh + o) / sh) for c in range(1, n + 1))


def _scale_for(ctx: _Ctx, frame: _Frame) -> float:
    sc = ctx.settings.scale
    sw, sh = ctx.size
    cw, ch = frame.content
    o = ctx.overlap
    match sc:
        case PercentScale():
            return sc.percent / 100.0
        case DrawingScale():
            return sc.factor
        case FinalSizeScale():
            if sc.width_mm is not None:
                return sc.width_mm / sw
            assert sc.height_mm is not None
            return sc.height_mm / sh
        case FitSheetScale():
            return min(cw / sw, ch / sh)
        case FitGridScale():
            return min((sc.cols * (cw - o) + o) / sw, (sc.rows * (ch - o) + o) / sh)
        case FitCountScale():
            return _fit_count_scale(ctx, frame, sc.max_sheets)
    raise AssertionError("unreachable")  # pragma: no cover


def _evaluate(
    ctx: _Ctx, frame: _Frame, fixed_scale: float | None = None, *, use_occ: bool = True
) -> _Candidate:
    if fixed_scale is None:
        s = _scale_for(ctx, frame)
        if not (MIN_SCALE * (1 - 1e-12) <= s <= MAX_SCALE * (1 + 1e-12)):
            raise DomainError(
                ErrorCode.SCALE_OUT_OF_RANGE,
                f"scale {s:.4f} is outside {MIN_SCALE:g}-{MAX_SCALE:g}",
                {"scale": s, "min": MIN_SCALE, "max": MAX_SCALE},
            )
        s = min(MAX_SCALE, max(MIN_SCALE, s))
    else:
        s = fixed_scale
    layout = _layout(ctx, frame, s)
    blank = _blank_grid(ctx, frame, layout, s) if use_occ else None
    n_blank = sum(map(sum, blank)) if blank is not None else 0
    printed = layout.cells - (n_blank if ctx.settings.tiling.skip_blank else 0)
    return _Candidate(frame, s, layout, blank, printed)


def _better(ctx: _Ctx, new: _Candidate, best: _Candidate) -> bool:
    """Orientation preference (SPEC 5.4 / 5.6); portrait is evaluated first and wins ties."""
    if isinstance(ctx.settings.scale, FitSheetScale | FitGridScale | FitCountScale):
        if abs(new.scale - best.scale) > _REL_TIE * max(new.scale, best.scale):
            return new.scale > best.scale
        return new.printed < best.printed
    if new.printed != best.printed:
        return new.printed < best.printed
    return new.coverage_area < best.coverage_area - 1e-6


# ------------------------------------------------------------------- alternatives & savings


def _alternatives(
    ctx: _Ctx,
    cur: _Candidate,
    cur_paper: str,
    cur_portrait: Vec2,
    printer: PrinterProfile,
    band_mm: float,
    band_top: bool,
) -> list[Alternative]:
    roll = ctx.settings.target.roll_mm is not None
    rotated = ctx.settings.target.landscape_as_rotated_portrait
    entries: list[tuple[str, Vec2]] = []
    if not roll:  # a roll has no other papers to compare with
        for pid in printer.supported_papers:
            if pid is PaperId.CUSTOM:
                continue
            p = get_paper(pid.value)
            entries.append((pid.value, (p.width_mm, p.height_mm)))
    if cur_paper not in {e[0] for e in entries}:
        entries.insert(0, (cur_paper, cur_portrait))
    out: list[Alternative] = []
    explicit = ctx.settings.target.margins_mm
    for key, size in entries:
        margins = explicit if explicit is not None else _margins_of(printer, key)
        for orient in _ORIENTATIONS[:1] if roll else _ORIENTATIONS:
            try:
                frame = _make_frame(
                    size, margins, orient, band_mm, band_top, ctx.overlap, ctx.butt, rotated, roll
                )
            except DomainError:
                continue
            cand = _evaluate(ctx, frame, cur.scale)
            out.append(
                Alternative(
                    paper_id=key,
                    orientation=orient,
                    cols=cand.layout.cols,
                    rows=cand.layout.rows,
                    total=cand.layout.cells,
                    sheets=cand.printed,
                    current=key == cur_paper and orient is cur.frame.orientation,
                )
            )
    out.sort(key=lambda a: a.sheets)  # stable: catalog order breaks ties
    return out


def _savings(ctx: _Ctx, frames: list[_Frame], cur: _Candidate) -> list[Saving]:
    """Pareto-optimal threshold scales (<= current, >= 80 % of it) that need fewer sheets."""
    use_occ = cur.layout.cells <= 400
    base = cur.printed if use_occ else cur.layout.cells
    s_cur = cur.scale
    found: list[Saving] = []
    for frame in frames:
        for axis in (0, 1):
            size = ctx.size[axis]
            step = frame.content[axis] - ctx.overlap
            n_lo = max(1, ceil_eps((SAVINGS_FLOOR * s_cur * size - ctx.overlap) / step))
            n_hi = floor_eps((s_cur * size - ctx.overlap) / step)
            for n in range(n_lo, n_hi + 1):
                s = (n * step + ctx.overlap) / size
                if s >= s_cur * (1 - _REL_TIE) or s < SAVINGS_FLOOR * s_cur * (1 - _REL_TIE):
                    continue
                if s < MIN_SCALE:
                    continue
                cand = _evaluate(ctx, frame, s, use_occ=use_occ)
                count = cand.printed if use_occ else cand.layout.cells
                if count < base:
                    found.append(
                        Saving(
                            scale=s,
                            scale_label=_fmt_percent(s),
                            orientation=frame.orientation,
                            cols=cand.layout.cols,
                            rows=cand.layout.rows,
                            sheets=count,
                            saved=base - count,
                        )
                    )
    found.sort(key=lambda sv: (-sv.scale, sv.sheets))
    pareto: list[Saving] = []
    for sv in found:
        if not pareto or sv.sheets < pareto[-1].sheets:
            pareto.append(sv)
    return pareto[:MAX_SAVINGS]


# ------------------------------------------------------------------------------ build


def _edges(r: int, c: int, rows: int, cols: int, butt: bool) -> tuple[list[Edge], list[Edge]]:
    """``(cut_edges, receive_edges)`` for the join method (SPEC 5.8)."""
    cut: list[Edge] = []
    receive: list[Edge] = []
    if r > 0:
        cut.append(Edge.TOP)
    if c < cols - 1:
        (cut if butt else receive).append(Edge.RIGHT)
    if r < rows - 1:
        (cut if butt else receive).append(Edge.BOTTOM)
    if c > 0:
        cut.append(Edge.LEFT)
    return cut, receive


def _build_sheets(
    ctx: _Ctx, cand: _Candidate, page: PageGeometry
) -> tuple[list[SheetPlan], Counts]:
    til = ctx.settings.tiling
    rows, cols = cand.layout.rows, cand.layout.cols

    def ident(r: int, c: int, page_count: int) -> str:
        return sheet_id(
            r,
            c,
            naming=til.naming,
            rows=rows,
            cols=cols,
            order=til.order,
            page_number=page.index + 1,
            page_count=page_count,
        )

    ids = [[ident(r, c, page.page_count) for c in range(cols)] for r in range(rows)]
    wanted = None if til.only_sheets is None else {s.strip().upper() for s in til.only_sheets}

    order = (
        [(r, c) for r in range(rows) for c in range(cols)]
        if til.order is SheetOrder.ROWS
        else [(r, c) for c in range(cols) for r in range(rows)]
    )
    sheets: list[SheetPlan] = []
    printed = blank_sel = deselected = 0
    for r, c in order:
        sid = ids[r][c]
        selected = wanted is None or sid.upper() in wanted or ident(r, c, 1).upper() in wanted
        is_blank = bool(cand.blank[r][c]) if cand.blank is not None else False
        will_print = selected and not (is_blank and til.skip_blank)
        if will_print:
            printed += 1
        if not selected:
            deselected += 1
        elif is_blank:
            blank_sel += 1
        cut, receive = _edges(r, c, rows, cols, ctx.butt)
        src = _tile_src(ctx, cand.frame, cand.layout, cand.scale, r, c)
        sheets.append(
            SheetPlan(
                id=sid,
                row=r,
                col=c,
                print_index=printed if will_print else None,
                src_rect_mm=(src.x0, src.y0, src.x1, src.y1),
                blank=is_blank,
                selected=selected,
                neighbors=Neighbors(
                    top=ids[r - 1][c] if r > 0 else None,
                    right=ids[r][c + 1] if c < cols - 1 else None,
                    bottom=ids[r + 1][c] if r < rows - 1 else None,
                    left=ids[r][c - 1] if c > 0 else None,
                ),
                cut_edges=cut,
                receive_edges=receive,
            )
        )
    counts = Counts(total=rows * cols, printed=printed, blank=blank_sel, deselected=deselected)
    return sheets, counts


def _effective_dpi(page: PageGeometry, s: float) -> float | None:
    if page.px is None:
        return None
    px_per_mm = min(page.px[0] / page.width_mm, page.px[1] / page.height_mm)
    return px_per_mm * MM_PER_INCH / s


ROLL_ID = "ROLL"


def _resolve_paper(settings: JobSettings) -> tuple[str, Vec2]:
    """``(paper id, portrait size)``; a roll is ``("ROLL", (width, max_length))``."""
    tgt = settings.target
    if tgt.roll_mm is not None:
        return ROLL_ID, (tgt.roll_mm.width, tgt.roll_mm.max_length)
    if tgt.paper is PaperId.CUSTOM:
        assert tgt.custom_paper_mm is not None  # enforced by the settings model
        lo, hi = sorted((tgt.custom_paper_mm.width, tgt.custom_paper_mm.height))
        return PaperId.CUSTOM.value, (lo, hi)
    p = get_paper(tgt.paper.value)
    return tgt.paper.value, (p.width_mm, p.height_mm)


def _margins_of(printer: PrinterProfile, paper_id: str) -> Margins:
    try:
        return printer.margins_for(PaperId(paper_id))
    except ValueError:  # ROLL: no per-paper margins
        return printer.default_margins


def _shrink_roll(
    ctx: _Ctx,
    best: _Candidate,
    paper_portrait: Vec2,
    margins: Margins,
    band_mm: float,
    band_top: bool,
) -> _Candidate:
    """Single-strip rolls: cut the sheet to the length actually needed (PAP-06)."""
    frame = best.frame
    needed = max(best.layout.y.target, MIN_CONTENT_MM) + frame.margins.top + frame.margins.bottom
    length = min(paper_portrait[1], needed + band_mm)
    if length >= frame.paper[1] - 1e-9:
        return best
    try:
        # ``butt=True`` skips the overlap-vs-height check: with one row there is no vertical join.
        short = _make_frame(
            (paper_portrait[0], length),
            margins,
            Orientation.PORTRAIT,
            band_mm,
            band_top,
            ctx.overlap,
            True,
            fixed_width=True,
        )
    except DomainError:
        return best
    if short.content[1] - ctx.overlap <= 1e-9:  # the layout would divide by a zero step
        return best
    cand = _evaluate(ctx, short, best.scale)
    return cand if cand.layout.rows == 1 else best


def _resolve_printer(settings: JobSettings, printer: PrinterProfile | None) -> PrinterProfile:
    if printer is not None:
        return printer
    found = get_builtin_printer(settings.target.printer_id) or get_builtin_printer(
        "builtin-generic"
    )
    assert found is not None  # the generic printer is always built in
    return found


# ------------------------------------------------------------------------------- plan


def plan(
    page: PageGeometry,
    settings: JobSettings,
    *,
    printer: PrinterProfile | None = None,
    occupancy: OccupancyMap | None = None,
    include_alternatives: bool = False,
    exact_scale: bool = False,
    max_sheets: int = DEFAULT_MAX_SHEETS,
) -> Plan:
    """Compute the tiling plan of one page (SPEC 5, 11.3).

    ``printer`` supplies margins and supported papers (default: the profile named by
    ``settings.target.printer_id``, else the generic 5 mm one); ``settings.target.margins_mm``
    overrides its margins. ``occupancy`` enables blank-sheet detection and ``auto_trim``.
    ``include_alternatives`` adds ``alternatives`` and, for ``percent`` mode unless
    ``exact_scale`` is set (pattern/plan presets), ``savings``. Raises :class:`DomainError`
    (``CONTENT_AREA_TOO_SMALL``, ``OVERLAP_TOO_LARGE``, ``SCALE_OUT_OF_RANGE``,
    ``TOO_MANY_SHEETS``, ``INVALID_SETTINGS``).
    """
    source = _resolve_source(page, settings, occupancy)
    region = source.region
    size = (region.width, region.height)
    butt = settings.tiling.join is JoinMethod.BUTT
    overlap = 0.0 if butt else settings.tiling.overlap_mm
    ctx = _Ctx(size, region, source.occ, overlap, butt, settings)

    prof = _resolve_printer(settings, printer)
    paper_id, paper_portrait = _resolve_paper(settings)
    tgt = settings.target
    margins = tgt.margins_mm if tgt.margins_mm is not None else _margins_of(prof, paper_id)
    band = tgt.info_band
    band_top = band.position is InfoBandPosition.TOP
    rotated = tgt.landscape_as_rotated_portrait
    roll = tgt.roll_mm is not None

    orientations = _ORIENTATIONS if tgt.orientation is Orientation.AUTO else (tgt.orientation,)
    if roll:
        orientations = (Orientation.PORTRAIT,)
    frames: list[_Frame] = []
    cands: list[_Candidate] = []
    errors: list[DomainError] = []
    # AUTO tries each orientation and keeps the ones that fit; if none does, the error of the
    # first (portrait) is re-raised so the user sees the primary orientation's problem.
    for orient in orientations:
        try:
            frame = _make_frame(
                paper_portrait,
                margins,
                orient,
                band.height_mm,
                band_top,
                overlap,
                butt,
                rotated,
                roll,
            )
            cands.append(_evaluate(ctx, frame))
            frames.append(frame)
        except DomainError as exc:
            errors.append(exc)
    if not cands:
        raise errors[0]
    best = cands[0]
    for other in cands[1:]:
        if _better(ctx, other, best):
            best = other

    if roll and best.layout.rows == 1:
        best = _shrink_roll(ctx, best, paper_portrait, margins, band.height_mm, band_top)

    if best.layout.cells > MAX_GRID_CELLS:
        raise DomainError(
            ErrorCode.TOO_MANY_SHEETS,
            f"{best.layout.cells} sheets exceed the limit",
            {"sheets": best.layout.cells, "max_sheets": max_sheets},
        )
    sheets, counts = _build_sheets(ctx, best, page)
    if counts.printed > max_sheets:
        raise DomainError(
            ErrorCode.TOO_MANY_SHEETS,
            f"{counts.printed} sheets exceed the limit of {max_sheets}",
            {"sheets": counts.printed, "max_sheets": max_sheets},
        )

    s = best.scale
    dpi = _effective_dpi(page, s)
    warnings = list(source.warnings)
    if page.physical_size_assumed:
        warnings.append(
            PlanWarning(
                code=WarningCode.PHYSICAL_SIZE_ASSUMED,
                params={"width_mm": page.width_mm, "height_mm": page.height_mm},
            )
        )
    if dpi is not None and dpi < VERY_LOW_DPI:
        warnings.append(PlanWarning(code=WarningCode.VERY_LOW_DPI, params={"dpi": dpi}))
    elif dpi is not None and dpi < LOW_DPI:
        warnings.append(PlanWarning(code=WarningCode.LOW_DPI, params={"dpi": dpi}))
    warnings.extend(legibility_warnings(page, s))
    if counts.printed > MANY_SHEETS_THRESHOLD:
        warnings.append(
            PlanWarning(code=WarningCode.MANY_SHEETS, params={"sheets": counts.printed})
        )

    alternatives: list[Alternative] | None = None
    savings: list[Saving] | None = None
    if include_alternatives:
        alternatives = _alternatives(
            ctx, best, paper_id, paper_portrait, prof, band.height_mm, band_top
        )
        if isinstance(settings.scale, PercentScale) and not exact_scale:
            savings = _savings(ctx, frames, best)

    lay, frame = best.layout, best.frame
    return Plan(
        page_index=page.index,
        view_size_mm=(source.view.width, source.view.height),
        source_rect_mm=(region.x0, region.y0, region.x1, region.y1),
        orientation=frame.orientation,
        paper=PaperRef(id=paper_id, width_mm=frame.paper[0], height_mm=frame.paper[1]),
        margins_mm=frame.margins,
        content_mm=frame.content,
        content_origin_mm=frame.origin,
        overlap_mm=overlap,
        step_mm=(lay.x.step, lay.y.step),
        scale=s,
        scale_label=scale_label(settings.scale, s),
        grid=Grid(cols=lay.cols, rows=lay.rows),
        coverage_mm=(lay.x.coverage, lay.y.coverage),
        origin_mm=(lay.x.origin, lay.y.origin),
        offset_range_mm=Range2(x=lay.x.offset_range, y=lay.y.offset_range),
        assembled_size_mm=(lay.x.target, lay.y.target),
        effective_dpi=dpi,
        sheets=sheets,
        counts=counts,
        alternatives=alternatives,
        savings=savings,
        warnings=warnings,
    )


__all__ = [
    "DEFAULT_MAX_SHEETS",
    "Affine",
    "plan",
    "printer_correction_matrix",
    "scale_label",
]
