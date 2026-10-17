"""Vector physical-size override: all page boxes, atomic commit, idempotent retry (WP-12 audit)."""

import json
import re
from pathlib import Path

import pymupdf
import pytest

from a0td.config import AppConfig
from a0td.domain.errors import ErrorCode
from a0td.ingest import IngestResult, ingest_document, set_physical_size
from a0td.ingest import pipeline as pipeline_mod
from a0td.ingest.converters.base import tool_available
from a0td.ingest.vector_size import NATIVE_SIZE_FILE, rescale_pdf_pages
from convert_support import fixture_path

needs_svg = pytest.mark.skipif(
    not (tool_available("rsvg-convert") or tool_available("inkscape")), reason="no SVG renderer"
)
BOXES = ("MediaBox", "CropBox", "TrimBox", "BleedBox", "ArtBox")


def _nums(raw: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+\.?\d*", raw)]


def _make_pdf(path: Path, *, inherit_media: bool = False) -> None:
    with pymupdf.open() as doc:
        page = doc.new_page(width=200, height=100)
        page.insert_text((10, 50), "x")
        page.set_cropbox(pymupdf.Rect(5, 5, 190, 95))
        page.set_trimbox(pymupdf.Rect(10, 10, 180, 90))
        page.set_bleedbox(pymupdf.Rect(4, 4, 195, 96))
        page.set_artbox(pymupdf.Rect(20, 20, 150, 80))
        if inherit_media:
            pages_xref = int(doc.xref_get_key(doc.pdf_catalog(), "Pages")[1].split()[0])
            kind, raw = doc.xref_get_key(page.xref, "MediaBox")
            assert kind == "array"
            doc.xref_set_key(pages_xref, "MediaBox", raw)
            doc.xref_set_key(page.xref, "MediaBox", "null")
        doc.save(path)


def _read_boxes(path: Path) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    with pymupdf.open(path) as doc:
        xref = doc.load_page(0).xref
        for key in BOXES:
            current = xref
            while True:
                kind, raw = doc.xref_get_key(current, key)
                if kind == "array":
                    out[key] = _nums(raw)
                    break
                kind, raw = doc.xref_get_key(current, "Parent")
                if kind != "xref":
                    break
                current = int(raw.split()[0])
    return out


@pytest.mark.parametrize("inherit", [False, True])
def test_rescale_scales_all_five_boxes_including_inherited(tmp_path: Path, inherit: bool) -> None:
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf, inherit_media=inherit)
    before = _read_boxes(pdf)
    assert set(before) == set(BOXES)
    rescale_pdf_pages(pdf, 2.5)
    after = _read_boxes(pdf)
    assert set(after) == set(BOXES)
    for key in BOXES:
        assert after[key] == pytest.approx([c * 2.5 for c in before[key]], abs=1e-3), key
    assert not list(tmp_path.glob("*.tmp"))


def test_rescale_rejects_bad_factor(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf)
    with pytest.raises(Exception) as exc:  # noqa: PT011
        rescale_pdf_pages(pdf, 0)
    assert getattr(exc.value, "code", None) is ErrorCode.INVALID_SETTINGS


# ---------------------------------------------------------------- set_physical_size atomicity


def _snapshot(work: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(work)): p.read_bytes()
        for p in sorted(work.rglob("*"))
        if p.is_file() and ".tmp" not in p.name
    }


@pytest.fixture
def svg_doc(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> Path:
    work = tmp_path / "doc"
    ingest_document(fixture_path(fixtures_dir, "svg_no_units.svg"), work, config=cfg)
    assert (work / NATIVE_SIZE_FILE).is_file()
    return work


def _pdf_width_mm(work: Path) -> float:
    with pymupdf.open(work / "canonical.pdf") as doc:
        return doc[0].rect.width * 25.4 / 72


def _no_leftovers(work: Path) -> None:
    names = [p.name for p in work.rglob("*")]
    assert not [n for n in names if ".tmp" in n or n.startswith(".pages-backup")], names


@needs_svg
@pytest.mark.parametrize("fail_at", ["occupancy", "meta", "replace"])
def test_failed_vector_resize_leaves_the_document_untouched(
    svg_doc: Path, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    before = _snapshot(svg_doc)
    real_build = pipeline_mod.build_occupancy
    real_save = IngestResult.save
    real_replace = pipeline_mod.os.replace

    def boom_build(*a, **kw):  # type: ignore[no-untyped-def]
        real_build(*a, **kw)  # maps are overwritten first: rollback must restore them
        raise RuntimeError("injected")

    saves = {"n": 0}

    def boom_save(self, workdir):  # type: ignore[no-untyped-def]
        saves["n"] += 1
        real_save(self, workdir)  # written, then fails: the old meta must come back
        if saves["n"] == 1:
            raise RuntimeError("injected")
        return workdir / "meta.json"

    def boom_replace(src, dst):  # type: ignore[no-untyped-def]
        if Path(dst).name == "canonical.pdf":
            raise OSError("injected")
        return real_replace(src, dst)

    if fail_at == "occupancy":
        monkeypatch.setattr(pipeline_mod, "build_occupancy", boom_build)
    elif fail_at == "meta":
        monkeypatch.setattr(IngestResult, "save", boom_save)
    else:
        monkeypatch.setattr(pipeline_mod.os, "replace", boom_replace)

    with pytest.raises((RuntimeError, OSError)):
        set_physical_size(svg_doc, 0, width_mm=400, config=cfg)

    monkeypatch.undo()
    assert _snapshot(svg_doc) == before
    _no_leftovers(svg_doc)

    # the retry is the first and only scaling
    page = set_physical_size(svg_doc, 0, width_mm=400, config=cfg).pages[0]
    assert page.width_mm == pytest.approx(400, abs=0.01)
    assert _pdf_width_mm(svg_doc) == pytest.approx(400, abs=0.05)
    assert IngestResult.load(svg_doc).pages[0].width_mm == pytest.approx(400, abs=0.01)
    _no_leftovers(svg_doc)


@needs_svg
def test_retry_after_a_stale_meta_does_not_scale_twice(svg_doc: Path, cfg: AppConfig) -> None:
    """Crash window: canonical.pdf already replaced, meta.json still the old one."""
    stale = (svg_doc / "meta.json").read_bytes()
    set_physical_size(svg_doc, 0, width_mm=400, config=cfg)
    (svg_doc / "meta.json").write_bytes(stale)

    page = set_physical_size(svg_doc, 0, width_mm=400, config=cfg).pages[0]
    assert _pdf_width_mm(svg_doc) == pytest.approx(400, abs=0.05)
    assert page.width_mm == pytest.approx(400, abs=0.01)
    assert page.height_mm == pytest.approx(200, abs=0.05)
    boxes = page.boxes_mm
    assert boxes
    for box in boxes.values():
        assert max(box) <= 400.05

    # dpi is relative to the native pixel size, whatever the current scale
    page = set_physical_size(svg_doc, 0, dpi=96, config=cfg).pages[0]
    assert page.width_mm == pytest.approx(200 * 25.4 / 96, abs=0.01)
    native = json.loads((svg_doc / NATIVE_SIZE_FILE).read_text())
    assert native["px"][0] == pytest.approx(200)
