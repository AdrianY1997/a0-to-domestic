"""Axis-aligned rectangles in millimetres (origin top-left, y down)."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_xywh(cls, x: float, y: float, w: float, h: float) -> "Rect":
        return cls(x, y, x + w, y + h)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(self.width, 0.0) * max(self.height, 0.0)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    @property
    def is_empty(self) -> bool:
        return self.x1 <= self.x0 or self.y1 <= self.y0

    def intersect(self, other: "Rect") -> "Rect | None":
        """Overlap of both rectangles, or ``None`` when they do not overlap (touching = none)."""
        r = Rect(
            max(self.x0, other.x0),
            max(self.y0, other.y0),
            min(self.x1, other.x1),
            min(self.y1, other.y1),
        )
        return None if r.is_empty else r

    def intersects(self, other: "Rect") -> bool:
        return self.intersect(other) is not None

    def union(self, other: "Rect") -> "Rect":
        """Bounding box of both rectangles."""
        return Rect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def contains(self, other: "Rect", eps: float = 1e-9) -> bool:
        return (
            self.x0 - eps <= other.x0
            and self.y0 - eps <= other.y0
            and other.x1 <= self.x1 + eps
            and other.y1 <= self.y1 + eps
        )

    def translate(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def scale(self, sx: float, sy: float | None = None) -> "Rect":
        """Scale coordinates about the origin."""
        sy = sx if sy is None else sy
        return Rect(self.x0 * sx, self.y0 * sy, self.x1 * sx, self.y1 * sy)

    def inflate(self, d: float) -> "Rect":
        """Grow (or shrink, if negative) every side by ``d``."""
        return Rect(self.x0 - d, self.y0 - d, self.x1 + d, self.y1 + d)

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]
