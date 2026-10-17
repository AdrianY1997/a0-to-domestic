"""landscape_as_rotated_portrait (SPEC 5.3): landscape sheets are printed as portrait pages."""

from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
import pytest

from a0td.config import AppConfig
from a0td.domain.units import mm_to_pt
from a0td.ingest import ingest_document
from a0td.render import render_job
from render_support import FIXED_NOW, make_settings

NO_MARKS: dict[str, Any] = {
    "cut_lines": False,
    "alignment_marks": False,
    "join_diamonds": False,
    "labels": False,
    "neighbor_hints": False,
    "ruler": False,
    "assembly_page": "none",
    "test_square": "none",
}


def _document(tmp_path: Path) -> Path:
    src = tmp_path / "v.pdf"
    d = pymupdf.open()
    pg = d.new_page(width=mm_to_pt(280), height=mm_to_pt(190))
    pg.draw_rect(pymupdf.Rect(0, 0, mm_to_pt(40), mm_to_pt(40)), fill=(0, 0, 0))  # top-left
    d.save(src)
    d.close()
    work = tmp_path / "doc"
    cfg = AppConfig(data_dir=tmp_path / "d", tmp_dir=tmp_path / "t")
    ingest_document(src, work, config=cfg)
    return work


def _render(work: Path, out: Path, **target: Any) -> pymupdf.Document:
    settings = make_settings(
        target={"orientation": "landscape", **target},
        marks=NO_MARKS,
        tiling={"skip_blank": False},
    )
    res = render_job(work, settings, out, now=FIXED_NOW)
    return pymupdf.open(out / res.outputs[0].name)


def _dark_bbox_mm(page: pymupdf.Page) -> tuple[float, float, float, float]:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csGRAY, alpha=False)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    ys, xs = np.where(arr < 128)
    mm = 25.4 / (72 * 2)
    return xs.min() * mm, ys.min() * mm, (xs.max() + 1) * mm, (ys.max() + 1) * mm


def test_sheets_become_portrait_pages_with_content_turned(tmp_path: Path) -> None:
    work = _document(tmp_path)
    with (
        _render(work, tmp_path / "plain") as plain,
        _render(work, tmp_path / "rot", landscape_as_rotated_portrait=True) as rot,
    ):
        assert plain[0].rect.width > plain[0].rect.height  # control: ordinary landscape
        pg = rot[0]
        assert pg.rect.width == pytest.approx(mm_to_pt(210), abs=0.01)
        assert pg.rect.height == pytest.approx(mm_to_pt(297), abs=0.01)
        x0, y0, x1, y1 = _dark_bbox_mm(pg)
        px0, py0, px1, py1 = _dark_bbox_mm(plain[0])
        # landscape top-left block -> portrait bottom-left (turned counter-clockwise)
        assert (x1 - x0, y1 - y0) == pytest.approx((py1 - py0, px1 - px0), abs=0.6)
        assert x0 == pytest.approx(py0, abs=0.6)
        assert y1 == pytest.approx(297 - px0, abs=0.6)


def test_exact_margins_are_used(tmp_path: Path) -> None:
    """With unequal margins the landscape view uses them rotated, not the 'safe' maxima."""
    work = _document(tmp_path)
    m = {"top": 2, "right": 4, "bottom": 6, "left": 8}
    settings = make_settings(
        target={
            "orientation": "landscape",
            "landscape_as_rotated_portrait": True,
            "margins_mm": m,
        },
        marks=NO_MARKS,
        tiling={"skip_blank": False},
    )
    res = render_job(work, settings, tmp_path / "o", now=FIXED_NOW)
    plan = res.plans[0]
    assert (plan.margins_mm.top, plan.margins_mm.right) == (8, 2)
    assert (plan.margins_mm.bottom, plan.margins_mm.left) == (4, 6)
    assert plan.paper.width_mm == pytest.approx(297)  # the plan stays in view space


def test_rotated_pages_in_a_multi_sheet_job_keep_count(tmp_path: Path) -> None:
    work = _document(tmp_path)
    settings = make_settings(
        target={"orientation": "landscape", "landscape_as_rotated_portrait": True},
        scale={"mode": "percent", "percent": 200},
        marks={**NO_MARKS, "assembly_page": "first"},
        tiling={"skip_blank": False},
    )
    res = render_job(work, settings, tmp_path / "o", now=FIXED_NOW)
    with pymupdf.open(tmp_path / "o" / res.outputs[0].name) as doc:
        assert doc.page_count == res.plans[0].counts.printed + 1
        assert all(p.rect.width < p.rect.height for p in doc)
