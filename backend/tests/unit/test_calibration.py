import math

import pymupdf
import pytest

from a0td.domain.calibration import apply_calibration, compute_calibration
from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.papers import PaperId, get_paper, list_papers
from a0td.domain.settings import Margins, get_builtin_printer
from a0td.domain.units import mm_to_pt
from a0td.render.calibration import (
    frame_insets_mm,
    render_calibration_pdf,
    ruler_lengths_mm,
)

TOL = 1e-3  # pt


def _rects(paper: str) -> list[pymupdf.Rect]:
    with pymupdf.open(stream=render_calibration_pdf(paper), filetype="pdf") as doc:
        drawings = doc[0].get_drawings()
    return [d["rect"] for d in drawings if len(d["items"]) == 1 and d["items"][0][0] == "re"]


def _has(rects: list[pymupdf.Rect], x0: float, y0: float, w: float, h: float) -> bool:
    exp = [mm_to_pt(v) for v in (x0, y0, x0 + w, y0 + h)]
    return any(all(abs(a - b) < TOL for a, b in zip(r, exp, strict=True)) for r in rects)


def _square(rects: list[pymupdf.Rect], mm: float) -> list[pymupdf.Rect]:
    side = mm_to_pt(mm)
    return [r for r in rects if abs(r.width - side) < TOL and abs(r.height - side) < TOL]


def test_squares_are_exact() -> None:
    rects = _rects("A4")
    assert len(_square(rects, 50)) == 1
    s2 = _square(rects, 50.8)
    assert len(s2) == 1
    assert abs(s2[0].width / 72 - 2.0) < 1e-5


@pytest.mark.parametrize("paper", [p.id for p in list_papers()])
def test_every_paper_fits_squares_and_rulers(paper: str) -> None:
    rects = _rects(paper)
    s50, s2 = _square(rects, 50), _square(rects, 50.8)
    assert len(s50) == 1
    assert len(s2) == 1
    p = get_paper(paper)
    for r in (*s50, *s2):
        assert r.x1 <= mm_to_pt(p.width_mm - 15) + TOL
        assert r.y1 <= mm_to_pt(p.height_mm - 15) + TOL
    rx, ry = ruler_lengths_mm(paper)
    assert rx % 10 == 0
    assert ry % 10 == 0
    assert p.width_mm - 40 < rx <= p.width_mm - 30
    assert p.height_mm - 40 < ry <= p.height_mm - 30


def test_frames_every_half_mm() -> None:
    p = get_paper("A4")
    rects = _rects("A4")
    insets = frame_insets_mm()
    assert insets[0] == 1.0
    assert insets[-1] == 10.0
    assert len(insets) == 19
    for d in insets:
        assert _has(rects, d, d, p.width_mm - 2 * d, p.height_mm - 2 * d), d


def test_ruler_ticks() -> None:
    p = get_paper("A4")
    rx, ry = ruler_lengths_mm("A4")
    assert (rx, ry) == (180.0, 260.0)
    with pymupdf.open(stream=render_calibration_pdf("A4", locale="en"), filetype="pdf") as doc:
        page = doc[0]
        assert abs(page.rect.width - mm_to_pt(p.width_mm)) < TOL
        lines = [it for d in page.get_drawings() for it in d["items"] if it[0] == "l"]
    x_end = mm_to_pt(15 + rx)
    assert any(abs(it[1].x - x_end) < TOL and abs(it[2].x - x_end) < TOL for it in lines)
    y_end = mm_to_pt(15 + ry)
    assert any(math.isclose(max(it[1].y, it[2].y), y_end, abs_tol=TOL) for it in lines)


def test_instructions_in_both_languages() -> None:
    for loc, needle in (("es", "Calibración"), ("en", "calibration")):
        with pymupdf.open(stream=render_calibration_pdf("A4", locale=loc), filetype="pdf") as d:
            assert needle in d[0].get_text()


def test_compute_calibration_ok() -> None:
    res = compute_calibration(
        measured_x_mm=179.0,
        measured_y_mm=260.0,
        nominal_x_mm=180.0,
        nominal_y_mm=260.0,
        margins_mm=Margins(top=3, right=3.5, bottom=4, left=3),
    )
    assert math.isclose(res.kx, 179 / 180, abs_tol=1e-5)
    assert res.ky == 1.0
    assert (res.margins.top, res.margins.bottom) == (3.5, 4.5)
    printer = get_builtin_printer("builtin-generic")
    assert printer is not None
    out = apply_calibration(printer, PaperId.A4, res)
    assert out.margins_for(PaperId.A4) == res.margins
    assert out.margins_for(PaperId.LETTER) == printer.default_margins


@pytest.mark.parametrize(("mx", "my"), [(170.0, 260.0), (180.0, 270.0)])
def test_compute_calibration_out_of_range(mx: float, my: float) -> None:
    with pytest.raises(DomainError) as exc:
        compute_calibration(
            measured_x_mm=mx,
            measured_y_mm=my,
            nominal_x_mm=180.0,
            nominal_y_mm=260.0,
            margins_mm=Margins(),
        )
    assert exc.value.code is ErrorCode.CALIBRATION_OUT_OF_RANGE
