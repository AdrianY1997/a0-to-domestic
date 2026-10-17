"""Vector render end to end: CA-1, CA-2, CA-3, CA-5, assembly page, output metadata (SPEC 15)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.settings import PrinterProfile
from a0td.domain.units import mm_to_pt
from a0td.render import RenderResult
from render_support import (
    direct_vector,
    mismatch_fraction,
    nonwhite_bbox_mm,
    reassemble,
)

Render = Callable[..., tuple[RenderResult, Path]]
A4_LANDSCAPE_PT = (mm_to_pt(297), mm_to_pt(210))
NO_MARKS: dict[str, Any] = {
    "cut_lines": False,
    "alignment_marks": False,
    "join_diamonds": False,
    "labels": False,
    "neighbor_hints": False,
    "ruler": False,
    "assembly_page": "none",
}


def test_ca1_pages_size_and_bound(render: Render, rfx_dir: Path) -> None:
    result, pdf = render("a0_vector_grid.pdf")
    plan = result.plans[0]
    assert (plan.grid.cols, plan.grid.rows, plan.counts.printed) == (3, 7, 21)
    assert plan.orientation.value == "landscape"
    out = result.outputs[0]
    assert out.kind == "pdf"
    assert out.pages == 22
    assert out.size_bytes == pdf.stat().st_size
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == 22
        for page in doc:
            assert page.rect.width == pytest.approx(A4_LANDSCAPE_PT[0], abs=0.01)
            assert page.rect.height == pytest.approx(A4_LANDSCAPE_PT[1], abs=0.01)
    origin = (rfx_dir / "a0_vector_grid.pdf").stat().st_size
    assert out.size_bytes <= 1.2 * origin + 200 * 1024


def test_shared_xobject_not_multiplied(render: Render) -> None:
    """The source page is one form XObject referenced by every sheet (SAL-01)."""
    _, pdf = render("a0_vector_grid.pdf")
    with pymupdf.open(pdf) as doc:
        big = [
            x
            for x in range(1, doc.xref_length())
            if doc.xref_get_key(x, "Subtype")[1] == "/Form" and len(doc.xref_stream_raw(x)) > 1000
        ]
        assert len(big) == 1


def test_viewer_preferences_metadata_and_attachment(render: Render) -> None:
    _result, pdf = render("a0_vector_grid.pdf", scale={"mode": "percent", "percent": 100})
    with pymupdf.open(pdf) as doc:
        prefs = doc.xref_get_key(doc.pdf_catalog(), "ViewerPreferences")[1]
        assert "/PrintScaling/None" in prefs.replace(" ", "")
        assert "/Duplex/Simplex" in prefs.replace(" ", "")
        assert "/PickTrayByPDFSize true" in prefs
        assert doc.metadata["title"] == pdf.stem
        assert doc.metadata["creator"].startswith("a0-to-domestic")
        assert doc.embfile_names() == ["a0td-job.json"]
        job = json.loads(doc.embfile_get("a0td-job.json"))
        assert job["settings"]["scale"] == {"mode": "percent", "percent": 100.0}
        assert job["plans"][0]["grid"] == {"cols": 3, "rows": 7}


def test_ca2_fit_sheet_single_page(render: Render) -> None:
    result, pdf = render(
        "a0_vector_grid.pdf",
        scale={"mode": "fit_sheet"},
        marks=NO_MARKS,
        target={"orientation": "auto"},
    )
    plan = result.plans[0]
    assert plan.scale == pytest.approx(0.2378, abs=5e-5)
    assert plan.orientation.value == "portrait"
    assert (plan.grid.cols, plan.grid.rows) == (1, 1)
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == 1
        assert doc[0].rect.width == pytest.approx(mm_to_pt(210), abs=0.01)
        assert doc[0].rect.height == pytest.approx(mm_to_pt(297), abs=0.01)
        bbox = nonwhite_bbox_mm(doc[0], dpi=200)
    assert bbox is not None
    # 200.0 x 282.8 mm centred on the 200 x 287 usable area (SPEC CA-2)
    assert bbox.width == pytest.approx(200.0, abs=0.6)
    assert bbox.height == pytest.approx(282.8, abs=0.6)
    assert (bbox.x0 + bbox.x1) / 2 == pytest.approx(105.0, abs=0.6)
    assert (bbox.y0 + bbox.y1) / 2 == pytest.approx(148.5, abs=0.6)


def test_ca3_drawing_scale_assembly_text(render: Render) -> None:
    result, pdf = render(
        "a0_vector_grid.pdf",
        scale={"mode": "drawing_scale", "from_denominator": 50, "to_denominator": 100},
    )
    plan = result.plans[0]
    assert (plan.grid.cols, plan.grid.rows) == (2, 4)
    assert plan.orientation.value == "landscape"
    with pymupdf.open(pdf) as doc:
        text = doc[0].get_text()
        assert "1:100" in text
        assert "Plano de montaje" in text


def test_ca5_blank_sheets_skipped_and_crossed(render: Render) -> None:
    result, pdf = render("a0_top_half_only.pdf")
    plan = result.plans[0]
    assert plan.counts.blank > 0
    assert plan.counts.printed == plan.counts.total - plan.counts.blank
    assert result.outputs[0].pages == plan.counts.printed + 1
    ids = [s.id for s in plan.sheets if s.print_index is not None]
    assert [s.print_index for s in plan.sheets if s.print_index is not None] == list(
        range(1, plan.counts.printed + 1)
    )
    with pymupdf.open(pdf) as doc:
        # every skipped sheet is crossed with two red diagonals on the assembly page
        red = [
            d
            for d in doc[0].get_drawings()
            if d.get("color") and d["color"][0] > 0.7 > d["color"][1]
        ]
        crosses = [d for d in red if d["items"] and d["items"][0][0] == "l"]
        assert len(crosses) >= 2 * plan.counts.blank
        # and "Sheet k/N" counts only printed sheets
        last = doc[doc.page_count - 1].get_text()
        assert f"{plan.counts.printed}/{plan.counts.printed}" in last
        assert ids[-1] in last


def test_skip_blank_off_prints_everything(render: Render) -> None:
    result, _ = render("a0_top_half_only.pdf", tiling={"skip_blank": False})
    plan = result.plans[0]
    assert plan.counts.printed == plan.counts.total


def test_assembly_test_squares_and_ruler(render: Render) -> None:
    _, pdf = render("a0_vector_grid.pdf")
    with pymupdf.open(pdf) as doc:
        rects = [
            d["rect"] for d in doc[0].get_drawings() if d["items"] and d["items"][0][0] == "re"
        ]
    sizes = [(round(r.width / 72 * 25.4, 1), round(r.height / 72 * 25.4, 1)) for r in rects]
    assert any(abs(w - 50.0) < 0.6 and abs(h - 50.0) < 0.6 for w, h in sizes)
    assert any(abs(w - 50.8) < 0.6 and abs(h - 50.8) < 0.6 for w, h in sizes)


def test_printer_correction_applies_to_assembly_page(render: Render) -> None:
    k = 1.02
    printer = PrinterProfile(id="p-test", name="Calibrated", scale_kx=k, scale_ky=k)
    _, pdf = render("a0_vector_grid.pdf", printer=printer)
    with pymupdf.open(pdf) as doc:
        rects = [
            d["rect"] for d in doc[0].get_drawings() if d["items"] and d["items"][0][0] == "re"
        ]
    sizes = [(r.width / 72 * 25.4, r.height / 72 * 25.4) for r in rects]
    for nominal in (50.0, 50.8):
        assert any(abs(w - nominal / k) < 0.05 and abs(h - nominal / k) < 0.05 for w, h in sizes), (
            nominal
        )


def test_assembly_english_and_last_position(render: Render) -> None:
    result, pdf = render("a0_vector_grid.pdf", locale="en", marks={"assembly_page": "last"})
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == 22
        assert "Assembly plan" in doc[doc.page_count - 1].get_text()
        assert "Assembly plan" not in doc[0].get_text()
    assert result.outputs[0].pages == 22


def test_test_sheet_only(render: Render) -> None:
    result, pdf = render("a0_vector_grid.pdf", test_sheet_only=True)
    assert result.outputs[0].pages == 1
    assert pdf.name.endswith("_test.pdf")
    assert len(result.plans) == 1
    assert result.plans[0].counts.printed == 21
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == 1
        assert "Plano de montaje" in doc[0].get_text()
        assert doc[0].rect.width == pytest.approx(A4_LANDSCAPE_PT[0], abs=0.01)


def test_test_sheet_only_forces_assembly_even_if_disabled(render: Render) -> None:
    result, _ = render("a0_vector_grid.pdf", test_sheet_only=True, marks={"assembly_page": "none"})
    assert result.outputs[0].pages == 1


def test_filename_template_and_progress(render: Render) -> None:
    events: list[tuple[str, float, str, dict[str, Any]]] = []
    _result, pdf = render(
        "a0_vector_grid.pdf",
        doc_name="Plano ñoño: A0 (v2)",
        output={"filename_template": "{name}_{paper}-{orientation}_{cols}x{rows}_{scale}pct"},
        progress=lambda *a: events.append(a),
    )
    assert pdf.name == "Plano_nono_A0_v2_A4-landscape_3x7_100pct.pdf"
    stages = [e[0] for e in events]
    assert stages[0] == "load"
    assert stages[-1] == "done"
    assert events[-1][1] == 100.0
    pcts = [e[1] for e in events]
    assert pcts == sorted(pcts)
    assert sum(1 for e in events if e[2] == "render.sheet") == 21


def test_invalid_settings_raise_domain_error(render: Render) -> None:
    with pytest.raises(DomainError) as exc:
        render("a0_vector_grid.pdf", source={"pages": "3"})
    assert exc.value.code is ErrorCode.INVALID_SETTINGS
    with pytest.raises(DomainError) as exc:
        render("a0_vector_grid.pdf", output={"filename_template": "{nope}"})
    assert exc.value.code is ErrorCode.INVALID_SETTINGS


def test_pdf_has_label_fonts_on_every_page(render: Render) -> None:
    """Regression: label fonts must be page resources even though the source XObject has them."""
    _, pdf = render("a0_vector_grid.pdf")
    with pymupdf.open(pdf) as doc:
        for page in (doc[0], doc[1], doc[21]):
            names = {f[4] for f in page.get_fonts(full=True) if f[6] == 0}
            assert {"helv", "hebo"} <= names


# ------------------------------------------------------------------- reassembly (SPEC 15.4)


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"scale": {"mode": "percent", "percent": 60}, "tiling": {"overlap_mm": 7}},
        {"source": {"rotate": 90}},
        {"tiling": {"join": "butt"}},
    ],
    ids=["100pct", "60pct", "rotate90", "butt"],
)
def test_reassembly_vector(
    render: Render, rfx_ingest: Callable[[str], Path], extra: dict[str, Any]
) -> None:
    marks = dict(NO_MARKS, assembly_page="none")
    result, pdf = render("a0_vector_grid.pdf", marks=marks, **extra)
    plan = result.plans[0]
    dpi = 150
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == plan.counts.printed
        got, xs, ys = reassemble(doc, plan, 0, dpi)
    rotate = extra.get("source", {}).get("rotate", 0)
    want = direct_vector(rfx_ingest("a0_vector_grid.pdf") / "canonical.pdf", 0, plan, dpi, rotate)
    frac = mismatch_fraction(got, want, seams_x=xs, seams_y=ys)
    assert got.shape[0] == pytest.approx(want.shape[0], abs=2)
    assert got.shape[1] == pytest.approx(want.shape[1], abs=2)
    assert frac < 0.005


# ------------------------------------------------------------------------------ multipage


def test_multipage_pdf_prefixes_and_sizes_per_page(render: Render) -> None:
    result, pdf = render("multipage_mixed.pdf", marks={"assembly_page": "first"})
    assert len(result.plans) == 3
    printed = [s.id for p in result.plans for s in p.sheets if s.print_index is not None]
    assert all(i.startswith(("P1-", "P2-", "P3-")) for i in printed)
    expected = sum(p.counts.printed + 1 for p in result.plans)
    assert result.outputs[0].pages == expected
    with pymupdf.open(pdf) as doc:
        text = "".join(page.get_text() for page in doc)
    assert "P3-" in text
    assert "Plano de montaje" in text


def test_single_selected_page_has_no_prefix(render: Render) -> None:
    result, _ = render("multipage_mixed.pdf", source={"pages": "3"})
    assert len(result.plans) == 1
    assert result.plans[0].page_index == 2
    assert not any(s.id.startswith("P") for s in result.plans[0].sheets)


def test_all_blank_is_an_error(render: Render) -> None:
    with pytest.raises(DomainError) as exc:
        render(
            "a0_top_half_only.pdf",
            tiling={"only_sheets": ["Z9"]},
            marks={"assembly_page": "none"},
        )
    assert exc.value.code is ErrorCode.INVALID_SETTINGS


def test_only_sheets_reprint(render: Render) -> None:
    result, pdf = render("a0_vector_grid.pdf", tiling={"only_sheets": ["B2", "C3"]})
    plan = result.plans[0]
    assert plan.counts.printed == 2
    assert result.outputs[0].pages == 3
    with pymupdf.open(pdf) as doc:
        assert "B2" in doc[1].get_text()
        assert "Hoja 2/2" in doc[2].get_text()


def test_box_trim_and_rotated_source_render(render: Render) -> None:
    result, pdf = render("boxes.pdf", source={"box": "trim", "rotate": 270})
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == result.outputs[0].pages > 1


# ------------------------------------------------------------- engine integration (WP-08)


def test_doc_name_defaults_to_document_json(
    rfx_ingest: Callable[[str], Path], tmp_path: Path
) -> None:
    import shutil

    from a0td.render import render_job
    from render_support import FIXED_NOW, make_settings

    work = tmp_path / "doc"
    shutil.copytree(rfx_ingest("a0_vector_grid.pdf"), work)
    (work / "document.json").write_text(
        json.dumps({"id": "x", "name": "Casa Ñ 1.pdf"}), encoding="utf-8"
    )
    res = render_job(work, make_settings(), tmp_path / "o", now=FIXED_NOW)
    assert res.outputs[0].name == "Casa_N_1_A4-landscape_3x7_100pct.pdf"
    assert Path(res.outputs[0].name).name == res.outputs[0].name  # a plain basename
    with pymupdf.open(tmp_path / "o" / res.outputs[0].name) as doc:
        assert "Casa N 1" in doc[1].get_text() or "Casa" in doc[1].get_text()


def test_render_sheet_preview_png(rfx_ingest: Callable[[str], Path]) -> None:
    import io

    from PIL import Image

    from a0td.render import render_sheet_preview
    from render_support import make_settings

    work = rfx_ingest("a0_vector_grid.pdf")
    png = render_sheet_preview(work, make_settings(), 0, "b2", 400)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"
    assert max(img.size) == 400
    assert img.size[0] > img.size[1]  # A4 landscape
    with pytest.raises(DomainError) as exc:
        render_sheet_preview(work, make_settings(), 0, "Z9", 400)
    assert exc.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(DomainError):
        render_sheet_preview(work, make_settings(), 5, "A1", 400)


def test_render_sheet_preview_raster(rfx_ingest: Callable[[str], Path]) -> None:
    from a0td.render import render_sheet_preview
    from render_support import make_settings

    png = render_sheet_preview(rfx_ingest("scan_a0_200dpi_g4.tif"), make_settings(), 0, "A1", 300)
    assert png.startswith(b"\x89PNG")
