"""Occupancy map protocol (SPEC 9.6) and simple in-memory implementations.

Coordinates are millimetres in the **canonical page frame** (the frame the page boxes of
:class:`~a0td.domain.page.PageGeometry` live in: origin top-left, y down). The planner maps
view-space rectangles into this frame before querying, so implementations never see rotations.
"""

import math
from collections.abc import Iterable, Sequence
from functools import reduce
from typing import Protocol

from a0td.domain.geometry import Rect
from a0td.domain.units import EPS


class OccupancyMap(Protocol):
    """Where a page has non-blank content."""

    def any_in(self, rect: Rect) -> bool:
        """Whether any content lies inside ``rect`` (touching only the edge is not inside)."""
        ...

    def content_bbox(self) -> Rect | None:
        """Bounding box of all content, or ``None`` for a blank page."""
        ...


class RectOccupancy:
    """Occupancy defined by a list of content rectangles."""

    def __init__(self, rects: Iterable[Rect]) -> None:
        self._rects: tuple[Rect, ...] = tuple(r for r in rects if not r.is_empty)

    def any_in(self, rect: Rect) -> bool:
        return any(r.intersects(rect) for r in self._rects)

    def content_bbox(self) -> Rect | None:
        if not self._rects:
            return None
        return reduce(Rect.union, self._rects)


class GridOccupancy:
    """Occupancy defined by a boolean pixel grid (``cells[row][col]``).

    The grid has ``px_per_mm`` pixels per millimetre (2.0 in production) and its top-left corner
    sits at ``origin`` (mm).
    """

    def __init__(
        self,
        cells: Sequence[Sequence[bool]],
        *,
        px_per_mm: float = 2.0,
        origin: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        if px_per_mm <= 0:
            raise ValueError("px_per_mm must be > 0")
        self._cells = [list(row) for row in cells]
        self._rows = len(self._cells)
        self._cols = max((len(r) for r in self._cells), default=0)
        self._ppm = px_per_mm
        self._ox, self._oy = origin

    def any_in(self, rect: Rect) -> bool:
        ppm = self._ppm
        c0 = max(0, math.floor((rect.x0 - self._ox) * ppm + EPS))
        c1 = min(self._cols, math.ceil((rect.x1 - self._ox) * ppm - EPS))
        r0 = max(0, math.floor((rect.y0 - self._oy) * ppm + EPS))
        r1 = min(self._rows, math.ceil((rect.y1 - self._oy) * ppm - EPS))
        if c1 <= c0 or r1 <= r0:  # rect lies outside the grid; avoid negative slices
            return False
        for r in range(r0, r1):
            row = self._cells[r]
            if any(row[c0 : min(c1, len(row))]):
                return True
        return False

    def content_bbox(self) -> Rect | None:
        xs: list[int] = []
        ys: list[int] = []
        for r, row in enumerate(self._cells):
            cols = [c for c, v in enumerate(row) if v]
            if cols:
                ys.append(r)
                xs.extend((cols[0], cols[-1]))
        if not ys:
            return None
        ppm = self._ppm
        return Rect(
            self._ox + min(xs) / ppm,
            self._oy + ys[0] / ppm,
            self._ox + (max(xs) + 1) / ppm,
            self._oy + (ys[-1] + 1) / ppm,
        )
