"""ANA-07 layers in the output: source-off layers, visible_layers, grayscale, marks OCG, OCMD."""

import re
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
import pytest

from a0td.config import AppConfig
from a0td.ingest import IngestResult, ingest_document
from a0td.render import render_job, render_sheet_preview
from a0td.render.layers import effective_off_names, layered_preview_webp
from render_support import FIXED_NOW, make_settings

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


def _off_names(pdf: Path) -> list[str]:
    with pymupdf.open(pdf) as doc:
        return sorted(c["text"] for c in doc.layer_ui_configs() if not c["on"])


def _rasterized(res: Any) -> bool:
    return any(w.code == "LAYERS_RASTERIZED" for w in res.warnings)


def _layered_source(fixtures_dir: Path, dst: Path, off: list[str]) -> Path:
    """``layers_ocg.pdf`` with the given layers OFF in the file's default configuration."""
    with pymupdf.open(fixtures_dir / "layers_ocg.pdf") as doc:
        xrefs = [x for x, i in doc.get_ocgs().items() if i["name"] in off]
        doc.set_layer(-1, off=xrefs)
        doc.save(dst)
    return dst


# ------------------------------------------------------------------ source-off layers


def test_source_off_layer_is_not_printed_by_default(fixtures_dir: Path, tmp_path: Path) -> None:
    src = _layered_source(fixtures_dir, tmp_path / "off.pdf", ["Talla 40"])
    work = _ingest(src, tmp_path)
    layers = {la.name: la.on for la in IngestResult.load(work).pages[0].layers}
    assert layers == {"Talla 38": True, "Talla 40": False, "Talla 42": True}
    _, default = _render(work, tmp_path / "a")
    assert _off_names(default) == ["Talla 40"]
    _, shown = _render(work, tmp_path / "b", source={"visible_layers": ["Talla 40"]})
    assert _off_names(shown) == []
    assert _dark(default) < _dark(shown) * 0.9


def test_hidden_wins_over_visible_and_adds_to_source_off(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    src = _layered_source(fixtures_dir, tmp_path / "off.pdf", ["Talla 40"])
    work = _ingest(src, tmp_path)
    layers = IngestResult.load(work).pages[0].layers
    by_name = {la.name: la for la in layers}
    assert effective_off_names(layers, ["Talla 38"], ["Talla 40"]) == {"Talla 38"}
    assert effective_off_names(layers, ["Talla 40"], ["Talla 40"]) == {"Talla 40"}
    assert effective_off_names(layers, [], [str(by_name["Talla 40"].xref)]) == set()
    _, out = _render(work, tmp_path / "o", source={"hidden_layers": ["Talla 38"]})
    assert _off_names(out) == ["Talla 38", "Talla 40"]


def test_sheet_preview_follows_source_off_and_visible(fixtures_dir: Path, tmp_path: Path) -> None:
    src = _layered_source(fixtures_dir, tmp_path / "off.pdf", ["Talla 40"])
    work = _ingest(src, tmp_path)
    off = render_sheet_preview(work, make_settings(marks=NO_MARKS), 0, "B2", 300)
    on = render_sheet_preview(
        work,
        make_settings(marks=NO_MARKS, source={"visible_layers": ["Talla 40"]}),
        0,
        "B2",
        300,
    )
    assert off != on


def test_page_preview_follows_visible(fixtures_dir: Path, tmp_path: Path) -> None:
    src = _layered_source(fixtures_dir, tmp_path / "off.pdf", ["Talla 40"])
    work = _ingest(src, tmp_path)
    layers = IngestResult.load(work).pages[0].layers
    canonical = work / "canonical.pdf"
    default = layered_preview_webp(canonical, 0, layers, ["Talla 38"], None, 300)
    plain = layered_preview_webp(canonical, 0, layers, ["Talla 38"], ["Talla 40"], 300)
    assert default != plain


# --------------------------------------------------------------------- grayscale vector


def test_grayscale_hidden_layers_fall_back_to_raster(fixtures_dir: Path, tmp_path: Path) -> None:
    work = _ingest(fixtures_dir / "layers_ocg.pdf", tmp_path)
    gray = {"mode": "grayscale"}
    out = {"dpi": 150}
    res0, shown = _render(work, tmp_path / "s", color=gray, output=out)
    assert not _rasterized(res0)
    res, hidden = _render(
        work,
        tmp_path / "h",
        color=gray,
        output=out,
        source={"hidden_layers": ["Talla 38", "Talla 40"]},
    )
    assert _rasterized(res)
    assert _dark(hidden) < _dark(shown) * 0.7
    with pymupdf.open(hidden) as doc:
        text = "".join(p.get_text() for p in doc)
        assert "Talla 38" not in text
        assert "Talla 40" not in text
        assert not doc.get_ocgs()  # the hidden content is not in the file at all


def test_grayscale_source_off_layer_visible_is_honoured(fixtures_dir: Path, tmp_path: Path) -> None:
    src = _layered_source(fixtures_dir, tmp_path / "off.pdf", ["Talla 40"])
    work = _ingest(src, tmp_path)
    gray = {"mode": "grayscale"}
    out = {"dpi": 150}
    _, default = _render(work, tmp_path / "a", color=gray, output=out)
    res, shown = _render(
        work,
        tmp_path / "b",
        color=gray,
        output=out,
        source={"visible_layers": ["Talla 40"]},
    )
    assert _rasterized(res)
    assert _dark(shown) > _dark(default) * 1.1


# ------------------------------------------------------------------------ marks OCG


def test_marks_ocg_is_not_matched_by_name(tmp_path: Path) -> None:
    src = tmp_path / "marcas.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=900, height=700)
    oc = doc.add_ocg("Marcas", on=True)
    page.draw_rect(pymupdf.Rect(100, 100, 600, 500), fill=(0, 0, 0), oc=oc)
    doc.save(src)
    doc.close()
    work = _ingest(src, tmp_path)
    marks = {**NO_MARKS, "marks_layer": True, "cut_lines": True, "labels": True}
    _, out = _render(work, tmp_path / "o", marks=marks, source={"hidden_layers": ["Marcas"]})
    with pymupdf.open(out) as pdf:
        mine = [x for x in pdf.get_ocgs() if "a0-to-domestic" in pdf.xref_object(x)]
        assert len(mine) == 1
        off_key = pdf.xref_get_key(pdf.pdf_catalog(), "OCProperties/D/OFF")[1]
        off = [int(m) for m in re.findall(r"(\d+) 0 R", off_key)]
        assert off  # the source "Marcas" layer is OFF ...
        assert mine[0] not in off  # ... and the marks layer is not


# ------------------------------------------------------------- OCMD and nested forms


def _ocmd_pdf(path: Path, policy: str | None, nested_form: bool) -> None:
    """One black rectangle controlled by a layer: through an OCMD (``policy``) or a nested form."""
    doc = pymupdf.open()
    page = doc.new_page(width=700, height=500)
    ocg = doc.add_ocg("Capa", on=True)
    body = b"0 g 100 100 300 200 re f"
    if nested_form:
        inner = doc.get_new_xref()
        doc.update_object(inner, f"<</Type/XObject/Subtype/Form/BBox[0 0 700 500]/OC {ocg} 0 R>>")
        doc.update_stream(inner, body)
        outer = doc.get_new_xref()
        doc.update_object(
            outer,
            "<</Type/XObject/Subtype/Form/BBox[0 0 700 500]"
            f"/Resources<</XObject<</In {inner} 0 R>>>>>>",
        )
        doc.update_stream(outer, b"/In Do")
        content = b"/Out Do"
        resources = f"<</XObject<</Out {outer} 0 R>>>>"
    else:
        ocmd = doc.get_new_xref()
        if policy == "VE-Not":
            doc.update_object(ocmd, f"<</Type/OCMD/VE[/Not {ocg} 0 R]>>")
        elif policy == "VE-Or":
            doc.update_object(ocmd, f"<</Type/OCMD/VE[/Or {ocg} 0 R [/And {ocg} 0 R]]>>")
        else:
            doc.update_object(ocmd, f"<</Type/OCMD/OCGs[{ocg} 0 R]/P/{policy}>>")
        content = b"/OC /Mc0 BDC " + body + b" EMC"
        resources = f"<</Properties<</Mc0 {ocmd} 0 R>>>>"
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content)
    page.set_contents(xref)
    doc.xref_set_key(page.xref, "Resources", resources)
    doc.save(path)
    doc.close()


def test_oc_on_nested_form_is_hidden(tmp_path: Path) -> None:
    src = tmp_path / "nested.pdf"
    _ocmd_pdf(src, None, nested_form=True)
    work = _ingest(src, tmp_path)
    _, shown = _render(work, tmp_path / "s")
    res, hidden = _render(work, tmp_path / "h", source={"hidden_layers": ["Capa"]})
    assert _off_names(hidden) == ["Capa"]
    assert _dark(shown) > 1
    assert _dark(hidden) < 0.5
    assert not _rasterized(res)


# (policy, visible while the layer is ON, visible while the layer is OFF), per PDF 32000 8.11.2.2
_POLICIES = [
    ("AnyOn", True, False),
    ("AllOn", True, False),
    ("AnyOff", False, True),
    ("AllOff", False, True),
    ("VE-Not", None, True),  # viewers differ on /VE with every group ON: not asserted
    ("VE-Or", None, False),
]


@pytest.mark.parametrize(("policy", "on_visible", "off_visible"), _POLICIES)
def test_ocmd_policies_are_evaluated_with_the_hidden_layer(
    tmp_path: Path, policy: str, on_visible: bool | None, off_visible: bool
) -> None:
    src = tmp_path / "ocmd.pdf"
    _ocmd_pdf(src, policy, nested_form=False)
    work = _ingest(src, tmp_path)
    res, plain = _render(work, tmp_path / "p")
    if on_visible is not None:
        assert (_dark(plain) > 1) is on_visible
    res, hidden = _render(work, tmp_path / "h", source={"hidden_layers": ["Capa"]})
    assert (_dark(hidden) > 1) is off_visible
    assert _off_names(hidden) == ["Capa"]
    assert not _rasterized(res)  # the OCMD is normalized, not rasterized
