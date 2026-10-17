"""Clockwise view-rotation semantics of the render, for vector and raster sources."""

from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
import pytest
from PIL import Image

from a0td.config import AppConfig
from a0td.domain.units import mm_to_pt
from a0td.ingest import ingest_document
from a0td.render import render_job
from render_support import FIXED_NOW, make_settings, reassemble

NO_MARKS: dict[str, Any] = {
    "cut_lines": False,
    "alignment_marks": False,
    "join_diamonds": False,
    "labels": False,
    "neighbor_hints": False,
    "ruler": False,
    "assembly_page": "none",
}
# canonical top-left block lands here after a clockwise rotation: (right?, bottom?)
EXPECTED = {0: (False, False), 90: (True, False), 180: (True, True), 270: (False, True)}


def _ingest(src: Path, tmp_path: Path) -> Path:
    cfg = AppConfig(data_dir=tmp_path / "d", tmp_dir=tmp_path / "t")
    work = tmp_path / "doc"
    ingest_document(src, work, config=cfg)
    return work


def _block_quadrant(work: Path, tmp_path: Path, rotate: int) -> tuple[bool, bool]:
    res = render_job(
        work,
        make_settings(source={"rotate": rotate}, marks=NO_MARKS, tiling={"skip_blank": False}),
        tmp_path / "o",
        now=FIXED_NOW,
    )
    plan = res.plans[0]
    with pymupdf.open(tmp_path / "o" / res.outputs[0].name) as doc:
        img, _, _ = reassemble(doc, plan, 0, 40)
    ys, xs = np.where(img < 128)
    assert xs.size > 0
    return bool(xs.mean() > img.shape[1] / 2), bool(ys.mean() > img.shape[0] / 2)


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_vector_rotation_is_clockwise(tmp_path: Path, rotate: int) -> None:
    src = tmp_path / "v.pdf"
    d = pymupdf.open()
    pg = d.new_page(width=mm_to_pt(600), height=mm_to_pt(400))
    pg.draw_rect(pymupdf.Rect(0, 0, mm_to_pt(100), mm_to_pt(100)), fill=(0, 0, 0))
    d.save(src)
    d.close()
    assert _block_quadrant(_ingest(src, tmp_path), tmp_path, rotate) == EXPECTED[rotate]


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_raster_rotation_is_clockwise(tmp_path: Path, rotate: int) -> None:
    src = tmp_path / "r.png"
    arr = np.full((400, 600), 255, np.uint8)  # 100 dpi-ish: 600 x 400 px
    arr[:100, :100] = 0
    Image.fromarray(arr).save(src, dpi=(25.4, 25.4))  # 1 px = 1 mm
    assert _block_quadrant(_ingest(src, tmp_path), tmp_path, rotate) == EXPECTED[rotate]
