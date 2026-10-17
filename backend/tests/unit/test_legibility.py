"""ANA-08 legibility warnings (domain/legibility.py) and ANA-09 scale detection."""

import pytest

from a0td.domain.errors import WarningCode
from a0td.domain.legibility import MIN_LINE_MM, MIN_TEXT_PT, legibility_warnings
from a0td.domain.page import PageGeometry
from a0td.domain.units import MM_PER_PT
from a0td.ingest.stats import detect_drawing_scale


def _page(**kw: float) -> PageGeometry:
    return PageGeometry(width_mm=841, height_mm=1189, **kw)


def test_no_stats_no_warnings() -> None:
    assert legibility_warnings(_page(), 0.1) == []


def test_small_text_after_scaling() -> None:
    p10 = 10 * MM_PER_PT  # 10 pt at scale 1
    page = _page(font_p10_mm=p10, font_min_mm=6 * MM_PER_PT)
    assert legibility_warnings(page, 1.0) == []
    (w,) = legibility_warnings(page, 0.4)  # 4 pt p10, 2.4 pt min
    assert w.code is WarningCode.SMALL_TEXT
    assert w.params == {"p10_pt": 4.0, "min_pt": 2.4}


def test_small_text_boundary_is_not_a_warning() -> None:
    page = _page(font_p10_mm=MIN_TEXT_PT * MM_PER_PT)
    assert legibility_warnings(page, 1.0) == []


def test_min_pt_never_exceeds_p10() -> None:
    page = _page(font_p10_mm=2 * MM_PER_PT, font_min_mm=9 * MM_PER_PT)  # inconsistent stats
    (w,) = legibility_warnings(page, 1.0)
    assert w.params["min_pt"] <= w.params["p10_pt"]


def test_min_pt_falls_back_to_p10() -> None:
    page = _page(font_p10_mm=3 * MM_PER_PT)
    (w,) = legibility_warnings(page, 1.0)
    assert w.params == {"p10_pt": 3.0, "min_pt": 3.0}


def test_thin_lines_after_scaling() -> None:
    page = _page(line_min_mm=0.5)
    assert legibility_warnings(page, 0.5) == []
    (w,) = legibility_warnings(page, 0.1)
    assert w.code is WarningCode.THIN_LINES
    assert w.params == {"min_mm": 0.05}
    assert legibility_warnings(_page(line_min_mm=MIN_LINE_MM), 1.0) == []


def test_both_warnings() -> None:
    page = _page(font_p10_mm=2.0, line_min_mm=0.2)
    codes = [w.code for w in legibility_warnings(page, 0.2)]
    assert codes == [WarningCode.SMALL_TEXT, WarningCode.THIN_LINES]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ESCALA 1:50", 50),
        ("Esc. 1/100", 100),
        ("scale = 1 : 200", 200),
        ("E 1:25 y E 1:25 y E 1:50", 25),  # most frequent keyed candidate wins
        ("Plano 1:50", 50),  # unkeyed but a common denominator
        ("hora 1:37", None),  # unkeyed and not a drawing scale
        ("Escala 1:7", 7),  # keyed: any denominator is accepted
        ("Escala 1:1", None),  # 1:1 is not a reduction
        ("Escala 1:100001", None),  # above the cap
        ("relación 11:50 / 2:100", None),  # digits glued to the ratio are not "1:N"
        ("", None),
    ],
)
def test_detect_drawing_scale(text: str, expected: int | None) -> None:
    assert detect_drawing_scale(text) == expected


def test_keyed_beats_more_frequent_plain() -> None:
    assert detect_drawing_scale("1:50 1:50 1:50 ESCALA 1:100") == 100
