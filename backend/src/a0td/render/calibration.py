"""Printer calibration page (SPEC 6.10 CAL-01).

A single portrait page of the chosen paper holding:

* an L-shaped pair of rulers starting at (15, 15) mm, each as long as the paper allows
  (``paper - 30 mm`` rounded down to a multiple of 10);
* a 50 mm square and a 2 in square;
* concentric frames every 0.5 mm from 1 to 10 mm inside the paper edge, labelled every mm, to read
  the real unprintable margin of each side;
* printing instructions (es / en).

Everything is vector and drawn in mm with :class:`a0td.render.canvas.Canvas`.
"""

import math
from typing import Final

import pymupdf

from a0td.domain.geometry import Rect
from a0td.domain.papers import PaperId, get_paper
from a0td.domain.units import in_to_mm, mm_to_pt
from a0td.render.canvas import Canvas, text_width_mm

RULER_ORIGIN_MM: Final = 15.0
RULER_RESERVED_MM: Final = 30.0
SQUARE_MM: Final = 50.0
SQUARE_2IN_MM: Final = in_to_mm(2.0)
FRAME_MIN_MM: Final = 1.0
FRAME_MAX_MM: Final = 10.0
FRAME_STEP_MM: Final = 0.5

_LINE_PT = 0.25
_TEXT_PT = 7.0
_LABEL_PT = 3.5
_SQUARES_TOP_MM = 28.0
_SQUARES_LEFT_MM = 30.0

_STRINGS: Final[dict[str, dict[str, str]]] = {
    "es": {
        "title": "Calibración de impresora",
        "paper": "Papel",
        "printer": "Impresora",
        "ruler_x": "Regla horizontal",
        "ruler_y": "Regla vertical",
        "sq50": "50 mm",
        "sq2in": "2 in",
        "i1": (
            "1. Imprime a tamaño real (100 %): desactiva «Ajustar a página» "
            "y «Ajustar al área imprimible»."
        ),
        "i2": "2. Mide con una regla precisa las dos reglas y anota las longitudes.",
        "i3": (
            "3. En cada lado, el primer marco que se ve completo da el margen real en mm "
            "(las etiquetas van de 1 a 10)."
        ),
        "i4": "4. Introduce los valores en el asistente de calibración.",
    },
    "en": {
        "title": "Printer calibration",
        "paper": "Paper",
        "printer": "Printer",
        "ruler_x": "Horizontal ruler",
        "ruler_y": "Vertical ruler",
        "sq50": "50 mm",
        "sq2in": "2 in",
        "i1": (
            "1. Print at actual size (100 %): turn off 'Fit to page' and 'Fit to printable area'."
        ),
        "i2": "2. Measure both rulers with an accurate ruler and note the lengths.",
        "i3": (
            "3. On each side, the first frame that shows completely gives the real margin in mm "
            "(labels run from 1 to 10)."
        ),
        "i4": "4. Enter the values in the calibration wizard.",
    },
}


def _strings(locale: str) -> dict[str, str]:
    return _STRINGS.get(locale.split("-")[0].lower(), _STRINGS["es"])


def ruler_length_mm(paper_side_mm: float) -> float:
    """Longest multiple of 10 mm that fits ``paper_side_mm - 30 mm``."""
    return float(max(10, math.floor((paper_side_mm - RULER_RESERVED_MM) / 10.0 + 1e-9) * 10))


def ruler_lengths_mm(paper: PaperId | str) -> tuple[float, float]:
    """``(horizontal, vertical)`` nominal ruler lengths for a portrait ``paper``."""
    p = get_paper(PaperId(paper).value)
    return ruler_length_mm(p.width_mm), ruler_length_mm(p.height_mm)


def frame_insets_mm() -> list[float]:
    n = round((FRAME_MAX_MM - FRAME_MIN_MM) / FRAME_STEP_MM)
    return [FRAME_MIN_MM + i * FRAME_STEP_MM for i in range(n + 1)]


def _rulers(cv: Canvas, rx: float, ry: float, caption_x: str, caption_y: str) -> None:
    ox = oy = RULER_ORIGIN_MM
    cv.line((ox, oy), (ox + rx, oy), width_pt=0.4)
    cv.line((ox, oy), (ox, oy + ry), width_pt=0.4)
    for i in range(int(rx) + 1):
        ln = 5.0 if i % 10 == 0 else 3.5 if i % 5 == 0 else 2.0
        cv.line((ox + i, oy), (ox + i, oy + ln), width_pt=0.25)
        if i % 10 == 0 and i > 0:
            t = str(i)
            cv.text(ox + i - text_width_mm(t, 5) / 2, oy + 8.2, t, 5)
    for i in range(int(ry) + 1):
        ln = 5.0 if i % 10 == 0 else 3.5 if i % 5 == 0 else 2.0
        cv.line((ox, oy + i), (ox + ln, oy + i), width_pt=0.25)
        if i % 10 == 0 and i > 0:
            cv.text(ox + 6.0, oy + i + 0.6, str(i), 5)
    cv.text(ox + rx - text_width_mm(caption_x, 5), oy + 11.5, caption_x, 5)
    # Vertical caption reads bottom to top, just right of the labels.
    cv.text(ox + 10.5, oy + ry - 2, caption_y, 5, rotate=90)


def _frames(cv: Canvas, w: float, h: float) -> None:
    for inset in frame_insets_mm():
        cv.rect(Rect(inset, inset, w - inset, h - inset), width_pt=_LINE_PT)
    white = (1.0, 1.0, 1.0)
    half = 0.45
    for k in range(int(FRAME_MIN_MM), int(FRAME_MAX_MM) + 1):
        label = str(k)
        tw = text_width_mm(label, _LABEL_PT)
        off = (k - 5.5) * 9.0
        # Top and bottom: horizontal text centred on the frame line, staggered along the side.
        cx = w / 2 + off
        for y in (k, h - k):
            cv.rect(
                Rect(cx - tw / 2 - 0.3, y - half, cx + tw / 2 + 0.3, y + half),
                fill=white,
                stroke=False,
            )
            cv.text(cx - tw / 2, y + half, label, _LABEL_PT)
        # Left and right: text reading bottom to top along the frame line.
        cy = h / 2 + off
        for x in (k, w - k):
            cv.rect(
                Rect(x - half, cy - tw / 2 - 0.3, x + half, cy + tw / 2 + 0.3),
                fill=white,
                stroke=False,
            )
            cv.text(x + half, cy + tw / 2, label, _LABEL_PT, rotate=90)


def _wrap(text: str, size_pt: float, width_mm: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and text_width_mm(trial, size_pt) > width_mm:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _squares_and_text(
    cv: Canvas, w: float, h: float, tr: dict[str, str], printer_name: str, paper_name: str
) -> None:
    x0, y0 = _SQUARES_LEFT_MM, _SQUARES_TOP_MM
    right_limit, bottom_limit = w - RULER_ORIGIN_MM, h - RULER_ORIGIN_MM
    gap = 4.0
    r50 = Rect.from_xywh(x0, y0, SQUARE_MM, SQUARE_MM)
    if x0 + SQUARE_MM + gap + SQUARE_2IN_MM <= right_limit:
        r2 = Rect.from_xywh(x0 + SQUARE_MM + gap, y0, SQUARE_2IN_MM, SQUARE_2IN_MM)
    else:
        r2 = Rect.from_xywh(x0, y0 + SQUARE_MM + gap, SQUARE_2IN_MM, SQUARE_2IN_MM)
    bottom = max(r50.y1, r2.y1)
    cv.rect(r50, width_pt=_LINE_PT)
    cv.rect(r2, width_pt=_LINE_PT)
    cv.text(r50.x0 + 2, r50.y0 + 5, tr["sq50"], 9, bold=True)
    cv.text(r2.x0 + 2, r2.y0 + 5, tr["sq2in"], 9, bold=True)

    # Text block under the squares, only as far as it fits.
    avail = right_limit - x0
    y = bottom + 7.0
    meta = f"{tr['paper']}: {paper_name}"
    if printer_name:
        meta += f" - {tr['printer']}: {printer_name}"
    blocks: list[tuple[str, float, bool]] = [(tr["title"], 11.0, True), (meta, _TEXT_PT, False)]
    blocks += [(tr[k], _TEXT_PT, False) for k in ("i1", "i2", "i3", "i4")]
    for text, size, bold in blocks:
        leading = 5.0 if bold else 3.4
        for ln in [text] if bold else _wrap(text, size, avail):
            if y + leading > bottom_limit:
                return
            cv.text(x0, y, ln, size, bold=bold)
            y += leading
        y += 1.0


def render_calibration_pdf(
    paper: PaperId | str, *, printer_name: str = "", locale: str = "es"
) -> bytes:
    """The calibration page for ``paper`` (portrait) as PDF bytes."""
    pid = PaperId(paper)
    if pid is PaperId.CUSTOM:
        raise ValueError("the calibration page needs a catalog paper")
    p = get_paper(pid.value)
    w, h = p.width_mm, p.height_mm
    rx, ry = ruler_length_mm(w), ruler_length_mm(h)
    tr = _strings(locale)
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=mm_to_pt(w), height=mm_to_pt(h))
        cv = Canvas(page, (0.0, 0.0, 0.0), _LINE_PT)
        _frames(cv, w, h)
        _rulers(cv, rx, ry, f"{tr['ruler_x']} {rx:g} mm", f"{tr['ruler_y']} {ry:g} mm")
        _squares_and_text(cv, w, h, tr, printer_name, p.name)
        cv.commit()
        doc.set_metadata({"title": f"{tr['title']} - {p.name}"})
        data: bytes = doc.tobytes(deflate=True)
    finally:
        doc.close()
    return data
