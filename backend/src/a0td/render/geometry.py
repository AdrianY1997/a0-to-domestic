"""Shared geometry of the render stage: view space -> sheet space and the visible source rect."""

from dataclasses import dataclass

from a0td.domain.geometry import Rect
from a0td.domain.page import ViewTransform
from a0td.domain.settings import SourceBox
from a0td.ingest.models import Page

MIN_SIDE_MM = 1e-4  # anything thinner than this is a rounding sliver, not content


@dataclass(frozen=True, slots=True)
class ViewMap:
    """Maps view space (mm) to sheet space (mm): ``p = origin + (v - view_origin) * scale``."""

    ox: float
    oy: float
    vx0: float
    vy0: float
    scale: float

    def point(self, vx: float, vy: float) -> tuple[float, float]:
        return (self.ox + (vx - self.vx0) * self.scale, self.oy + (vy - self.vy0) * self.scale)

    def rect(self, r: Rect) -> Rect:
        x0, y0 = self.point(r.x0, r.y0)
        x1, y1 = self.point(r.x1, r.y1)
        return Rect(x0, y0, x1, y1)


@dataclass(frozen=True, slots=True)
class PageView:
    """Where a canonical page sits in view space (box, rotation) for one page of a job."""

    transform: ViewTransform
    page_view: Rect  # the whole canonical page in view space (what actually has content)


def page_view_of(page: Page, box: SourceBox, rotate: int, mirror: bool = False) -> PageView:
    """The same box choice as the planner: a missing box falls back to the crop box."""
    geo = page.to_geometry()
    rect = geo.box(box) or geo.box(SourceBox.CROP)
    assert rect is not None  # crop always resolves
    tf = ViewTransform(rect, rotate, mirror)
    return PageView(tf, tf.to_view(Rect(0.0, 0.0, page.width_mm, page.height_mm)))


def visible_rect(src: Rect, region: Rect, page_view: Rect) -> Rect | None:
    """Part of a sheet's source rectangle that holds content (inside region and page)."""
    r = src.intersect(region)
    if r is not None:
        r = r.intersect(page_view)
    if r is None or r.width < MIN_SIDE_MM or r.height < MIN_SIDE_MM:
        return None
    return r
