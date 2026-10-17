"""F3 advanced analysis and render: layers, mirror, marks layer, ink, min line width, stats."""

from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
import pytest
from PIL import Image

from a0td.config import AppConfig
from a0td.domain.units import mm_to_pt
from a0td.ingest import IngestResult, ingest_document
from a0td.ingest.stats import detect_drawing_scale
from a0td.render import render_job, render_sheet_preview
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


def _ingest(src: Path, tmp_path: Path) -> Path:
    cfg = AppConfig(data_dir=tmp_path / "d", tmp_dir=tmp_path / "t")
    work = tmp_path / "doc"
    ingest_document(src, work, config=cfg)
    return work


def _render(work: Path, out: Path, **kw: Any) -> tuple[Any, Path]:
    kw.setdefault("marks", NO_MARKS)
    kw.setdefault("tiling", {"skip_blank": False})
    res = render_job(work, make_settings(**kw), out, now=FIXED_NOW)
    return res, out / res.outputs[0].name


def _dark(pdf: Path) -> float:
    with pymupdf.open(pdf) as doc:
        total = 0.0
        for page in doc:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(0.3, 0.3), colorspace=pymupdf.csGRAY)
            total += float(255 - np.frombuffer(pix.samples, np.uint8).mean())
    return total


# ------------------------------------------------------------------------------- layers


def test_layers_listed_at_ingest(fixtures_dir: Path, tmp_path: Path) -> None:
    work = _ingest(fixtures_dir / "layers_ocg.pdf", tmp_path)
    layers = IngestResult.load(work).pages[0].layers
    assert [la.name for la in layers] == ["Talla 38", "Talla 40", "Talla 42"]


def test_hidden_layers_do_not_render_nor_print(fixtures_dir: Path, tmp_path: Path) -> None:
    work = _ingest(fixtures_dir / "layers_ocg.pdf", tmp_path)
    _, shown = _render(work, tmp_path / "s")
    layers = {la.name: la for la in IngestResult.load(work).pages[0].layers}
    _, hidden = _render(
        work,
        tmp_path / "h",
        source={"hidden_layers": ["Talla 40", str(layers["Talla 42"].xref)]},
    )
    assert _dark(hidden) < _dark(shown) * 0.7
    with pymupdf.open(hidden) as doc:
        ocgs = doc.get_ocgs()
        off = {c["text"] for c in doc.layer_ui_configs() if not c["on"]}
        assert off == {"Talla 40", "Talla 42"}
        assert "/AS" in doc.xref_object(doc.pdf_catalog())
        assert "PrintState" in "".join(doc.xref_object(x) for x in ocgs)
    _, none = _render(
        work, tmp_path / "n", source={"hidden_layers": ["Talla 38", "Talla 40", "Talla 42"]}
    )
    assert _dark(none) < 0.5


def test_hidden_layers_respected_when_rasterizing(fixtures_dir: Path, tmp_path: Path) -> None:
    work = _ingest(fixtures_dir / "layers_ocg.pdf", tmp_path)
    out = {"rasterize": True, "dpi": 150}
    _, shown = _render(work, tmp_path / "s", output=out)
    _, hidden = _render(
        work, tmp_path / "h", output=out, source={"hidden_layers": ["Talla 38", "Talla 40"]}
    )
    assert _dark(hidden) < _dark(shown) * 0.7


def test_preview_reflects_hidden_layers(fixtures_dir: Path, tmp_path: Path) -> None:
    work = _ingest(fixtures_dir / "layers_ocg.pdf", tmp_path)
    plain = render_sheet_preview(work, make_settings(marks=NO_MARKS), 0, "B2", 300)
    hidden = render_sheet_preview(
        work,
        make_settings(marks=NO_MARKS, source={"hidden_layers": ["Talla 38", "Talla 40"]}),
        0,
        "B2",
        300,
    )
    assert plain != hidden


# --------------------------------------------------------------------------- marks layer


def test_marks_layer_is_an_ocg(fixtures_dir: Path, tmp_path: Path) -> None:
    work = _ingest(fixtures_dir / "a0_vector_grid.pdf", tmp_path)
    marks = {**NO_MARKS, "marks_layer": True, "cut_lines": True, "labels": True}
    _, pdf = _render(work, tmp_path / "m", marks=marks)
    with pymupdf.open(pdf) as doc:
        ocgs = doc.get_ocgs()
        assert [v["name"] for v in ocgs.values()] == ["Marcas"]
    _, plain = _render(work, tmp_path / "p", marks={**marks, "marks_layer": False})
    assert _dark(pdf) == pytest.approx(_dark(plain), rel=0.02)


def test_marks_layer_english_name(fixtures_dir: Path, tmp_path: Path) -> None:
    work = _ingest(fixtures_dir / "a0_vector_grid.pdf", tmp_path)
    res = render_job(
        work,
        make_settings(marks={"marks_layer": True, "assembly_page": "none"}),
        tmp_path / "o",
        locale="en",
        now=FIXED_NOW,
    )
    with pymupdf.open(tmp_path / "o" / res.outputs[0].name) as doc:
        assert [v["name"] for v in doc.get_ocgs().values()] == ["Marks"]


# --------------------------------------------------------------------------------- mirror


def _quadrant(img: Any) -> tuple[bool, bool]:
    ys, xs = np.where(img < 128)
    assert xs.size > 0
    return bool(xs.mean() > img.shape[1] / 2), bool(ys.mean() > img.shape[0] / 2)


def _block_pdf(path: Path) -> None:
    d = pymupdf.open()
    pg = d.new_page(width=mm_to_pt(600), height=mm_to_pt(400))
    pg.draw_rect(pymupdf.Rect(0, 0, mm_to_pt(100), mm_to_pt(100)), fill=(0, 0, 0))
    d.save(path)
    d.close()


@pytest.mark.parametrize("rotate", [0, 90])
def test_vector_mirror(tmp_path: Path, rotate: int) -> None:
    src = tmp_path / "v.pdf"
    _block_pdf(src)
    work = _ingest(src, tmp_path)
    got = {}
    for mirror in (False, True):
        res, pdf = _render(
            work, tmp_path / str(mirror), source={"rotate": rotate, "mirror": mirror}
        )
        with pymupdf.open(pdf) as doc:
            img, _, _ = reassemble(doc, res.plans[0], 0, 40)
        got[mirror] = _quadrant(img)
    assert got[True][0] != got[False][0]
    assert got[True][1] == got[False][1]


def test_raster_mirror(tmp_path: Path) -> None:
    src = tmp_path / "r.png"
    arr = np.full((400, 600), 255, np.uint8)
    arr[:100, :100] = 0
    Image.fromarray(arr).save(src, dpi=(25.4, 25.4))
    work = _ingest(src, tmp_path)
    res, pdf = _render(work, tmp_path / "o", source={"mirror": True})
    with pymupdf.open(pdf) as doc:
        img, _, _ = reassemble(doc, res.plans[0], 0, 40)
    assert _quadrant(img) == (True, False)


def test_mirror_plan_follows_occupancy(tmp_path: Path) -> None:
    src = tmp_path / "v.pdf"
    _block_pdf(src)
    work = _ingest(src, tmp_path)
    kw = {"marks": NO_MARKS, "tiling": {"skip_blank": True}}
    plain = render_job(work, make_settings(**kw), tmp_path / "a", now=FIXED_NOW)
    mirrored = render_job(
        work, make_settings(**kw, source={"mirror": True}), tmp_path / "b", now=FIXED_NOW
    )
    assert not any(w.code == "MIRROR_NOT_IMPLEMENTED" for w in mirrored.warnings)
    p, m = plain.plans[0], mirrored.plans[0]
    assert p.counts.printed == m.counts.printed
    assert next(s for s in p.sheets if s.print_index is not None).col == 0
    assert next(s for s in m.sheets if s.print_index is not None).col == p.grid.cols - 1


# -------------------------------------------------------------------------- ink / min line


def test_ink_estimate(fixtures_dir: Path, tmp_path: Path) -> None:
    work = _ingest(fixtures_dir / "a0_vector_grid.pdf", tmp_path)
    res, _ = _render(work, tmp_path / "o")
    ink = res.ink
    assert ink is not None
    assert len(ink.sheets) == res.plans[0].counts.printed
    assert 0 < ink.total_coverage < 1
    assert ink.total_equiv_cm2 == pytest.approx(sum(s.equiv_cm2 for s in ink.sheets), abs=0.1)


def test_min_line_width_thickens_raster(tmp_path: Path) -> None:
    src = tmp_path / "l.pdf"
    d = pymupdf.open()
    pg = d.new_page(width=mm_to_pt(300), height=mm_to_pt(200))
    pg.draw_line((20, 20), (800, 20), width=0.05)
    d.save(src)
    d.close()
    work = _ingest(src, tmp_path)
    out = {"rasterize": True, "dpi": 300}
    _, thin = _render(work, tmp_path / "a", output=out)
    _, thick = _render(work, tmp_path / "b", output=out, color={"min_line_width_mm": 0.5})
    assert _dark(thick) > _dark(thin) * 2


# --------------------------------------------------------------------------- legibility


def _text_pdf(path: Path, size: float, line_w: float, extra: str = "") -> None:
    d = pymupdf.open()
    pg = d.new_page(width=mm_to_pt(420), height=mm_to_pt(297))
    for i in range(10):
        pg.insert_text((50, 80 + i * 30), f"Plano de cotas {i} {extra}", fontsize=size)
    pg.draw_line((50, 20), (700, 20), width=line_w)
    d.save(path)
    d.close()


def test_page_stats_and_scale_detection(tmp_path: Path) -> None:
    src = tmp_path / "t.pdf"
    _text_pdf(src, 10, 0.5, extra="ESCALA 1:50")
    page = IngestResult.load(_ingest(src, tmp_path)).pages[0]
    assert page.font_p10_mm == pytest.approx(10 * 25.4 / 72, rel=0.05)
    assert page.line_min_mm == pytest.approx(0.5 * 25.4 / 72, rel=0.1)
    assert page.detected_scale == 50


def test_small_text_and_thin_lines_after_scale(tmp_path: Path) -> None:
    src = tmp_path / "t.pdf"
    _text_pdf(src, 10, 0.7)
    work = _ingest(src, tmp_path)
    full = render_job(work, make_settings(marks=NO_MARKS), tmp_path / "a", now=FIXED_NOW)
    assert not {w.code for w in full.plans[0].warnings} & {"SMALL_TEXT", "THIN_LINES"}
    small = render_job(
        work,
        make_settings(marks=NO_MARKS, scale={"mode": "percent", "percent": 30}),
        tmp_path / "b",
        now=FIXED_NOW,
    )
    assert {"SMALL_TEXT", "THIN_LINES"} <= {w.code for w in small.plans[0].warnings}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1:50", 50),
        ("E 1/100", 100),
        ("ESCALA 1:200", 200),
        ("Scale 1 : 25", 25),
        ("Esc. 1:75", 75),
        ("ratio 1:7", None),
        ("Eje 5 y 2:3", None),
        ("", None),
    ],
)
def test_detect_drawing_scale(text: str, expected: int | None) -> None:
    assert detect_drawing_scale(text) == expected
