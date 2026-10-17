"""Planner tests: every SPEC Appendix C vector plus behaviour of each planner feature."""

from itertools import pairwise
from typing import Any

import pytest

from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.domain.geometry import Rect
from a0td.domain.occupancy import GridOccupancy, RectOccupancy
from a0td.domain.page import PageGeometry, ViewTransform
from a0td.domain.papers import Orientation, PaperId
from a0td.domain.plan import Edge, Plan
from a0td.domain.planner import (
    plan,
    printer_correction_matrix,
    scale_label,
)
from a0td.domain.settings import (
    BUILTIN_PRINTERS,
    DrawingScale,
    JobSettings,
    Margins,
    PercentScale,
    PrinterProfile,
)


def a0(**kw: Any) -> PageGeometry:
    return PageGeometry(width_mm=841, height_mm=1189, **kw)


def cfg(
    paper: str = "A4",
    orientation: str = "auto",
    scale: dict[str, Any] | None = None,
    **sections: dict[str, Any],
) -> JobSettings:
    data: dict[str, Any] = {"target": {"paper": paper, "orientation": orientation}}
    if scale is not None:
        data["scale"] = scale
    for key, val in sections.items():
        data.setdefault(key, {}).update(val)
    return JobSettings.model_validate(data)


def r1(values: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(round(v, 1) for v in values)


# --------------------------------------------------------------------- C.1 tiling at 100 %

C1 = [
    # paper, orientation, content, step, cols, rows, sheets
    ("A4", "portrait", (200, 287), (190, 277), 5, 5, 25),
    ("A4", "landscape", (287, 200), (277, 190), 3, 7, 21),
    ("A3", "portrait", (287, 410), (277, 400), 3, 3, 9),
    ("A3", "landscape", (410, 287), (400, 277), 3, 5, 15),
    ("LETTER", "portrait", (205.9, 269.4), (195.9, 259.4), 5, 5, 25),
    ("LETTER", "landscape", (269.4, 205.9), (259.4, 195.9), 4, 7, 28),
    ("LEGAL", "portrait", (205.9, 345.6), (195.9, 335.6), 5, 4, 20),
    ("LEGAL", "landscape", (345.6, 205.9), (335.6, 195.9), 3, 7, 21),
    ("TABLOID", "portrait", (269.4, 421.8), (259.4, 411.8), 4, 3, 12),
    ("TABLOID", "landscape", (421.8, 269.4), (411.8, 259.4), 3, 5, 15),
]


@pytest.mark.parametrize(("paper", "orient", "content", "step", "cols", "rows", "total"), C1)
def test_c1_forced_orientation(
    paper: str,
    orient: str,
    content: tuple[float, float],
    step: tuple[float, float],
    cols: int,
    rows: int,
    total: int,
) -> None:
    p = plan(a0(), cfg(paper, orient))
    assert p.orientation.value == orient
    assert r1(p.content_mm) == content
    assert r1(p.step_mm) == step
    assert (p.grid.cols, p.grid.rows) == (cols, rows)
    assert p.counts.total == total == len(p.sheets)
    assert p.overlap_mm == 10


@pytest.mark.parametrize(
    ("paper", "orient", "sheets"),
    [
        ("A4", "landscape", 21),
        ("A3", "portrait", 9),
        ("LETTER", "portrait", 25),
        ("LEGAL", "portrait", 20),
        ("TABLOID", "portrait", 12),
    ],
)
def test_c1_auto_orientation(paper: str, orient: str, sheets: int) -> None:
    p = plan(a0(), cfg(paper, "auto"))
    assert p.orientation.value == orient
    assert p.counts.total == sheets


def test_c1_boundary_exact_division_gives_three_columns() -> None:
    # (841 - 10) / 277 == 3 exactly (A4 landscape and A3 portrait): 3 columns, not 4
    assert plan(a0(), cfg("A4", "landscape")).grid.cols == 3
    assert plan(a0(), cfg("A3", "portrait")).grid.cols == 3


def test_c1_a4_landscape_centered_details() -> None:
    p = plan(a0(), cfg("A4", "landscape"))
    assert r1(p.coverage_mm) == (841, 1340)
    assert r1(p.assembled_size_mm) == (841, 1189)
    assert p.origin_mm == (0.0, -75.5)
    assert p.offset_range_mm.x == (0.0, 0.0)
    assert p.offset_range_mm.y == (-75.5, 75.5)
    by_id = {s.id: s for s in p.sheets}
    assert by_id["A1"].src_rect_mm == pytest.approx((0, -75.5, 287, 124.5))
    assert by_id["G1"].src_rect_mm == pytest.approx((0, 1064.5, 287, 1264.5))
    assert p.paper.id == "A4"
    assert (p.paper.width_mm, p.paper.height_mm) == (297, 210)
    assert p.content_origin_mm == (5, 5)


# ------------------------------------------------------------------------ C.2 fit_sheet

C2 = [
    ("A4", "portrait", 23.78, (200.0, 282.8)),
    ("A3", "portrait", 34.13, (287.0, 405.8)),
    ("LETTER", "portrait", 22.66, (190.6, 269.4)),
]


@pytest.mark.parametrize(("paper", "orient", "pct", "size"), C2)
def test_c2_fit_sheet(paper: str, orient: str, pct: float, size: tuple[float, float]) -> None:
    p = plan(a0(), cfg(paper, scale={"mode": "fit_sheet"}))
    assert p.orientation.value == orient
    assert round(p.scale * 100, 2) == pct
    assert r1(p.assembled_size_mm) == size
    assert p.counts.total == 1
    assert p.scale_label == f"{pct:g} %"


def test_c2_a4_landscape_would_give_16_82_percent() -> None:
    p = plan(a0(), cfg("A4", "landscape", scale={"mode": "fit_sheet"}))
    assert round(p.scale * 100, 2) == 16.82


def test_c2_a4_exact_values() -> None:
    p = plan(a0(), cfg("A4", scale={"mode": "fit_sheet"}))
    assert p.scale == pytest.approx(200 / 841)
    # centered on the single sheet
    assert p.origin_mm[0] == pytest.approx(0.0)
    assert p.origin_mm[1] < 0


# ------------------------------------------------------------------------ C.3 fit_grid


def test_c3_fit_grid_a3_2x2() -> None:
    p = plan(a0(), cfg("A3", scale={"mode": "fit_grid", "cols": 2, "rows": 2}))
    assert p.orientation is Orientation.PORTRAIT
    assert p.scale == pytest.approx(564 / 841)
    assert round(p.scale * 100, 2) == 67.06
    assert r1(p.assembled_size_mm) == (564.0, 797.4)
    assert (p.grid.cols, p.grid.rows) == (2, 2)
    landscape = plan(a0(), cfg("A3", "landscape", scale={"mode": "fit_grid", "cols": 2, "rows": 2}))
    assert round(landscape.scale * 100, 2) == 47.43


def test_c3_fit_grid_a4_2x2() -> None:
    p = plan(a0(), cfg("A4", scale={"mode": "fit_grid", "cols": 2, "rows": 2}))
    assert round(p.scale * 100, 2) == 46.37
    assert p.scale == pytest.approx(390 / 841)
    assert r1(p.assembled_size_mm) == (390.0, 551.4)
    assert (p.grid.cols, p.grid.rows) == (2, 2)


# ------------------------------------------------------------------ C.4 drawing scale


def test_c4_drawing_scale() -> None:
    scale = {"mode": "drawing_scale", "from_denominator": 50, "to_denominator": 100}
    auto = plan(a0(), cfg("A4", scale=scale))
    assert auto.scale == 0.5
    assert auto.scale_label == "1:100"
    assert r1(auto.assembled_size_mm) == (420.5, 594.5)
    assert auto.orientation is Orientation.LANDSCAPE
    assert (auto.grid.cols, auto.grid.rows) == (2, 4)
    assert auto.counts.total == 8
    portrait = plan(a0(), cfg("A4", "portrait", scale=scale))
    assert (portrait.grid.cols, portrait.grid.rows) == (3, 3)
    assert portrait.counts.total == 9


# ------------------------------------------------------------------------- C.5 savings


def test_c5_savings_contain_spec_vectors() -> None:
    p = plan(a0(), cfg("A4"), include_alternatives=True)
    assert p.orientation is Orientation.LANDSCAPE
    assert p.counts.total == 21
    assert p.savings is not None
    by_pct = {round(s.scale * 100, 2): s for s in p.savings}
    top = by_pct[96.72]
    assert (top.orientation, top.cols, top.rows, top.sheets) == (Orientation.LANDSCAPE, 3, 6, 18)
    assert top.scale == pytest.approx(1150 / 1189)
    low = by_pct[80.74]
    assert (low.orientation, low.cols, low.rows, low.sheets) == (Orientation.LANDSCAPE, 3, 5, 15)
    assert low.scale == pytest.approx(960 / 1189)
    assert low.saved == 6


def test_c5_savings_are_pareto_optimal_and_capped() -> None:
    p = plan(a0(), cfg("A4"), include_alternatives=True)
    assert p.savings is not None
    assert len(p.savings) <= 3
    scales = [s.scale for s in p.savings]
    sheets = [s.sheets for s in p.savings]
    assert scales == sorted(scales, reverse=True)
    assert all(a > b for a, b in pairwise(sheets))
    assert all(0.8 * 1.0 - 1e-9 <= s < 1.0 for s in scales)
    # the portrait 91.56 % x 16 sheets option is also Pareto-optimal (see the report)
    assert [(round(s.scale * 100, 2), s.sheets) for s in p.savings] == [
        (96.72, 18),
        (91.56, 16),
        (80.74, 15),
    ]


def test_savings_hidden_for_exact_scales_and_non_percent_modes() -> None:
    assert plan(a0(), cfg("A4"), include_alternatives=True, exact_scale=True).savings is None
    assert plan(a0(), cfg("A4")).savings is None
    fit = plan(a0(), cfg("A4", scale={"mode": "fit_sheet"}), include_alternatives=True)
    assert fit.savings is None
    assert fit.alternatives is not None


def test_savings_with_butt_and_forced_orientation() -> None:
    p = plan(
        a0(),
        cfg("A4", "portrait", tiling={"join": "butt"}),
        include_alternatives=True,
    )
    assert p.savings is not None
    assert all(s.orientation is Orientation.PORTRAIT for s in p.savings)
    assert all(s.sheets < p.counts.total for s in p.savings)


def test_savings_empty_when_nothing_improves() -> None:
    p = plan(a0(), cfg("A3", scale={"mode": "percent", "percent": 50}), include_alternatives=True)
    assert p.savings is not None
    assert all(s.sheets < p.counts.total for s in p.savings)


def test_savings_without_occupancy_for_big_grids() -> None:
    p = plan(
        a0(),
        cfg("A4", scale={"mode": "percent", "percent": 300}),
        include_alternatives=True,
        max_sheets=500,
    )
    assert p.savings is not None
    assert all(s.sheets < p.counts.total for s in p.savings)


# ------------------------------------------------------------------------ C.6 DPI


def test_c6_effective_dpi() -> None:
    hi = plan(a0(px=(9933, 14043)), cfg("A4"))
    assert hi.effective_dpi == pytest.approx(300, abs=0.1)
    assert not hi.warnings

    lo = plan(a0(px=(4000, 5657)), cfg("A4"))
    assert lo.effective_dpi is not None
    assert round(lo.effective_dpi, 1) == 120.8
    assert [w.code for w in lo.warnings] == [WarningCode.LOW_DPI]
    assert lo.warnings[0].params["dpi"] == pytest.approx(lo.effective_dpi)

    fit = plan(a0(px=(4000, 5657)), cfg("A4", scale={"mode": "fit_sheet"}))
    assert fit.effective_dpi is not None
    assert round(fit.effective_dpi) == 508
    assert not fit.warnings


def test_very_low_dpi_and_vector_has_no_dpi() -> None:
    p = plan(a0(px=(2000, 2828)), cfg("A4"))
    assert [w.code for w in p.warnings] == [WarningCode.VERY_LOW_DPI]
    assert plan(a0(), cfg("A4")).effective_dpi is None


def test_physical_size_assumed_passthrough() -> None:
    p = plan(a0(physical_size_assumed=True, px=(9933, 14043)), cfg("A4"))
    assert [w.code for w in p.warnings] == [WarningCode.PHYSICAL_SIZE_ASSUMED]
    assert p.warnings[0].params == {"width_mm": 841, "height_mm": 1189}


# ------------------------------------------------------------------------- scale modes


def test_percent_and_scale_range() -> None:
    p = plan(a0(), cfg("A3", scale={"mode": "percent", "percent": 50}))
    assert p.scale == 0.5
    assert p.scale_label == "50 %"
    with pytest.raises(DomainError) as exc:
        plan(a0(), cfg("A4", scale={"mode": "final_size", "width_mm": 100000}))
    assert exc.value.code is ErrorCode.SCALE_OUT_OF_RANGE
    with pytest.raises(DomainError) as exc:
        plan(a0(), cfg("A4", scale={"mode": "final_size", "width_mm": 1}))
    assert exc.value.code is ErrorCode.SCALE_OUT_OF_RANGE


def test_final_size_by_width_and_height() -> None:
    w = plan(a0(), cfg("A3", scale={"mode": "final_size", "width_mm": 420.5}))
    assert w.scale == pytest.approx(0.5)
    assert r1(w.assembled_size_mm) == (420.5, 594.5)
    h = plan(a0(), cfg("A3", scale={"mode": "final_size", "height_mm": 594.5}))
    assert h.scale == pytest.approx(0.5)
    assert h.scale_label == "50 %"


def _brute_fit_count(paper: str, n: int) -> float:
    best = 0.0
    for orient in ("portrait", "landscape"):
        p = plan(a0(), cfg(paper, orient, scale={"mode": "fit_sheet"}))
        cw, ch = p.content_mm
        dw, dh = cw - 10, ch - 10
        for c in range(1, n + 1):
            for r in range(1, n // c + 1):
                best = max(best, min((c * dw + 10) / 841, (r * dh + 10) / 1189))
    return best


@pytest.mark.parametrize(
    ("paper", "n"), [("A4", 1), ("A4", 4), ("A3", 4), ("A4", 6), ("LETTER", 9)]
)
def test_fit_count_matches_brute_force(paper: str, n: int) -> None:
    p = plan(a0(), cfg(paper, scale={"mode": "fit_count", "max_sheets": n}))
    assert p.scale == pytest.approx(_brute_fit_count(paper, n))
    assert p.counts.total <= n


def test_fit_count_one_equals_fit_sheet() -> None:
    one = plan(a0(), cfg("A4", scale={"mode": "fit_count", "max_sheets": 1}))
    fit = plan(a0(), cfg("A4", scale={"mode": "fit_sheet"}))
    assert one.scale == pytest.approx(fit.scale)
    assert one.counts.total == 1


def test_fit_grid_never_exceeds_requested_grid() -> None:
    for cols, rows in [(1, 1), (1, 3), (3, 1), (2, 5)]:
        p = plan(a0(), cfg("A4", scale={"mode": "fit_grid", "cols": cols, "rows": rows}))
        assert p.grid.cols <= cols
        assert p.grid.rows <= rows


def test_fit_orientation_tie_prefers_fewer_sheets_then_portrait() -> None:
    # A square source on a square-ish custom paper: portrait wins the exact tie
    page = PageGeometry(width_mm=500, height_mm=500)
    s = JobSettings.model_validate(
        {
            "target": {"paper": "CUSTOM", "custom_paper_mm": {"width": 200, "height": 200}},
            "scale": {"mode": "fit_sheet"},
        }
    )
    assert plan(page, s).orientation is Orientation.PORTRAIT


def test_fit_orientation_tie_on_scale_prefers_fewer_sheets() -> None:
    # fit_count on a square source with a non-square paper: both orientations share the scale
    page = PageGeometry(width_mm=1000, height_mm=1000)
    p = plan(page, cfg("A4", scale={"mode": "fit_count", "max_sheets": 1}))
    assert p.counts.total == 1


# --------------------------------------------------------------- grid, alignment, offset


def test_alignment_start_end_and_offset_clamping() -> None:
    base = {"align": {"x": "start", "y": "start"}}
    start = plan(a0(), cfg("A4", "landscape", tiling=base))
    assert start.origin_mm == (0.0, 0.0)
    assert start.offset_range_mm.y == (-151.0, 0.0)

    end = plan(a0(), cfg("A4", "landscape", tiling={"align": {"x": "end", "y": "end"}}))
    assert end.origin_mm == (0.0, -151.0)
    assert end.offset_range_mm.y == (0.0, 151.0)

    moved = plan(
        a0(),
        cfg("A4", "landscape", tiling={"offset_mm": {"x": 5, "y": 20}}),
    )
    assert moved.origin_mm == (0.0, -55.5)
    clamped = plan(a0(), cfg("A4", "landscape", tiling={"offset_mm": {"x": 5, "y": 500}}))
    assert clamped.origin_mm == (0.0, 0.0)
    clamped = plan(a0(), cfg("A4", "landscape", tiling={"offset_mm": {"x": 5, "y": -500}}))
    assert clamped.origin_mm == (0.0, -151.0)


def test_small_source_is_centered_on_one_sheet() -> None:
    page = PageGeometry(width_mm=100, height_mm=100)
    p = plan(page, cfg("A4", "portrait"))
    assert p.counts.total == 1
    assert p.origin_mm == pytest.approx((-50.0, -93.5))
    first = p.sheets[0].src_rect_mm
    assert first == pytest.approx((-50.0, -93.5, 150.0, 193.5))
    assert p.offset_range_mm.x == (-50.0, 50.0)


# -------------------------------------------------------------- margins and info band


def test_landscape_safe_margins_from_inkjet_profile() -> None:
    inkjet = next(p for p in BUILTIN_PRINTERS if p.id == "builtin-inkjet")  # 3/3/5/3
    port = plan(a0(), cfg("A4", "portrait"), printer=inkjet)
    assert port.margins_mm == Margins(top=3, right=3, bottom=5, left=3)
    assert r1(port.content_mm) == (204, 289)
    land = plan(a0(), cfg("A4", "landscape"), printer=inkjet)
    assert land.margins_mm == Margins(top=3, right=5, bottom=3, left=5)
    assert (land.margins_mm.top, land.margins_mm.bottom) == (3, 3)
    assert (land.margins_mm.left, land.margins_mm.right) == (5, 5)
    assert r1(land.content_mm) == (287, 204)


def test_explicit_margins_override_the_profile_and_printer_id_is_resolved() -> None:
    s = cfg(
        "A4", "portrait", target={"margins_mm": {"top": 10, "right": 1, "bottom": 2, "left": 3}}
    )
    p = plan(a0(), s)
    assert r1(p.content_mm) == (206, 285)
    laser = cfg("A4", "portrait", target={"printer_id": "builtin-laser"})
    assert r1(plan(a0(), laser).content_mm) == (201, 288)
    unknown = cfg("A4", "portrait", target={"printer_id": "nope"})
    assert r1(plan(a0(), unknown).content_mm) == (200, 287)


def test_info_band_top_and_bottom() -> None:
    top = plan(
        a0(),
        cfg("A4", "portrait", target={"info_band": {"position": "top", "height_mm": 12}}),
    )
    assert r1(top.content_mm) == (200, 275)
    assert top.content_origin_mm == (5, 17)
    bottom = plan(
        a0(),
        cfg("A4", "portrait", target={"info_band": {"position": "bottom", "height_mm": 12}}),
    )
    assert r1(bottom.content_mm) == (200, 275)
    assert bottom.content_origin_mm == (5, 5)


def test_custom_paper() -> None:
    s = cfg("CUSTOM", "portrait", target={"custom_paper_mm": {"width": 420, "height": 300}})
    p = plan(a0(), s)
    assert (p.paper.id, p.paper.width_mm, p.paper.height_mm) == ("CUSTOM", 300, 420)
    assert r1(p.content_mm) == (290, 410)


# ------------------------------------------------------------------------- validation


def test_content_area_too_small() -> None:
    s = cfg(
        "A6", "portrait", target={"margins_mm": {"top": 5, "right": 40, "bottom": 5, "left": 40}}
    )
    with pytest.raises(DomainError) as exc:
        plan(a0(), s)
    assert exc.value.code is ErrorCode.CONTENT_AREA_TOO_SMALL
    assert exc.value.params["min_mm"] == 30


def test_content_area_too_small_for_forced_orientation_but_auto_picks_the_other() -> None:
    # 50 x 200 custom paper with 15 mm side margins: portrait C = 20 x 190 (too small),
    # landscape (safe margins 15 top/bottom after swapping) is C = 170 x 20 (too small) as well
    s = cfg(
        "CUSTOM",
        "auto",
        target={
            "custom_paper_mm": {"width": 50, "height": 200},
            "margins_mm": {"top": 5, "right": 15, "bottom": 5, "left": 15},
        },
    )
    with pytest.raises(DomainError) as exc:
        plan(a0(), s)
    assert exc.value.code is ErrorCode.CONTENT_AREA_TOO_SMALL
    ok = cfg(
        "CUSTOM",
        "auto",
        target={
            "custom_paper_mm": {"width": 60, "height": 200},
            "margins_mm": {"top": 5, "right": 15, "bottom": 5, "left": 15},
        },
    )
    p = plan(a0(), ok)
    assert p.orientation is Orientation.PORTRAIT
    assert p.content_mm == (30, 190)


def test_overlap_too_large() -> None:
    s = cfg(
        "A6",
        "portrait",
        target={"margins_mm": {"top": 30, "right": 30, "bottom": 30, "left": 30}},
        tiling={"overlap_mm": 30},
    )
    with pytest.raises(DomainError) as exc:
        plan(a0(), s)
    assert exc.value.code is ErrorCode.OVERLAP_TOO_LARGE
    assert exc.value.params["max_mm"] == 25
    # the same overlap is fine when butt joining
    ok = cfg(
        "A6",
        "portrait",
        target={"margins_mm": {"top": 30, "right": 30, "bottom": 30, "left": 30}},
        tiling={"join": "butt"},
    )
    assert plan(a0(), ok).overlap_mm == 0


def test_overlap_below_minimum_is_rejected() -> None:
    s = cfg("A4").model_copy(deep=True)
    s.tiling.overlap_mm = 1  # bypasses the model validator on purpose
    with pytest.raises(DomainError) as exc:
        plan(a0(), s)
    assert exc.value.code is ErrorCode.INVALID_SETTINGS


def test_overlap_too_large_in_one_orientation_only_falls_back_to_the_other() -> None:
    # portrait C = 90 x 290 (max overlap 70); landscape safe margins give C = 210 x 50 (max 30)
    kwargs: dict[str, Any] = {
        "target": {
            "custom_paper_mm": {"width": 140, "height": 300},
            "margins_mm": {"top": 5, "right": 5, "bottom": 5, "left": 45},
        },
        "tiling": {"overlap_mm": 40},
    }
    auto = plan(a0(), cfg("CUSTOM", "auto", **kwargs))
    assert auto.orientation is Orientation.PORTRAIT
    with pytest.raises(DomainError) as exc:
        plan(a0(), cfg("CUSTOM", "landscape", **kwargs))
    assert exc.value.code is ErrorCode.OVERLAP_TOO_LARGE


def test_auto_orientation_both_fail_reraises_portrait_error() -> None:
    # overlap 40 exceeds the maximum in both orientations (portrait max 25, landscape max 25)
    kwargs: dict[str, Any] = {
        "target": {
            "margins_mm": {"top": 30, "right": 30, "bottom": 30, "left": 30},
        },
        "tiling": {"overlap_mm": 40},
    }
    with pytest.raises(DomainError) as exc:
        plan(a0(), cfg("A6", "auto", **kwargs))
    assert exc.value.code is ErrorCode.OVERLAP_TOO_LARGE
    assert exc.value.params["orientation"] == Orientation.PORTRAIT.value


def test_too_many_sheets() -> None:
    with pytest.raises(DomainError) as exc:
        plan(a0(), cfg("A4"), max_sheets=10)
    assert exc.value.code is ErrorCode.TOO_MANY_SHEETS
    assert exc.value.params["sheets"] == 21
    assert plan(a0(), cfg("A4"), max_sheets=21).counts.printed == 21


def test_too_many_sheets_hard_grid_guard() -> None:
    s = cfg("A6", scale={"mode": "percent", "percent": 1000})
    with pytest.raises(DomainError) as exc:
        plan(PageGeometry(width_mm=5000, height_mm=5000), s, max_sheets=10**9)
    assert exc.value.code is ErrorCode.TOO_MANY_SHEETS


def test_many_sheets_warning() -> None:
    p = plan(a0(), cfg("A4", scale={"mode": "percent", "percent": 200}))
    assert p.counts.printed > 50
    assert WarningCode.MANY_SHEETS in {w.code for w in p.warnings}
    assert plan(a0(), cfg("A4")).warnings == []


# ----------------------------------------------------------------------- naming & order


def test_ids_order_and_neighbors() -> None:
    p = plan(a0(), cfg("A4", "landscape"))
    assert [s.id for s in p.sheets[:4]] == ["A1", "A2", "A3", "B1"]
    assert [s.print_index for s in p.sheets[:4]] == [1, 2, 3, 4]
    b2 = next(s for s in p.sheets if s.id == "B2")
    assert (b2.neighbors.top, b2.neighbors.right, b2.neighbors.bottom, b2.neighbors.left) == (
        "A2",
        "B3",
        "C2",
        "B1",
    )
    a1 = p.sheets[0]
    assert a1.neighbors.top is None
    assert a1.neighbors.left is None
    last = p.sheets[-1]
    assert last.id == "G3"
    assert last.neighbors.right is None
    assert last.neighbors.bottom is None


def test_columns_order_and_other_naming() -> None:
    p = plan(
        a0(),
        cfg("A4", "landscape", tiling={"order": "columns", "naming": "number_number"}),
    )
    assert [s.id for s in p.sheets[:3]] == ["1-1", "2-1", "3-1"]
    assert p.sheets[7].id == "1-2"
    assert [s.print_index for s in p.sheets[:3]] == [1, 2, 3]
    seq = plan(a0(), cfg("A4", "landscape", tiling={"naming": "sequential"}))
    assert [s.id for s in seq.sheets[:4]] == ["1", "2", "3", "4"]
    seq_cols = plan(
        a0(), cfg("A4", "landscape", tiling={"naming": "sequential", "order": "columns"})
    )
    assert [s.id for s in seq_cols.sheets[:3]] == ["1", "2", "3"]
    assert (seq_cols.sheets[1].row, seq_cols.sheets[1].col) == (1, 0)


def test_page_prefix_for_multi_page_jobs() -> None:
    p = plan(a0(index=1, page_count=3), cfg("A4", "landscape"))
    assert p.page_index == 1
    assert p.sheets[0].id == "P2-A1"
    assert p.sheets[0].neighbors.right == "P2-A2"
    single = plan(a0(index=1, page_count=1), cfg("A4", "landscape"))
    assert single.sheets[0].id == "A1"


# --------------------------------------------------------------------- joins and edges


def edges(p: Plan, sid: str) -> tuple[set[Edge], set[Edge]]:
    s = next(x for x in p.sheets if x.id == sid)
    return set(s.cut_edges), set(s.receive_edges)


def test_overlap_edges() -> None:
    p = plan(a0(), cfg("A4", "landscape"))  # 3 cols x 7 rows
    assert edges(p, "A1") == (set(), {Edge.RIGHT, Edge.BOTTOM})
    assert edges(p, "B2") == ({Edge.LEFT, Edge.TOP}, {Edge.RIGHT, Edge.BOTTOM})
    assert edges(p, "G3") == ({Edge.LEFT, Edge.TOP}, set())
    assert edges(p, "A3") == ({Edge.LEFT}, {Edge.BOTTOM})


def test_butt_edges_are_all_cut() -> None:
    p = plan(a0(), cfg("A4", "landscape", tiling={"join": "butt"}))
    assert p.overlap_mm == 0
    assert p.step_mm == p.content_mm
    assert all(not s.receive_edges for s in p.sheets)
    assert edges(p, "B2")[0] == {Edge.LEFT, Edge.TOP, Edge.RIGHT, Edge.BOTTOM}
    assert edges(p, "A1")[0] == {Edge.RIGHT, Edge.BOTTOM}
    # tiles abut exactly
    s1, s2 = p.sheets[0].src_rect_mm, p.sheets[1].src_rect_mm
    assert s1[2] == pytest.approx(s2[0])


def test_adjacent_tiles_overlap_by_o() -> None:
    p = plan(a0(), cfg("A4", "landscape"))
    s1, s2 = p.sheets[0].src_rect_mm, p.sheets[1].src_rect_mm
    assert s1[2] - s2[0] == pytest.approx(10)


# ------------------------------------------------------------------ source geometry


def test_rotation_swaps_the_view() -> None:
    p = plan(a0(), cfg("A4", source={"rotate": 90}))
    assert p.view_size_mm == (1189, 841)
    assert p.source_rect_mm == (0, 0, 1189, 841)
    p180 = plan(a0(), cfg("A4", source={"rotate": 180}))
    assert p180.view_size_mm == (841, 1189)


def test_view_transform_roundtrip_and_known_points() -> None:
    box = Rect(10, 20, 110, 70)  # 100 x 50
    tf0 = ViewTransform(box, 0)
    assert tf0.to_view(Rect(10, 20, 30, 40)) == Rect(0, 0, 20, 20)
    tf90 = ViewTransform(box, 90)
    assert tf90.view_size == (50, 100)
    # clockwise: the top-left corner of the box ends up at the top-right of the view
    assert tf90.to_view(Rect(10, 20, 20, 30)) == Rect(40, 0, 50, 10)
    tf270 = ViewTransform(box, 270)
    assert tf270.to_view(Rect(10, 20, 20, 30)) == Rect(0, 90, 10, 100)
    tf180 = ViewTransform(box, 180)
    assert tf180.to_view(Rect(10, 20, 20, 30)) == Rect(90, 40, 100, 50)
    for rot in (0, 90, 180, 270):
        tf = ViewTransform(box, rot)
        r = Rect(12, 25, 47, 61)
        assert tf.to_canonical(tf.to_view(r)) == r


def test_region_is_clipped_to_the_page_and_sets_the_source() -> None:
    s = cfg("A4", source={"region_mm": {"x": 100, "y": 200, "width": 400, "height": 300}})
    p = plan(a0(), s)
    assert p.source_rect_mm == (100, 200, 500, 500)
    assert r1(p.assembled_size_mm) == (400, 300)
    first = p.sheets[0].src_rect_mm
    assert first[0] < 100 + 1e-9 or p.origin_mm[0] <= 0
    outside = cfg("A4", source={"region_mm": {"x": 800, "y": 200, "width": 400, "height": 300}})
    assert plan(a0(), outside).source_rect_mm == (800, 200, 841, 500)
    nowhere = cfg("A4", source={"region_mm": {"x": 900, "y": 200, "width": 40, "height": 30}})
    with pytest.raises(DomainError) as exc:
        plan(a0(), nowhere)
    assert exc.value.code is ErrorCode.INVALID_SETTINGS
    assert exc.value.errors is not None


def test_box_selection_and_fallback() -> None:
    page = a0(boxes_mm={"trim": (10, 10, 831, 1179)})
    trim = plan(page, cfg("A4", source={"box": "trim"}))
    assert trim.view_size_mm == (821, 1169)
    assert trim.warnings == []
    fallback = plan(page, cfg("A4", source={"box": "bleed"}))
    assert fallback.view_size_mm == (841, 1189)
    assert [w.code for w in fallback.warnings] == [WarningCode.BOX_FALLBACK]
    assert fallback.warnings[0].params == {"requested": "bleed"}
    explicit_crop = a0(boxes_mm={"crop": (0, 0, 800, 1100)})
    assert plan(explicit_crop, cfg("A4")).view_size_mm == (800, 1100)


# ------------------------------------------------------------ occupancy, blanks, trim


def top_half() -> RectOccupancy:
    return RectOccupancy([Rect(0, 0, 841, 594.5)])


def test_blank_sheets_are_skipped_and_numbering_counts_printed_only() -> None:
    p = plan(a0(), cfg("A4", "landscape"), occupancy=top_half())
    blanks = [s for s in p.sheets if s.blank]
    assert blanks
    assert p.counts.blank == len(blanks)
    assert p.counts.printed == p.counts.total - len(blanks)
    assert all(s.print_index is None for s in blanks)
    printed = [s.print_index for s in p.sheets if s.print_index is not None]
    assert printed == list(range(1, p.counts.printed + 1))
    # row E+ (rows start at y = -75.5 + 190 r) lies below 594.5 only from row 3 (y0 = 494.5 < 594.5)
    row_d = [s for s in p.sheets if s.id.startswith("D")]
    assert not any(s.blank for s in row_d)
    assert all(s.blank for s in p.sheets if s.id[0] in "EFG")


def test_blank_includes_overlap_and_touching_does_not_count() -> None:
    # content ends exactly at y = 1064.5 + 10: row G starts at 1064.5 (overlap strip included)
    occ = RectOccupancy([Rect(0, 0, 841, 1074.4)])
    p = plan(a0(), cfg("A4", "landscape"), occupancy=occ)
    assert not any(s.blank for s in p.sheets if s.id.startswith("G"))
    touching = RectOccupancy([Rect(0, 0, 841, 1064.5)])
    p2 = plan(a0(), cfg("A4", "landscape"), occupancy=touching)
    assert all(s.blank for s in p2.sheets if s.id.startswith("G"))


def test_skip_blank_off_keeps_blank_sheets_printed() -> None:
    p = plan(
        a0(),
        cfg("A4", "landscape", tiling={"skip_blank": False}),
        occupancy=top_half(),
    )
    assert p.counts.printed == p.counts.total
    assert p.counts.blank > 0
    assert all(s.print_index is not None for s in p.sheets)


def test_auto_orientation_ignores_blank_sheets_when_skipping() -> None:
    # content only in the left strip: 3 columns shrink to 1 effective column
    occ = RectOccupancy([Rect(0, 0, 150, 1189)])
    p = plan(a0(), cfg("A4"), occupancy=occ)
    full = plan(a0(), cfg("A4"))
    assert p.counts.printed < full.counts.printed
    assert p.counts.printed == min(
        plan(a0(), cfg("A4", o), occupancy=occ).counts.printed for o in ("portrait", "landscape")
    )


def test_only_sheets_selection() -> None:
    p = plan(a0(), cfg("A4", "landscape", tiling={"only_sheets": ["a1", "B3", "Z9"]}))
    assert [s.id for s in p.sheets if s.selected] == ["A1", "B3"]
    assert p.counts.printed == 2
    assert p.counts.deselected == 19
    assert [s.print_index for s in p.sheets if s.selected] == [1, 2]
    assert all(s.print_index is None for s in p.sheets if not s.selected)


def test_only_sheets_matches_ids_with_and_without_page_prefix() -> None:
    s = cfg("A4", "landscape", tiling={"only_sheets": ["P2-A1", "B1"]})
    p = plan(a0(index=1, page_count=2), s)
    assert [x.id for x in p.sheets if x.selected] == ["P2-A1", "P2-B1"]
    other = plan(a0(index=0, page_count=2), s)
    assert [x.id for x in other.sheets if x.selected] == ["P1-B1"]


def test_selected_blank_sheet_is_not_printed() -> None:
    p = plan(
        a0(),
        cfg("A4", "landscape", tiling={"only_sheets": ["A1", "G1"]}),
        occupancy=top_half(),
    )
    assert p.counts.printed == 1
    assert p.counts.blank == 1
    assert p.counts.deselected == 19


def test_auto_trim_shrinks_the_region_with_two_mm_safety() -> None:
    occ = RectOccupancy([Rect(100, 200, 300, 500)])
    p = plan(a0(), cfg("A4", source={"auto_trim": True}), occupancy=occ)
    assert p.source_rect_mm == (98, 198, 302, 502)
    assert p.view_size_mm == (841, 1189)
    clipped = RectOccupancy([Rect(0, 0, 100, 100)])
    q = plan(a0(), cfg("A4", source={"auto_trim": True}), occupancy=clipped)
    assert q.source_rect_mm == (0, 0, 102, 102)
    # a blank page, or no occupancy, keeps the whole region
    blank = plan(a0(), cfg("A4", source={"auto_trim": True}), occupancy=RectOccupancy([]))
    assert blank.source_rect_mm == (0, 0, 841, 1189)
    assert plan(a0(), cfg("A4", source={"auto_trim": True})).source_rect_mm == (0, 0, 841, 1189)


def test_auto_trim_within_region_and_content_outside_region() -> None:
    occ = RectOccupancy([Rect(0, 0, 841, 1189)])
    s = cfg(
        "A4",
        source={"auto_trim": True, "region_mm": {"x": 100, "y": 100, "width": 300, "height": 300}},
    )
    assert plan(a0(), s, occupancy=occ).source_rect_mm == (100, 100, 400, 400)
    outside = RectOccupancy([Rect(600, 600, 700, 700)])
    s2 = cfg(
        "A4",
        source={"auto_trim": True, "region_mm": {"x": 100, "y": 100, "width": 300, "height": 300}},
    )
    # content lies fully outside the region: nothing to trim to, region kept
    assert plan(a0(), s2, occupancy=outside).source_rect_mm == (100, 100, 400, 400)


def test_occupancy_is_mapped_through_the_rotation() -> None:
    # content in the canonical top-left corner; rotated 90 clockwise it is in the view's top-right
    occ = RectOccupancy([Rect(0, 0, 100, 100)])
    p = plan(
        a0(),
        cfg("A4", source={"rotate": 90, "auto_trim": True}),
        occupancy=occ,
    )
    assert p.view_size_mm == (1189, 841)
    assert p.source_rect_mm == pytest.approx((1087, 0, 1189, 102))


def test_grid_occupancy_drives_blank_detection() -> None:
    # 2 px/mm grid covering the top-left 300 x 300 mm only
    cells = [[x < 600 and y < 600 for x in range(1682)] for y in range(2378)]
    occ = GridOccupancy(cells, px_per_mm=2.0)
    p = plan(a0(), cfg("A4", "landscape", tiling={"skip_blank": True}), occupancy=occ)
    assert p.counts.printed == 4  # 2 columns x 2 rows touch the 300 x 300 mm corner
    assert [s.id for s in p.sheets if s.print_index] == ["A1", "A2", "B1", "B2"]


# ---------------------------------------------------------------- alternatives


def test_alternatives_across_papers_and_orientations() -> None:
    p = plan(a0(), cfg("A4"), include_alternatives=True)
    assert p.alternatives is not None
    by_key = {(a.paper_id, a.orientation.value): a for a in p.alternatives}
    assert by_key[("A4", "landscape")].sheets == 21
    assert by_key[("A4", "landscape")].current
    assert by_key[("A4", "portrait")].sheets == 25
    assert by_key[("A3", "portrait")].sheets == 9
    assert by_key[("LETTER", "portrait")].sheets == 25
    assert sum(a.current for a in p.alternatives) == 1
    sheets = [a.sheets for a in p.alternatives]
    assert sheets == sorted(sheets)
    assert "CUSTOM" not in {a.paper_id for a in p.alternatives}


def test_alternatives_use_the_profile_supported_papers_and_include_custom_current() -> None:
    printer = PrinterProfile(
        id="mine",
        name="Mine",
        supported_papers=[PaperId.A4, PaperId.A3],
        default_margins=Margins.uniform(5),
    )
    s = cfg("CUSTOM", "portrait", target={"custom_paper_mm": {"width": 420, "height": 297}})
    p = plan(a0(), s, printer=printer, include_alternatives=True)
    assert p.alternatives is not None
    assert {a.paper_id for a in p.alternatives} == {"CUSTOM", "A4", "A3"}
    assert next(a for a in p.alternatives if a.current).paper_id == "CUSTOM"


def test_alternatives_skip_invalid_papers() -> None:
    s = cfg(
        "A4",
        "portrait",
        target={"margins_mm": {"top": 40, "right": 40, "bottom": 40, "left": 40}},
        tiling={"overlap_mm": 15},
    )
    p = plan(a0(), s, include_alternatives=True)
    assert p.alternatives is not None
    assert "A6" not in {a.paper_id for a in p.alternatives}


# ------------------------------------------------------------------- misc


def test_scale_label_formats() -> None:
    assert scale_label(PercentScale(percent=100), 1.0) == "100 %"
    assert scale_label(PercentScale(percent=33.33), 0.3333) == "33.33 %"
    assert scale_label(PercentScale(percent=50.5), 0.505) == "50.5 %"
    assert scale_label(DrawingScale(from_denominator=50, to_denominator=100), 0.5) == "1:100"


def test_plan_serializes_to_the_spec_shape() -> None:
    p = plan(a0(), cfg("A4"), include_alternatives=True)
    data = p.model_dump(mode="json")
    keys = [
        "page_index", "view_size_mm", "source_rect_mm", "orientation", "paper", "margins_mm",
        "content_mm", "overlap_mm", "step_mm", "scale", "scale_label", "grid", "coverage_mm",
        "origin_mm", "offset_range_mm", "assembled_size_mm", "effective_dpi", "sheets", "counts",
        "alternatives", "savings", "warnings",
    ]  # fmt: skip
    for key in keys:
        assert key in data
    assert data["grid"] == {"cols": 3, "rows": 7}
    assert data["counts"] == {"total": 21, "printed": 21, "blank": 0, "deselected": 0}
    sheet = data["sheets"][0]
    assert set(sheet) == {
        "id",
        "row",
        "col",
        "print_index",
        "src_rect_mm",
        "blank",
        "selected",
        "neighbors",
        "cut_edges",
        "receive_edges",
    }
    assert Plan.model_validate(data) == p


def test_mirror_and_landscape_as_rotated_portrait_do_not_change_the_plan() -> None:
    base = plan(a0(), cfg("A4"))
    other = plan(
        a0(),
        cfg("A4", source={"mirror": True}, target={"landscape_as_rotated_portrait": True}),
    )
    assert base == other


def test_printer_correction_matrix() -> None:
    m = printer_correction_matrix(210, 297, 1.0, 1.0)
    assert tuple(m) == (1, 0, 0, 1, 0, 0)
    m = printer_correction_matrix(210, 297, 1.01, 0.99)
    assert m.a == pytest.approx(1 / 1.01)
    assert m.d == pytest.approx(1 / 0.99)
    assert m.b == 0
    assert m.c == 0
    # the page centre is a fixed point
    assert m.apply(105, 148.5) == pytest.approx((105, 148.5))
    # a point at distance L from the centre moves to L / k
    x, _ = m.apply(105 + 100, 148.5)
    assert x == pytest.approx(105 + 100 / 1.01)
    with pytest.raises(ValueError, match="calibration"):
        printer_correction_matrix(210, 297, 0, 1)


def test_alternatives_ignore_custom_in_the_supported_list() -> None:
    printer = PrinterProfile(
        id="p",
        name="P",
        supported_papers=[PaperId.CUSTOM, PaperId.A4],
        default_margins=Margins.uniform(5),
    )
    p = plan(a0(), cfg("A4"), printer=printer, include_alternatives=True)
    assert p.alternatives is not None
    assert {a.paper_id for a in p.alternatives} == {"A4"}


def test_savings_never_go_below_the_minimum_scale() -> None:
    page = PageGeometry(width_mm=40000, height_mm=40000)
    p = plan(
        page,
        cfg("A4", scale={"mode": "percent", "percent": 1}),
        include_alternatives=True,
        max_sheets=10**6,
    )
    assert p.savings == [] or all(s.scale >= 0.01 for s in p.savings)
