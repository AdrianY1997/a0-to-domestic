"""Property tests for the planner: SPEC 15.2 invariants on random sources, papers and scales."""

from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from a0td.domain.errors import DomainError
from a0td.domain.geometry import Rect
from a0td.domain.occupancy import RectOccupancy
from a0td.domain.page import PageGeometry, ViewTransform
from a0td.domain.papers import PaperId
from a0td.domain.plan import Plan
from a0td.domain.planner import plan
from a0td.domain.settings import JobSettings

EPS = 1e-6
PAPERS = [p.value for p in PaperId if p not in (PaperId.CUSTOM, PaperId.A6, PaperId.PHOTO_10X15)]

SETTINGS = settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


def make(
    paper: str = "A4",
    orientation: str = "auto",
    scale: dict[str, Any] | None = None,
    margin: float = 5.0,
    overlap: float = 10.0,
    join: str = "overlap",
    **tiling: Any,
) -> JobSettings:
    data: dict[str, Any] = {
        "target": {
            "paper": paper,
            "orientation": orientation,
            "margins_mm": {"top": margin, "right": margin, "bottom": margin, "left": margin},
        },
        "tiling": {"join": join, "overlap_mm": overlap, **tiling},
    }
    if scale is not None:
        data["scale"] = scale
    return JobSettings.model_validate(data)


def try_plan(page: PageGeometry, s: JobSettings, **kw: Any) -> Plan:
    """Plan, discarding combinations the domain legitimately rejects."""
    try:
        return plan(page, s, **kw)
    except DomainError:
        assume(False)
        raise  # pragma: no cover


dims = st.floats(min_value=60, max_value=3000, allow_nan=False, allow_infinity=False)
pages = st.builds(lambda w, h: PageGeometry(width_mm=w, height_mm=h), dims, dims)
papers = st.sampled_from(PAPERS)
margins = st.floats(min_value=0, max_value=15, allow_nan=False)
overlaps = st.floats(min_value=3, max_value=50, allow_nan=False)
percents = st.floats(min_value=5, max_value=400, allow_nan=False)
orientations = st.sampled_from(["auto", "portrait", "landscape"])
aligns = st.sampled_from(["start", "center", "end"])


def paper_coords(p: Plan) -> list[tuple[float, float, float, float]]:
    """Tiles in paper mm relative to the top-left of the printed source (T-space)."""
    x0, y0 = p.source_rect_mm[0], p.source_rect_mm[1]
    s = p.scale
    return [
        (
            (sh.src_rect_mm[0] - x0) * s,
            (sh.src_rect_mm[1] - y0) * s,
            (sh.src_rect_mm[2] - x0) * s,
            (sh.src_rect_mm[3] - y0) * s,
        )
        for sh in p.sheets
    ]


def grid_of(p: Plan) -> dict[tuple[int, int], tuple[float, float, float, float]]:
    return {(sh.row, sh.col): t for sh, t in zip(p.sheets, paper_coords(p), strict=True)}


def check_tiling(p: Plan) -> None:
    g = grid_of(p)
    rows, cols = p.grid.rows, p.grid.cols
    assert len(g) == rows * cols == p.counts.total
    t_w, t_h = p.assembled_size_mm
    c_w, c_h = p.content_mm
    o = p.overlap_mm
    # 3. no tile exceeds the usable area, which itself fits in the paper
    for x0, y0, x1, y1 in g.values():
        assert x1 - x0 == pytest.approx(c_w, abs=EPS)
        assert y1 - y0 == pytest.approx(c_h, abs=EPS)
    m = p.margins_mm
    assert c_w <= p.paper.width_mm - m.left - m.right + EPS
    assert c_h <= p.paper.height_mm - m.top - m.bottom + EPS
    # 1. union covers R: tiles are contiguous along each axis and span [0, T]
    for r in range(rows):
        assert g[(r, 0)][0] <= EPS
        assert g[(r, cols - 1)][2] >= t_w - EPS
        for c in range(cols - 1):
            # 2. adjacent tiles overlap exactly o
            assert g[(r, c)][2] - g[(r, c + 1)][0] == pytest.approx(o, abs=EPS)
    for c in range(cols):
        assert g[(0, c)][1] <= EPS
        assert g[(rows - 1, c)][3] >= t_h - EPS
        for r in range(rows - 1):
            assert g[(r, c)][3] - g[(r + 1, c)][1] == pytest.approx(o, abs=EPS)
    # 4. minimality: one fewer column / row cannot cover
    d_w, d_h = p.step_mm
    if cols > 1:
        assert (cols - 1) * d_w + o < t_w + EPS
    if rows > 1:
        assert (rows - 1) * d_h + o < t_h + EPS
    assert p.coverage_mm[0] == pytest.approx(cols * d_w + o)
    assert p.coverage_mm[1] == pytest.approx(rows * d_h + o)


@SETTINGS
@given(pages, papers, margins, overlaps, percents, orientations, aligns, aligns)
def test_tiling_invariants_percent(
    page: PageGeometry,
    paper: str,
    margin: float,
    overlap: float,
    pct: float,
    orientation: str,
    ax: str,
    ay: str,
) -> None:
    s = make(
        paper,
        orientation,
        {"mode": "percent", "percent": pct},
        margin,
        overlap,
        align={"x": ax, "y": ay},
    )
    check_tiling(try_plan(page, s))


@SETTINGS
@given(pages, papers, margins, overlaps, percents)
def test_tiling_invariants_butt(
    page: PageGeometry, paper: str, margin: float, overlap: float, pct: float
) -> None:
    p = try_plan(
        page, make(paper, "auto", {"mode": "percent", "percent": pct}, margin, join="butt")
    )
    assert p.overlap_mm == 0
    assert p.step_mm == p.content_mm
    check_tiling(p)


@SETTINGS
@given(pages, papers, margins, overlaps, percents, orientations)
def test_centered_slack_is_symmetric(
    page: PageGeometry, paper: str, margin: float, overlap: float, pct: float, orientation: str
) -> None:
    p = try_plan(
        page, make(paper, orientation, {"mode": "percent", "percent": pct}, margin, overlap)
    )
    for axis in (0, 1):
        slack_start = -p.origin_mm[axis]
        slack_end = p.coverage_mm[axis] + p.origin_mm[axis] - p.assembled_size_mm[axis]
        assert slack_start >= -1e-9
        assert slack_start == pytest.approx(slack_end, abs=1e-9)


@SETTINGS
@given(
    pages,
    papers,
    margins,
    overlaps,
    percents,
    aligns,
    aligns,
    st.floats(-500, 500, allow_nan=False),
    st.floats(-500, 500, allow_nan=False),
)
def test_offset_is_clamped_and_range_is_exact(
    page: PageGeometry,
    paper: str,
    margin: float,
    overlap: float,
    pct: float,
    ax: str,
    ay: str,
    dx: float,
    dy: float,
) -> None:
    scale = {"mode": "percent", "percent": pct}
    base = try_plan(
        page,
        make(paper, "portrait", scale, margin, overlap, align={"x": ax, "y": ay}),
    )
    moved = try_plan(
        page,
        make(
            paper,
            "portrait",
            scale,
            margin,
            overlap,
            align={"x": ax, "y": ay},
            offset_mm={"x": dx, "y": dy},
        ),
    )
    check_tiling(moved)
    for axis, d in ((0, dx), (1, dy)):
        lo, hi = (base.offset_range_mm.x, base.offset_range_mm.y)[axis]
        slack = base.coverage_mm[axis] - base.assembled_size_mm[axis]
        assert hi - lo == pytest.approx(slack, abs=1e-9)
        assert moved.origin_mm[axis] == pytest.approx(
            base.origin_mm[axis] + min(hi, max(lo, d)), abs=1e-9
        )
        assert -slack - 1e-9 <= moved.origin_mm[axis] <= 1e-9


@SETTINGS
@given(pages, papers, margins, overlaps, percents)
def test_auto_orientation_never_gives_more_sheets(
    page: PageGeometry, paper: str, margin: float, overlap: float, pct: float
) -> None:
    scale = {"mode": "percent", "percent": pct}
    auto = try_plan(page, make(paper, "auto", scale, margin, overlap))
    for orientation in ("portrait", "landscape"):
        try:
            other = plan(page, make(paper, orientation, scale, margin, overlap))
        except DomainError:
            continue
        assert auto.counts.total <= other.counts.total


@SETTINGS
@given(pages, papers, margins, overlaps)
def test_fit_sheet_auto_gives_the_largest_scale(
    page: PageGeometry, paper: str, margin: float, overlap: float
) -> None:
    scale = {"mode": "fit_sheet"}
    auto = try_plan(page, make(paper, "auto", scale, margin, overlap))
    assert auto.counts.total == 1
    for orientation in ("portrait", "landscape"):
        try:
            other = plan(page, make(paper, orientation, scale, margin, overlap))
        except DomainError:
            continue
        assert auto.scale >= other.scale - 1e-12
    # the printed source fits inside the usable area
    assert auto.assembled_size_mm[0] <= auto.content_mm[0] + EPS
    assert auto.assembled_size_mm[1] <= auto.content_mm[1] + EPS


@SETTINGS
@given(
    pages,
    papers,
    margins,
    overlaps,
    st.integers(1, 6),
    st.integers(1, 6),
    orientations,
)
def test_fit_grid_never_exceeds_the_requested_grid(
    page: PageGeometry,
    paper: str,
    margin: float,
    overlap: float,
    cols: int,
    rows: int,
    orientation: str,
) -> None:
    p = try_plan(
        page,
        make(paper, orientation, {"mode": "fit_grid", "cols": cols, "rows": rows}, margin, overlap),
    )
    assert p.grid.cols <= cols
    assert p.grid.rows <= rows
    # one of the axes is fully used (the scale is the largest that still fits)
    assert p.grid.cols == cols or p.grid.rows == rows
    check_tiling(p)


@SETTINGS
@given(pages, papers, margins, overlaps, st.integers(1, 30), orientations)
def test_fit_count_respects_the_sheet_budget(
    page: PageGeometry,
    paper: str,
    margin: float,
    overlap: float,
    n: int,
    orientation: str,
) -> None:
    p = try_plan(
        page, make(paper, orientation, {"mode": "fit_count", "max_sheets": n}, margin, overlap)
    )
    assert p.counts.total <= n
    # a larger budget never shrinks the scale
    bigger = try_plan(
        page,
        make(paper, orientation, {"mode": "fit_count", "max_sheets": n + 1}, margin, overlap),
    )
    assert bigger.scale >= p.scale - 1e-12


@SETTINGS
@given(
    st.integers(1, 9),
    st.integers(1, 9),
    st.floats(min_value=0.2, max_value=3, allow_nan=False),
    st.sampled_from(["portrait", "landscape"]),
    st.sampled_from([("A4", 210, 297), ("A3", 297, 420), ("LETTER", 215.9, 279.4)]),
)
def test_boundary_t_equals_n_d_plus_o_gives_exactly_n(
    n: int, m: int, s: float, orientation: str, paper: tuple[str, float, float]
) -> None:
    name, pw, ph = paper
    if orientation == "landscape":
        pw, ph = ph, pw
    d_w, d_h = pw - 10 - 10, ph - 10 - 10  # C = P - 10 (5 mm margins), o = 10
    t_w, t_h = n * d_w + 10, m * d_h + 10
    page = PageGeometry(width_mm=t_w / s, height_mm=t_h / s)
    p = plan(page, make(name, orientation, {"mode": "percent", "percent": s * 100}))
    assert (p.grid.cols, p.grid.rows) == (n, m)
    check_tiling(p)


@SETTINGS
@given(
    pages,
    papers,
    percents,
    st.lists(
        st.tuples(
            st.floats(0, 1, allow_nan=False),
            st.floats(0, 1, allow_nan=False),
            st.floats(0.01, 0.5, allow_nan=False),
            st.floats(0.01, 0.5, allow_nan=False),
        ),
        min_size=0,
        max_size=3,
    ),
    orientations,
)
def test_blank_flags_match_the_occupancy(
    page: PageGeometry,
    paper: str,
    pct: float,
    content: list[tuple[float, float, float, float]],
    orientation: str,
) -> None:
    rects = [
        Rect.from_xywh(
            fx * (1 - fw) * page.width_mm,
            fy * (1 - fh) * page.height_mm,
            fw * page.width_mm,
            fh * page.height_mm,
        )
        for fx, fy, fw, fh in content
    ]
    occ = RectOccupancy(rects)
    s = make(paper, orientation, {"mode": "percent", "percent": pct})
    p = try_plan(page, s, occupancy=occ)
    for sh in p.sheets:
        tile = Rect(*sh.src_rect_mm)
        expect_blank = not any(r.intersects(tile) for r in rects if not r.is_empty)
        assert sh.blank == expect_blank
        assert (sh.print_index is None) == sh.blank  # skip_blank defaults to true
    printed = [sh.print_index for sh in p.sheets if sh.print_index is not None]
    assert printed == list(range(1, p.counts.printed + 1))
    assert p.counts.printed + p.counts.blank == p.counts.total
    # every content rectangle is covered by at least one printed sheet
    for r in rects:
        if not r.is_empty:
            assert any(Rect(*sh.src_rect_mm).intersects(r) for sh in p.sheets if sh.print_index)


@SETTINGS
@given(pages, papers, margins, overlaps, percents, st.sampled_from([0, 90, 180, 270]))
def test_rotation_only_swaps_the_view(
    page: PageGeometry, paper: str, margin: float, overlap: float, pct: float, rot: int
) -> None:
    scale = {"mode": "percent", "percent": pct}
    s = make(paper, "portrait", scale, margin, overlap)
    s.source.rotate = rot  # type: ignore[assignment]
    p = try_plan(page, s)
    expected = (
        (page.height_mm, page.width_mm) if rot in (90, 270) else (page.width_mm, page.height_mm)
    )
    assert p.view_size_mm == pytest.approx(expected)
    check_tiling(p)


@SETTINGS
@given(
    st.sampled_from([0, 90, 180, 270]),
    st.floats(1, 500, allow_nan=False),
    st.floats(1, 500, allow_nan=False),
    st.tuples(*[st.floats(0, 1, allow_nan=False)] * 4),
)
def test_view_transform_roundtrip(rot: int, w: float, h: float, frac: tuple[float, ...]) -> None:
    box = Rect(7, 3, 7 + w, 3 + h)
    tf = ViewTransform(box, rot)
    a, b = sorted((frac[0], frac[2])), sorted((frac[1], frac[3]))
    r = Rect(7 + a[0] * w, 3 + b[0] * h, 7 + a[1] * w, 3 + b[1] * h)
    v = tf.to_view(r)
    assert v.width == pytest.approx(r.width if rot in (0, 180) else r.height)
    back = tf.to_canonical(v)
    assert back.as_list() == pytest.approx(r.as_list(), abs=1e-9)
