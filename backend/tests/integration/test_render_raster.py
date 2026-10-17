"""Raster render end to end: scan (G4) and photo, crops, compression, max_dpi, reassembly."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from a0td.domain.units import mm_to_pt
from a0td.ingest import IngestResult
from a0td.render import RenderResult
from render_support import (
    FIXED_NOW,
    direct_raster,
    make_settings,
    mismatch_fraction,
    reassemble,
)

Render = Callable[..., tuple[RenderResult, Path]]
NO_MARKS: dict[str, Any] = {
    "cut_lines": False,
    "alignment_marks": False,
    "join_diamonds": False,
    "labels": False,
    "neighbor_hints": False,
    "ruler": False,
    "assembly_page": "none",
}


def _images(doc: pymupdf.Document, pno: int) -> list[tuple[Any, ...]]:
    return list(doc[pno].get_images(full=True))


def test_scan_g4_pages_and_one_bit_crops(render: Render) -> None:
    result, pdf = render("scan_a0_200dpi_g4.tif")
    plan = result.plans[0]
    assert (plan.grid.cols, plan.grid.rows) == (3, 7)
    assert result.outputs[0].pages == 22
    with pymupdf.open(pdf) as doc:
        for page in doc:
            assert page.rect.width == pytest.approx(mm_to_pt(297), abs=0.01)
            assert page.rect.height == pytest.approx(mm_to_pt(210), abs=0.01)
        # every sheet embeds only its own crop (<= content area at 200 dpi + rounding), 1 bit
        for pno in range(1, 22):
            imgs = _images(doc, pno)
            assert len(imgs) == 1
            _xref, _, w, h, bpc = imgs[0][:5]
            assert bpc == 1
            assert w <= 287 / 25.4 * 200 + 3
            assert h <= 200 / 25.4 * 200 + 3
    # compressed far below the raw 1-bit size of 21 full tiles
    assert result.outputs[0].size_bytes < 3_000_000


def test_scan_blank_tiles_embed_nothing(render: Render) -> None:
    _result, pdf = render("scan_a0_200dpi_g4.tif", tiling={"skip_blank": False})
    with pymupdf.open(pdf) as doc:
        counts = [len(_images(doc, p)) for p in range(1, doc.page_count)]
    assert all(c <= 1 for c in counts)


def test_photo_uses_jpeg_and_warns_low_dpi(render: Render) -> None:
    result, pdf = render("photo_nodpi.jpg")
    plan = result.plans[0]
    assert plan.effective_dpi == pytest.approx(120.8, abs=0.5)
    assert any(w.code.value == "LOW_DPI" for w in plan.warnings)
    with pymupdf.open(pdf) as doc:
        page = doc[1]
        (xref, *_rest) = page.get_images(full=True)[0]
        assert doc.xref_get_key(xref, "Filter")[1] == "/DCTDecode"
        assert doc[1].rect.width == pytest.approx(mm_to_pt(297), abs=0.01)


def test_max_dpi_downsamples_only_above_limit(render: Render) -> None:
    _, pdf_hi = render("scan_a0_200dpi_g4.tif", output={"max_dpi": 600}, marks=NO_MARKS)
    with pymupdf.open(pdf_hi) as doc:
        w_hi = max(_images(doc, p)[0][2] for p in range(doc.page_count) if _images(doc, p))
    _, pdf_lo = render("scan_a0_200dpi_g4.tif", output={"max_dpi": 100}, marks=NO_MARKS)
    with pymupdf.open(pdf_lo) as doc:
        w_lo = max(_images(doc, p)[0][2] for p in range(doc.page_count) if _images(doc, p))
    assert w_hi <= 287 / 25.4 * 200 + 3  # never upsampled to 600
    assert w_lo <= 287 / 25.4 * 100 + 3  # reduced to the limit
    assert w_lo < w_hi / 1.8


def test_raster_effective_scale_exact(render: Render) -> None:
    """The crop is placed at the rectangle of its whole pixels: scale stays exactly s."""
    result, pdf = render("scan_a0_200dpi_g4.tif", marks=NO_MARKS, tiling={"skip_blank": False})
    plan = result.plans[0]
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        info = page.get_image_info(xrefs=True)[0]
        x0, y0, x1, y1 = info["bbox"]
        w_px, h_px = info["width"], info["height"]
    pt_per_px = (x1 - x0) / w_px
    assert pt_per_px == pytest.approx(72 / 200 / plan.scale, rel=1e-4)
    assert (y1 - y0) / h_px == pytest.approx(pt_per_px, rel=1e-4)


def test_reassembly_raster_scan_coarse(render: Render, rfx_ingest: Callable[[str], Path]) -> None:
    """The G4 scan has 1 px lines: compare at a quarter of the resolution (resampling differs)."""
    import numpy as np

    result, pdf = render("scan_a0_200dpi_g4.tif", marks=NO_MARKS)
    plan = result.plans[0]
    work = rfx_ingest("scan_a0_200dpi_g4.tif")
    page = IngestResult.load(work).pages[0]
    with pymupdf.open(pdf) as doc:
        got, _, _ = reassemble(doc, plan, 0, 150)
    want = direct_raster(work / "page-0.tif", plan, page.width_mm, page.height_mm, 150)
    h, w = min(got.shape[0], want.shape[0]) // 4 * 4, min(got.shape[1], want.shape[1]) // 4 * 4

    def small(a: Any) -> Any:
        return a[:h, :w].astype(float).reshape(h // 4, 4, w // 4, 4).mean(axis=(1, 3))

    assert float(np.abs(small(got) - small(want)).mean()) < 6.0


@pytest.mark.parametrize("mode", ["L", "RGB"])
def test_reassembly_raster_exact(tmp_path: Path, mode: str) -> None:
    """Large filled shapes: tiles reassemble to the direct resample within 1 px (SPEC 15.4)."""
    import numpy as np
    from PIL import Image

    from a0td.config import AppConfig
    from a0td.ingest import ingest_document
    from a0td.render import render_job

    rng = np.random.default_rng(7)
    arr = np.full((3200, 2400), 255, np.uint8)
    for _ in range(150):
        x, y = int(rng.integers(0, 2300)), int(rng.integers(0, 3100))
        w, h = int(rng.integers(30, 220)), int(rng.integers(30, 220))
        arr[y : y + h, x : x + w] = int(rng.choice([0, 40, 90]))
    img = Image.fromarray(arr).convert(mode)
    src = tmp_path / "shapes.tif"
    img.save(src, dpi=(100, 100))
    work = tmp_path / "doc"
    cfg = AppConfig(data_dir=tmp_path / "d", tmp_dir=tmp_path / "t")
    res = ingest_document(src, work, config=cfg)
    settings = make_settings(
        marks=NO_MARKS, target={"orientation": "portrait"}, tiling={"overlap_mm": 8}
    )
    out = render_job(work, settings, tmp_path / "out", now=FIXED_NOW)
    plan = out.plans[0]
    assert plan.counts.printed >= 6
    with pymupdf.open(tmp_path / "out" / out.outputs[0].name) as doc:
        got, xs, ys = reassemble(doc, plan, 0, 150)
    want = direct_raster(
        work / "page-0.tif", plan, res.pages[0].width_mm, res.pages[0].height_mm, 150
    )
    assert abs(got.shape[0] - want.shape[0]) <= 2
    assert abs(got.shape[1] - want.shape[1]) <= 2
    assert mismatch_fraction(got, want, seams_x=xs, seams_y=ys, threshold=128) < 0.003


@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_raster_rotation_matches_vector_semantics(render: Render, rotate: int) -> None:
    """A rotated raster job covers the rotated view: same sheet count as the swapped page."""
    result, pdf = render("scan_a0_200dpi_g4.tif", source={"rotate": rotate}, marks=NO_MARKS)
    plan = result.plans[0]
    assert plan.view_size_mm[0] == pytest.approx(1189 if rotate in (90, 270) else 841, abs=1.5)
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == plan.counts.printed


def test_multipage_tiff_prefixes_ids(render: Render) -> None:
    result, pdf = render(
        "multipage.tif", scale={"mode": "percent", "percent": 400}, marks={"assembly_page": "none"}
    )
    assert len(result.plans) == 3
    ids = [s.id for p in result.plans for s in p.sheets if s.print_index is not None]
    assert all(i.startswith(("P1-", "P2-", "P3-")) for i in ids)
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == len(ids)
        assert "P2-" in "".join(p.get_text() for p in doc)
