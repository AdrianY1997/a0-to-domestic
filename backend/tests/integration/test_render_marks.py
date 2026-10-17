"""Marks, labels, rulers and printer correction (SPEC 5.5, 5.8, 5.9)."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from a0td.config import AppConfig
from a0td.domain.settings import PrinterProfile
from a0td.domain.units import mm_to_pt
from a0td.ingest import ingest_document
from a0td.render import RenderResult, render_job
from render_support import FIXED_NOW, make_settings, nonwhite_bbox_mm

MM = 72 / 25.4  # points per mm
PORTRAIT: dict[str, Any] = {"orientation": "portrait"}
BASE_MARKS: dict[str, Any] = {"assembly_page": "none"}


@pytest.fixture(scope="module")
def blank_doc(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A blank 400 x 600 mm PDF: only the marks are drawn on the sheets."""
    root = tmp_path_factory.mktemp("blank")
    src = root / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page(width=mm_to_pt(400), height=mm_to_pt(600))
    doc.save(src)
    cfg = AppConfig(data_dir=root / "d", tmp_dir=root / "t")
    work = root / "doc"
    ingest_document(src, work, config=cfg)
    return work


Run = Callable[..., tuple[RenderResult, pymupdf.Document]]


@pytest.fixture
def run(blank_doc: Path, tmp_path: Path) -> Run:
    opened: list[pymupdf.Document] = []

    def _run(**kw: Any) -> tuple[RenderResult, pymupdf.Document]:
        printer = kw.pop("printer", None)
        kw["target"] = {**PORTRAIT, **kw.get("target", {})}
        kw["tiling"] = {"skip_blank": False, **kw.get("tiling", {})}
        kw["marks"] = {**BASE_MARKS, **kw.get("marks", {})}
        res = render_job(
            blank_doc, make_settings(**kw), tmp_path / "o", now=FIXED_NOW, printer=printer
        )
        doc = pymupdf.open(tmp_path / "o" / res.outputs[0].name)
        opened.append(doc)
        return res, doc

    yield _run
    for d in opened:
        d.close()


def _page_of(res: RenderResult, sheet_id: str) -> int:
    ids = [s.id for s in res.plans[0].sheets if s.print_index is not None]
    return ids.index(sheet_id)


def _words(page: pymupdf.Page) -> dict[str, pymupdf.Rect]:
    return {w[4]: pymupdf.Rect(w[:4]) for w in page.get_text("words")}


def _lines(page: pymupdf.Page, *, dashed: bool | None = None) -> list[tuple[Any, Any]]:
    """Every straight segment of the page's vector drawings (PyMuPDF merges paths per style)."""
    out: list[tuple[Any, Any]] = []
    for d in page.get_drawings():
        if dashed is not None and bool(d.get("dashes") and "8.5" in d["dashes"]) != dashed:
            continue
        out += [(it[1], it[2]) for it in d["items"] if it[0] == "l"]
    return out


def _long_vlines(page: pymupdf.Page) -> list[tuple[Any, Any]]:
    return [(a, b) for a, b in _lines(page, dashed=False) if a.x == b.x and abs(a.y - b.y) > 300]


def _dark_at(page: pymupdf.Page, x_mm: float, y_mm: float) -> bool:
    pix = page.get_pixmap(
        dpi=300,
        colorspace=pymupdf.csGRAY,
        alpha=False,
        clip=pymupdf.Rect(
            (x_mm - 0.2) * MM, (y_mm - 0.2) * MM, (x_mm + 0.2) * MM, (y_mm + 0.2) * MM
        ),
    )
    return int(pix.samples[len(pix.samples) // 2]) < 200


def _dashed(page: pymupdf.Page) -> list[dict[str, Any]]:
    return [d for d in page.get_drawings() if d.get("dashes") and "8.5" in d["dashes"]]


def test_all_marks_inside_printable_area(run: Run) -> None:
    res, doc = run(
        marks={
            "glue_shading": True,
            "outer_cut_lines": True,
            "color": "#000000",
            "line_width_pt": 1.0,
        }
    )
    plan = res.plans[0]
    ox, oy = plan.content_origin_mm
    cw, ch = plan.content_mm
    assert doc.page_count == plan.counts.printed == 9
    for pno in range(doc.page_count):
        bbox = nonwhite_bbox_mm(doc[pno], dpi=200)
        assert bbox is not None
        tol = 0.2  # anti-aliasing
        assert bbox.x0 >= ox - tol
        assert bbox.y0 >= oy - tol
        assert bbox.x1 <= ox + cw + tol
        assert bbox.y1 <= oy + ch + tol


def test_cut_lines_dashed_3_2_on_cut_edges_only(run: Run) -> None:
    res, doc = run()
    assert len(_dashed(doc[_page_of(res, "A1")])) == 0
    assert len(_dashed(doc[_page_of(res, "B2")])) == 2  # left and top
    assert len(_dashed(doc[_page_of(res, "C3")])) == 2
    assert len(_dashed(doc[_page_of(res, "A3")])) == 1  # left only
    d = _dashed(doc[_page_of(res, "B2")])[0]
    assert d["dashes"].replace("[", "").replace("]", "").split()[:2] == ["8.5039", "5.6693"]


def test_outer_cut_lines_add_exterior_edges(run: Run) -> None:
    res, doc = run(marks={"outer_cut_lines": True})
    assert len(_dashed(doc[_page_of(res, "A1")])) == 2  # top and left have no neighbour
    assert len(_dashed(doc[_page_of(res, "B2")])) == 2


def test_alignment_lines_and_triangles_on_receiving_edges(run: Run) -> None:
    res, doc = run(marks={"cut_lines": False})
    plan = res.plans[0]
    ox, oy = plan.content_origin_mm
    cw, ch = plan.content_mm
    o = plan.overlap_mm
    a1 = doc[_page_of(res, "A1")]
    vs = _long_vlines(a1)
    assert len(vs) == 1
    assert vs[0][0].x / MM == pytest.approx(ox + cw - o, abs=0.05)
    hs = [(a, b) for a, b in _lines(a1, dashed=False) if a.y == b.y and abs(a.x - b.x) > 300]
    assert len(hs) == 1
    assert hs[0][0].y / MM == pytest.approx(oy + ch - o, abs=0.05)
    # triangle at the top end of the right alignment line, inside the strip (apex on the line)
    assert _dark_at(a1, ox + cw - o + 1.0, oy + 2.2)
    assert not _dark_at(a1, ox + cw - o + 1.0, oy + 12)
    last = doc[_page_of(res, "C3")]
    assert _long_vlines(last) == []  # the last sheet receives nothing


def test_labels_placement_rules(run: Run) -> None:
    res, doc = run()
    plan = res.plans[0]
    ox, oy = plan.content_origin_mm
    cw, ch = plan.content_mm
    o = plan.overlap_mm
    # bottom receiving strip
    r = _words(doc[_page_of(res, "A1")])["A1"]
    assert (oy + ch - o) * MM <= r.y0
    assert r.y1 <= (oy + ch) * MM
    # last column, not last row: still the bottom strip
    r = _words(doc[_page_of(res, "B3")])["B3"]
    assert (oy + ch - o) * MM <= r.y0
    assert r.y1 <= (oy + ch) * MM
    # last row, not last column: right strip, text rotated
    r = _words(doc[_page_of(res, "C1")])["C1"]
    assert (ox + cw - o) * MM <= r.x0
    assert r.x1 <= (ox + cw) * MM
    # last sheet: 6 pt label in the bottom-right corner of the usable area
    r = _words(doc[_page_of(res, "C3")])["Hoja"]
    assert r.y1 <= (oy + ch) * MM
    assert r.y0 >= (oy + ch - 10) * MM
    assert r.x0 >= (ox + cw - 80) * MM


def test_label_texts_neighbors_and_ruler(run: Run) -> None:
    res, doc = run()
    text = doc[_page_of(res, "B2")].get_text()
    for needle in ("B2", "Hoja 5/9", "A2", "C2", "B1", "B3", "50 mm"):
        assert needle in text
    corner = doc[_page_of(res, "C3")].get_text()
    assert "50 mm" not in corner
    assert "Hoja 9/9" in corner


def test_small_overlap_moves_label_to_corner_and_drops_ruler(run: Run) -> None:
    res, doc = run(tiling={"overlap_mm": 4})
    plan = res.plans[0]
    ox, oy = plan.content_origin_mm
    cw, ch = plan.content_mm
    assert "50 mm" not in doc.get_page_text(0)
    page = doc[_page_of(res, "C3")]  # no receiving strip: visible corner label
    r = _words(page)["Hoja"]
    assert r.x0 >= (ox + cw - 80) * MM
    assert r.y1 <= (oy + ch) * MM


def test_small_overlap_labels_hidden_in_receiving_strips(run: Run) -> None:
    res, doc = run(tiling={"overlap_mm": 4})
    plan = res.plans[0]
    ox, oy = plan.content_origin_mm
    cw, ch = plan.content_mm
    o = plan.overlap_mm
    for sh in plan.sheets:
        if sh.print_index is None or sh.id == "C3":
            continue
        page = doc[_page_of(res, sh.id)]
        words = _words(page)
        assert "Hoja" not in words
        assert set(words) == {sh.id}  # compact: ID only, no hints / ruler
        r = words[sh.id]
        if sh.id[0] != "C":  # bottom strip
            assert (oy + ch - o) * MM - 0.5 <= r.y0
            assert r.y1 <= (oy + ch) * MM + 0.5
        else:  # right strip
            assert (ox + cw - o) * MM - 0.5 <= r.x0
            assert r.x1 <= (ox + cw) * MM + 0.5


def test_info_band_hosts_label_when_overlap_small(run: Run) -> None:
    res, doc = run(
        tiling={"overlap_mm": 4}, target={"info_band": {"position": "top", "height_mm": 10}}
    )
    plan = res.plans[0]
    r = _words(doc[_page_of(res, "C3")])["Hoja"]
    assert plan.margins_mm.top * MM <= r.y0
    assert r.y1 <= (plan.margins_mm.top + 10) * MM


def test_labels_off_neighbors_off_ruler_off(run: Run) -> None:
    res, doc = run(marks={"neighbor_hints": False, "ruler": False})
    text = doc[_page_of(res, "B2")].get_text()
    assert "Hoja 5/9" in text
    assert "C2" not in text
    assert "50 mm" not in text
    res, doc = run(marks={"labels": False})
    assert "Hoja" not in doc[0].get_text()


def test_butt_join_diamonds_and_cuts(run: Run) -> None:
    res, doc = run(tiling={"join": "butt"}, marks={"alignment_marks": True})
    plan = res.plans[0]
    assert plan.overlap_mm == 0
    a1 = doc[_page_of(res, "A1")]
    assert len(_dashed(a1)) == 2  # right and bottom are cut edges in a butt join
    text = a1.get_text()
    assert "A1|A2" in text
    assert "A1|B1" in text
    ox, oy = plan.content_origin_mm
    cw, ch = plan.content_mm
    # half diamonds: filled triangles with the base on the right / bottom edge midpoints
    assert _dark_at(a1, ox + cw - 2.0, oy + ch / 2)
    assert _dark_at(a1, ox + cw / 2, oy + ch - 2.0)
    assert not _dark_at(a1, ox + 2.0, oy + ch / 2)
    # no receiving strips: no alignment line
    assert _long_vlines(a1) == []
    # B1 (middle row, first column of a 2 x 3 grid) labels its three joints
    t = doc[_page_of(res, "B1")].get_text()
    assert all(j in t for j in ("A1|B1", "B1|B2", "B1|C1")), t
    # and the diamond marks can be disabled
    res, doc = run(tiling={"join": "butt"}, marks={"join_diamonds": False})
    assert "A1|A2" not in doc[0].get_text()


def test_mark_color_and_width_from_settings(run: Run) -> None:
    res, doc = run(marks={"color": "#ff0000", "line_width_pt": 1.0})
    drawings = doc[_page_of(res, "B2")].get_drawings()
    dashed = _dashed(doc[_page_of(res, "B2")])
    assert dashed
    assert dashed[0]["color"] == (1.0, 0.0, 0.0)
    assert dashed[0]["width"] == pytest.approx(1.0)
    assert any(d.get("fill") == (1.0, 0.0, 0.0) for d in drawings)


def test_glue_shading_fills_receiving_strips(run: Run) -> None:
    res, doc = run(marks={"glue_shading": True})
    shaded = [
        d
        for d in doc[_page_of(res, "A1")].get_drawings()
        if d.get("fill_opacity") == pytest.approx(0.1)
    ]
    assert len(shaded) == 2


def test_printer_correction_applied_to_marks_and_content(run: Run) -> None:
    _plain, doc0 = run(marks={"cut_lines": False})
    printer = PrinterProfile(id="p-test", name="Calibrated", scale_kx=1.02, scale_ky=0.98)
    _, doc1 = run(marks={"cut_lines": False}, printer=printer)
    page0, page1 = doc0[0], doc1[0]
    cx, cy = page0.rect.width / 2, page0.rect.height / 2

    def vline(page: pymupdf.Page) -> tuple[float, float, float]:
        (p, q), *_ = _long_vlines(page)
        return p.x, min(p.y, q.y), max(p.y, q.y)

    x0, ya, yb = vline(page0)
    x1, ya1, yb1 = vline(page1)
    assert x1 == pytest.approx(cx + (x0 - cx) / 1.02, abs=0.01)
    assert ya1 == pytest.approx(cy + (ya - cy) / 0.98, abs=0.01)
    assert yb1 == pytest.approx(cy + (yb - cy) / 0.98, abs=0.01)
    assert page1.rect == page0.rect


def test_printer_correction_scales_vector_content(
    rfx_ingest: Callable[[str], Path], tmp_path: Path
) -> None:
    work = rfx_ingest("a0_vector_grid.pdf")
    base = make_settings(
        marks={
            "assembly_page": "none",
            "cut_lines": False,
            "alignment_marks": False,
            "labels": False,
            "ruler": False,
        }
    )
    printer = PrinterProfile(id="p-test", name="Calibrated", scale_kx=1.01, scale_ky=1.01)
    a = render_job(work, base, tmp_path / "a", now=datetime(2026, 1, 1, tzinfo=UTC))
    b = render_job(
        work, base, tmp_path / "b", printer=printer, now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    with (
        pymupdf.open(tmp_path / "a" / a.outputs[0].name) as da,
        pymupdf.open(tmp_path / "b" / b.outputs[0].name) as db,
    ):
        ba, bb = nonwhite_bbox_mm(da[4], 150), nonwhite_bbox_mm(db[4], 150)
    assert ba is not None
    assert bb is not None
    assert bb.width == pytest.approx(ba.width / 1.01, abs=0.4)
    assert bb.height == pytest.approx(ba.height / 1.01, abs=0.4)


def test_hostile_source_pdf_leaves_no_active_content(tmp_path: Path) -> None:
    src = tmp_path / "hostile.pdf"
    d = pymupdf.open()
    page = d.new_page(width=mm_to_pt(200), height=mm_to_pt(200))
    page.draw_rect(pymupdf.Rect(40, 40, 200, 200), fill=(0, 0, 0))
    page.insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(10, 10, 100, 100),
            "uri": "https://evil.example/x",
        }
    )
    page.add_text_annot((50, 50), "hostile note")
    cat = d.pdf_catalog()
    js = d.get_new_xref()
    d.update_object(js, r"<< /S /JavaScript /JS (app.alert\(1\)) >>")
    d.xref_set_key(cat, "OpenAction", f"{js} 0 R")
    d.xref_set_key(cat, "Names", f"<< /JavaScript << /Names [(a) {js} 0 R] >> >>")
    d.save(src)
    d.close()
    cfg = AppConfig(data_dir=tmp_path / "d", tmp_dir=tmp_path / "t")
    work = tmp_path / "doc"
    ingest_document(src, work, config=cfg)
    res = render_job(work, make_settings(), tmp_path / "o", now=FIXED_NOW)
    with pymupdf.open(tmp_path / "o" / res.outputs[0].name) as out:
        for pg in out:
            assert list(pg.annots()) == []
            assert pg.get_links() == []
        cat = out.pdf_catalog()
        assert out.xref_get_key(cat, "OpenAction")[0] == "null"
        names = out.xref_get_key(cat, "Names")
        assert names[0] == "null" or "JavaScript" not in names[1]
        for x in range(1, out.xref_length()):
            obj = out.xref_object(x, compressed=True)
            assert "/JavaScript" not in obj
            assert "/URI" not in obj
