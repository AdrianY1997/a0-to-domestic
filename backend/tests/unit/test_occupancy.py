from a0td.domain.geometry import Rect
from a0td.domain.occupancy import GridOccupancy, RectOccupancy
from a0td.domain.page import PageGeometry
from a0td.domain.settings import SourceBox


def test_rect_occupancy() -> None:
    occ = RectOccupancy([Rect(10, 10, 20, 20), Rect(50, 50, 60, 80), Rect(5, 5, 5, 9)])
    assert occ.any_in(Rect(0, 0, 15, 15))
    assert not occ.any_in(Rect(20, 0, 30, 30))  # touching the edge only
    assert not occ.any_in(Rect(30, 30, 40, 40))
    assert occ.content_bbox() == Rect(10, 10, 60, 80)  # the empty rect is ignored
    assert RectOccupancy([]).content_bbox() is None
    assert not RectOccupancy([]).any_in(Rect(0, 0, 10, 10))


def test_grid_occupancy() -> None:
    cells = [[False] * 10 for _ in range(10)]
    cells[2][3] = True
    cells[6][8] = True
    occ = GridOccupancy(cells, px_per_mm=2.0, origin=(100.0, 50.0))
    # pixel (row 2, col 3) covers x 101.5-102, y 51-51.5
    assert occ.any_in(Rect(101, 50, 103, 52))
    assert not occ.any_in(Rect(102, 50, 110, 52))  # touching the right edge
    assert not occ.any_in(Rect(100, 52, 101, 53))
    assert occ.any_in(Rect(0, 0, 1000, 1000))
    assert not occ.any_in(Rect(-50, -50, 90, 40))
    assert occ.content_bbox() == Rect(101.5, 51.0, 104.5, 53.5)
    assert GridOccupancy([[False, False]]).content_bbox() is None
    assert GridOccupancy([]).content_bbox() is None
    ragged = GridOccupancy([[True], [False, True]])
    assert ragged.any_in(Rect(0.5, 0.5, 1.0, 1.0))


def test_grid_occupancy_outside_grid_is_empty() -> None:
    # all-True grid: any in-grid rect would hit, so False proves the range was rejected
    occ = GridOccupancy([[True] * 10 for _ in range(10)], px_per_mm=2.0, origin=(100.0, 0.0))
    assert occ.any_in(Rect(100, 0, 105, 5))
    assert not occ.any_in(Rect(-50, 0, 99, 5))  # entirely left of the grid
    assert not occ.any_in(Rect(100, -20, 105, -1))  # entirely above the grid


def test_grid_occupancy_rejects_bad_resolution() -> None:
    import pytest

    with pytest.raises(ValueError, match="px_per_mm"):
        GridOccupancy([[True]], px_per_mm=0)


def test_page_geometry_boxes() -> None:
    page = PageGeometry(width_mm=100, height_mm=200, boxes_mm={SourceBox.TRIM: (5, 5, 95, 195)})
    assert page.box(SourceBox.CROP) == Rect(0, 0, 100, 200)
    assert page.box(SourceBox.TRIM) == Rect(5, 5, 95, 195)
    assert page.box(SourceBox.BLEED) is None
    assert not page.is_raster
    assert PageGeometry(width_mm=1, height_mm=1, px=(10, 10)).is_raster
