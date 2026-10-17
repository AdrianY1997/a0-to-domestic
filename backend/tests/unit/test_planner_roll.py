"""PAP-06 (roll / banner strips) and the exact margins of landscape_as_rotated_portrait."""

from typing import Any

import pytest

from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.page import PageGeometry
from a0td.domain.papers import Orientation, pwg_media_name
from a0td.domain.planner import plan
from a0td.domain.settings import JobSettings, Margins


def settings(**target: Any) -> JobSettings:
    return JobSettings.model_validate(
        {"target": {"margins_mm": Margins.uniform(5).model_dump(), **target}}
    )


def test_roll_single_strip_is_cut_to_length() -> None:
    # 1189 x 841 mm drawing on a 610 mm roll: 2 columns, one row, sheet as long as needed.
    s = settings(roll_mm={"width": 610, "max_length": 3000})
    p = plan(PageGeometry(width_mm=1189, height_mm=841), s)
    assert p.paper.id == "ROLL"
    assert p.orientation is Orientation.PORTRAIT
    assert (p.grid.cols, p.grid.rows) == (2, 1)
    assert p.paper.width_mm == 610
    # 841 mm of content + 5 mm margins on both sides
    assert p.paper.height_mm == pytest.approx(841 + 10)
    assert p.counts.printed == 2


def test_roll_exact_fit_has_no_extra_rows() -> None:
    s = settings(roll_mm={"width": 610, "max_length": 851})
    p = plan(PageGeometry(width_mm=500, height_mm=841), s)
    assert (p.grid.cols, p.grid.rows) == (1, 1)
    assert p.paper.height_mm == pytest.approx(851)


def test_roll_longer_than_max_length_tiles_rows() -> None:
    s = settings(roll_mm={"width": 610, "max_length": 1000})
    p = plan(PageGeometry(width_mm=500, height_mm=2500), s)
    assert p.grid.cols == 1
    assert p.grid.rows > 1
    assert p.paper.height_mm == 1000  # every strip is a full max_length sheet


def test_roll_short_banner_respects_minimum_content() -> None:
    s = settings(roll_mm={"width": 300, "max_length": 2000})
    p = plan(PageGeometry(width_mm=1000, height_mm=20), s)
    assert p.grid.rows == 1
    assert p.content_mm[1] >= 30
    assert p.paper.width_mm == 300
    assert p.paper.height_mm >= 40


def test_roll_sheet_shorter_than_width_keeps_roll_width() -> None:
    s = settings(roll_mm={"width": 610, "max_length": 3000})
    p = plan(PageGeometry(width_mm=1000, height_mm=500), s)
    assert p.paper.width_mm == 610
    assert p.paper.height_mm == pytest.approx(510)
    assert (p.grid.cols, p.grid.rows) == (2, 1)


def test_roll_20mm_banner_keeps_roll_width() -> None:
    s = settings(roll_mm={"width": 300, "max_length": 2000})
    p = plan(PageGeometry(width_mm=1000, height_mm=20), s)
    assert p.paper.width_mm == 300
    assert p.paper.height_mm < 300
    assert p.grid.cols < 10


def test_roll_max_length_below_width_keeps_roll_width() -> None:
    s = settings(roll_mm={"width": 610, "max_length": 400})
    p = plan(PageGeometry(width_mm=500, height_mm=300), s)
    assert p.paper.width_mm == 610
    assert p.paper.height_mm <= 400
    assert p.grid.cols == 1


def test_roll_shrink_with_overlap_equal_to_min_content_does_not_divide_by_zero() -> None:
    s = settings(
        roll_mm={"width": 300, "max_length": 2000},
        margins_mm=Margins.uniform(5).model_dump(),
    )
    s = s.model_copy(deep=True)
    s.tiling.overlap_mm = 30
    p = plan(PageGeometry(width_mm=1000, height_mm=20), s)
    assert p.paper.width_mm == 300
    assert p.grid.rows == 1


def test_roll_ignores_requested_landscape() -> None:
    s = settings(roll_mm={"width": 610, "max_length": 3000}, orientation="landscape")
    p = plan(PageGeometry(width_mm=1189, height_mm=841), s)
    assert p.orientation is Orientation.PORTRAIT


def test_roll_alternatives_only_list_the_roll() -> None:
    s = settings(roll_mm={"width": 610, "max_length": 3000})
    p = plan(PageGeometry(width_mm=1189, height_mm=841), s, include_alternatives=True)
    assert p.alternatives is not None
    assert {a.paper_id for a in p.alternatives} == {"ROLL"}


def test_roll_limits_are_validated() -> None:
    with pytest.raises(ValueError, match="width"):
        settings(roll_mm={"width": 10, "max_length": 3000})


def test_roll_too_narrow_for_margins_raises() -> None:
    s = settings(
        roll_mm={"width": 60, "max_length": 3000}, margins_mm=Margins.uniform(25).model_dump()
    )
    with pytest.raises(DomainError) as exc:
        plan(PageGeometry(width_mm=500, height_mm=500), s)
    assert exc.value.code is ErrorCode.CONTENT_AREA_TOO_SMALL


def test_pwg_media_names() -> None:
    assert pwg_media_name("A4", 210, 297) == "iso_a4_210x297mm"
    assert pwg_media_name("ROLL", 610, 851.25) == "custom_roll_610x851.2mm"
    assert pwg_media_name("CUSTOM", 297, 120) == "custom_custom_120x297mm"


# ------------------------------------------------------- landscape_as_rotated_portrait


def test_rotated_portrait_uses_exact_margins() -> None:
    m = {"top": 2, "right": 4, "bottom": 6, "left": 8}
    page = PageGeometry(width_mm=1000, height_mm=700)
    safe = plan(page, settings(orientation="landscape", margins_mm=m))
    exact = plan(
        page,
        settings(orientation="landscape", margins_mm=m, landscape_as_rotated_portrait=True),
    )
    # safe: top = bottom = max(l, r) = 8, left = right = max(t, b) = 6
    assert (safe.margins_mm.top, safe.margins_mm.left) == (8, 6)
    # exact: portrait top -> view right, left -> top, bottom -> left, right -> bottom
    got = exact.margins_mm
    assert (got.top, got.right, got.bottom, got.left) == (8, 2, 4, 6)
    assert exact.content_mm == pytest.approx((297 - 6 - 2, 210 - 8 - 4))


def test_rotated_portrait_does_not_touch_portrait_sheets() -> None:
    page = PageGeometry(width_mm=500, height_mm=700)
    a = plan(page, settings(orientation="portrait"))
    b = plan(page, settings(orientation="portrait", landscape_as_rotated_portrait=True))
    assert a.margins_mm == b.margins_mm
    assert a.grid == b.grid
