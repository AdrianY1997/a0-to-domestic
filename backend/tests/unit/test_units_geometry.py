import pytest

from a0td.domain.geometry import Rect
from a0td.domain.units import (
    ceil_eps,
    floor_eps,
    in_to_mm,
    mm_to_in,
    mm_to_pt,
    pt_to_mm,
)


def test_pt_mm_roundtrip() -> None:
    assert pt_to_mm(72) == pytest.approx(25.4)
    assert mm_to_pt(25.4) == pytest.approx(72)
    assert pt_to_mm(mm_to_pt(123.4)) == pytest.approx(123.4)


def test_inch_conversions() -> None:
    assert in_to_mm(8.5) == pytest.approx(215.9)
    assert mm_to_in(215.9) == pytest.approx(8.5)


@pytest.mark.parametrize(
    ("x", "expected"),
    [(2.0, 2), (2.0 + 1e-10, 2), (2.0 + 1e-6, 3), (0.0, 0), (-0.5, 0), (2.5, 3)],
)
def test_ceil_eps(x: float, expected: int) -> None:
    assert ceil_eps(x) == expected


def test_floor_eps() -> None:
    assert floor_eps(3.0 - 1e-10) == 3
    assert floor_eps(2.9) == 2


def test_rect_basics() -> None:
    r = Rect.from_xywh(10, 20, 30, 40)
    assert (r.width, r.height, r.area) == (30, 40, 1200)
    assert r.center == (25, 40)
    assert r.as_list() == [10, 20, 40, 60]


def test_rect_intersect_union() -> None:
    a = Rect(0, 0, 10, 10)
    b = Rect(5, 5, 15, 15)
    assert a.intersect(b) == Rect(5, 5, 10, 10)
    assert a.union(b) == Rect(0, 0, 15, 15)
    assert a.intersect(Rect(10, 0, 20, 10)) is None  # touching only
    assert not a.intersects(Rect(11, 11, 12, 12))


def test_rect_transforms() -> None:
    r = Rect(0, 0, 10, 20)
    assert r.translate(1, 2) == Rect(1, 2, 11, 22)
    assert r.scale(2) == Rect(0, 0, 20, 40)
    assert r.inflate(1) == Rect(-1, -1, 11, 21)
    assert r.contains(Rect(1, 1, 9, 9))
    assert not r.contains(Rect(-1, 0, 5, 5))
    assert r.contains_point(10, 20)
    assert Rect(0, 0, 0, 5).is_empty
