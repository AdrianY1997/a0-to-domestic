"""Page geometry (input of the planner) and the canonical-page -> view-space transform."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from a0td.domain.geometry import Rect
from a0td.domain.settings import SourceBox

Box = tuple[float, float, float, float]
Rotation = Literal[0, 90, 180, 270]


class PageGeometry(BaseModel):
    """What the planner needs to know about one canonical page (SPEC 7.3 / 11.1 ``Page``).

    ``width_mm`` x ``height_mm`` is the canonical page (the CropBox, origin at its corner).
    ``boxes_mm`` holds ``[x0, y0, x1, y1]`` boxes in the canonical frame; a missing ``crop``
    means ``[0, 0, width_mm, height_mm]``.
    """

    model_config = ConfigDict(extra="forbid")

    index: int = Field(default=0, ge=0)
    page_count: int = Field(default=1, ge=1)  # pages in the job; >1 adds the ``P2-`` id prefix
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    boxes_mm: dict[SourceBox, Box] = Field(default_factory=dict)
    px: tuple[int, int] | None = None  # raster pages: pixel size of the canonical page
    physical_size_assumed: bool = False  # ANA-03: size was assumed, not read from metadata
    # ANA-08 legibility stats of the canonical page, in mm at scale 1 (None: unknown / no text).
    font_min_mm: float | None = Field(default=None, gt=0)
    font_p10_mm: float | None = Field(default=None, gt=0)
    line_min_mm: float | None = Field(default=None, gt=0)

    @property
    def is_raster(self) -> bool:
        return self.px is not None

    def box(self, which: SourceBox) -> Rect | None:
        """The requested box as a :class:`Rect`, or ``None`` when the page does not have it."""
        raw = self.boxes_mm.get(which)
        if raw is not None:
            return Rect(*raw)
        if which is SourceBox.CROP:
            return Rect(0.0, 0.0, self.width_mm, self.height_mm)
        return None


@dataclass(frozen=True, slots=True)
class ViewTransform:
    """Canonical frame <-> view space (box origin + clockwise rotation, SPEC 5.2).

    ``box`` is the selected box in canonical coordinates; view space has its origin at the top-left
    of the rotated box, y down, in mm. ``mirror`` (ESC-06) flips the rotated box horizontally.
    """

    box: Rect
    rotate: int = 0
    mirror: bool = False

    @property
    def view_size(self) -> tuple[float, float]:
        w, h = self.box.width, self.box.height
        return (h, w) if self.rotate in (90, 270) else (w, h)

    def _point_to_view(self, x: float, y: float) -> tuple[float, float]:
        vx, vy = self._rotate_to_view(x, y)
        return (self.view_size[0] - vx, vy) if self.mirror else (vx, vy)

    def _rotate_to_view(self, x: float, y: float) -> tuple[float, float]:
        x -= self.box.x0
        y -= self.box.y0
        bw, bh = self.box.width, self.box.height
        if self.rotate == 90:
            return (bh - y, x)
        if self.rotate == 180:
            return (bw - x, bh - y)
        if self.rotate == 270:
            return (y, bw - x)
        return (x, y)

    def _point_to_canonical(self, x: float, y: float) -> tuple[float, float]:
        if self.mirror:
            x = self.view_size[0] - x
        bw, bh = self.box.width, self.box.height
        if self.rotate == 90:
            x, y = y, bh - x
        elif self.rotate == 180:
            x, y = bw - x, bh - y
        elif self.rotate == 270:
            x, y = bw - y, x
        return (x + self.box.x0, y + self.box.y0)

    def to_view(self, rect: Rect) -> Rect:
        """Map a canonical-frame rectangle into view space."""
        ax, ay = self._point_to_view(rect.x0, rect.y0)
        bx, by = self._point_to_view(rect.x1, rect.y1)
        return Rect(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))

    def to_canonical(self, rect: Rect) -> Rect:
        """Map a view-space rectangle back into the canonical frame."""
        ax, ay = self._point_to_canonical(rect.x0, rect.y0)
        bx, by = self._point_to_canonical(rect.x1, rect.y1)
        return Rect(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
