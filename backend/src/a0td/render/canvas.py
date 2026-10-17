"""A tiny drawing helper over a PyMuPDF page: coordinates in millimetres (origin top-left)."""

from collections.abc import Sequence
from functools import lru_cache

import pymupdf

from a0td.domain.geometry import Rect
from a0td.domain.units import MM_PER_PT, mm_to_pt

Color = tuple[float, float, float]
Pt = tuple[float, float]

FONT = "helv"
FONT_BOLD = "hebo"


def parse_color(value: str) -> Color:
    """``#RRGGBB`` -> floats in 0..1."""
    v = value.lstrip("#")
    return (int(v[0:2], 16) / 255.0, int(v[2:4], 16) / 255.0, int(v[4:6], 16) / 255.0)


@lru_cache(maxsize=2)
def _font(bold: bool) -> pymupdf.Font:
    return pymupdf.Font(FONT_BOLD if bold else FONT)


def text_width_mm(text: str, size_pt: float, bold: bool = False) -> float:
    """Width of ``text`` in Helvetica(-Bold) at ``size_pt``, in mm."""
    width_pt: float = _font(bold).text_length(text, fontsize=size_pt)
    return width_pt * MM_PER_PT


def pt_to_mm_size(size_pt: float) -> float:
    return size_pt * MM_PER_PT


def dash_pattern(on_mm: float, off_mm: float) -> str:
    return f"[{mm_to_pt(on_mm):.4f} {mm_to_pt(off_mm):.4f}] 0"


class Canvas:
    """Vector drawing in mm with a default colour and line width (marks colour / width)."""

    def __init__(self, page: pymupdf.Page, color: Color, width_pt: float, oc: int = 0) -> None:
        self.page = page
        self.oc = oc  # xref of the optional content group everything is drawn into (0: none)
        self.color = color
        self.width_pt = width_pt
        self._shape = page.new_shape()

    @staticmethod
    def _p(x: float, y: float) -> pymupdf.Point:
        return pymupdf.Point(mm_to_pt(x), mm_to_pt(y))

    def line(
        self,
        p0: Pt,
        p1: Pt,
        *,
        dashes: str | None = None,
        width_pt: float | None = None,
        color: Color | None = None,
    ) -> None:
        self._shape.draw_line(self._p(*p0), self._p(*p1))
        self._shape.finish(
            color=color or self.color,
            width=self.width_pt if width_pt is None else width_pt,
            dashes=dashes,
            closePath=False,
            oc=self.oc,
        )

    def polygon(
        self,
        points: Sequence[Pt],
        *,
        fill: Color | None = None,
        stroke: bool = True,
        width_pt: float | None = None,
        color: Color | None = None,
    ) -> None:
        self._shape.draw_polyline([self._p(*p) for p in points])
        col = color or self.color
        self._shape.finish(
            color=col if stroke else None,
            fill=fill,
            width=self.width_pt if width_pt is None else width_pt,
            closePath=True,
            oc=self.oc,
        )

    def rect(
        self,
        r: Rect,
        *,
        fill: Color | None = None,
        fill_opacity: float = 1.0,
        stroke: bool = True,
        width_pt: float | None = None,
        color: Color | None = None,
    ) -> None:
        self._shape.draw_rect(pymupdf.Rect(*(mm_to_pt(v) for v in r.as_list())))
        self._shape.finish(
            color=(color or self.color) if stroke else None,
            fill=fill,
            fill_opacity=fill_opacity,
            width=self.width_pt if width_pt is None else width_pt,
            oc=self.oc,
        )

    def text(
        self,
        x: float,
        y: float,
        text: str,
        size_pt: float,
        *,
        bold: bool = False,
        rotate: int = 0,
        color: Color | None = None,
    ) -> None:
        """Text with its baseline starting at ``(x, y)`` mm; ``rotate`` counter-clockwise."""
        self._shape.insert_text(
            self._p(x, y),
            text,
            fontsize=size_pt,
            fontname=FONT_BOLD if bold else FONT,
            color=color or self.color,
            rotate=rotate,
            oc=self.oc,
        )

    def commit(self) -> None:
        self._shape.commit()


class Frame:
    """A local ``(u, v)`` frame in mm: ``u`` along the text direction, ``v`` towards its bottom.

    ``rot=0`` reads left to right; ``rot=90`` reads bottom to top (text rotated counter-clockwise),
    used for labels in a vertical strip.
    """

    def __init__(self, ox: float, oy: float, rot: int = 0) -> None:
        if rot not in (0, 90):
            raise ValueError("rot must be 0 or 90")
        self.ox, self.oy, self.rot = ox, oy, rot

    def to_page(self, u: float, v: float) -> Pt:
        if self.rot == 90:
            return (self.ox + v, self.oy - u)
        return (self.ox + u, self.oy + v)

    def text(
        self,
        cv: Canvas,
        u: float,
        v: float,
        text: str,
        size_pt: float,
        *,
        bold: bool = False,
        color: Color | None = None,
    ) -> None:
        x, y = self.to_page(u, v)
        cv.text(x, y, text, size_pt, bold=bold, rotate=self.rot, color=color)

    def line(
        self,
        cv: Canvas,
        a: Pt,
        b: Pt,
        *,
        width_pt: float | None = None,
        color: Color | None = None,
    ) -> None:
        cv.line(self.to_page(*a), self.to_page(*b), width_pt=width_pt, color=color)
