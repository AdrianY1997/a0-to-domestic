"""PDF ingestion end to end (SPEC 6.1, 6.2, 7.3), driven by the generated fixtures."""

import shutil
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.domain.geometry import Rect
from a0td.domain.planner import plan
from a0td.domain.settings import JobSettings, SourceBox
from a0td.ingest import (
    DocFormat,
    IngestResult,
    Page,
    PageKind,
    Progress,
    ingest_document,
    load_occupancy,
    page_dir,
)

Ingested = Callable[[str], tuple[IngestResult, Path]]


def _codes(page: Page) -> set[str]:
    return {w.code.value for w in page.warnings}


def _doc_codes(result: IngestResult) -> set[str]:
    return {w.code.value for w in result.warnings}


def _pix(page: pymupdf.Page, dpi: int = 40) -> np.ndarray:
    return np.frombuffer(page.get_pixmap(dpi=dpi, alpha=False).samples, dtype=np.uint8).astype(int)


def test_vector_a0(ingested: Ingested) -> None:
    result, workdir = ingested("a0_vector_grid.pdf")
    assert result.format is DocFormat.PDF
    assert result.mime == "application/pdf"
    assert result.kind == "pdf"
    assert result.warnings == []
    (page,) = result.pages
    assert (page.width_mm, page.height_mm) == pytest.approx((841, 1189), abs=0.5)
    assert page.kind is PageKind.VECTOR
    assert page.has_text
    assert page.standard_match is not None
    assert (page.standard_match.id, page.standard_match.orientation.value) == ("A0", "portrait")
    assert page.px is None
    assert page.dpi is None
    assert page.boxes_mm[SourceBox.CROP] == pytest.approx((0, 0, page.width_mm, page.height_mm))
    assert _codes(page) == set()
    # SPEC 11.2 layout
    assert (workdir / "canonical.pdf").is_file()
    assert (workdir / "meta.json").is_file()
    for name in ("preview.webp", "thumb.webp", "occupancy.png"):
        assert (page_dir(workdir, 0) / name).is_file()
    assert IngestResult.load(workdir) == result


def test_previews(ingested: Ingested) -> None:
    _, workdir = ingested("a0_vector_grid.pdf")
    with Image.open(page_dir(workdir, 0) / "preview.webp") as img:
        assert img.format == "WEBP"
        assert max(img.size) == 4096
        assert img.width / img.height == pytest.approx(841 / 1189, abs=0.002)
    with Image.open(page_dir(workdir, 0) / "thumb.webp") as img:
        assert max(img.size) == 512


def test_preview_size_follows_config(fixtures_dir: Path, tmp_path: Path) -> None:
    cfg = AppConfig(preview_max_px=1024)
    ingest_document(fixtures_dir / "a1_landscape.pdf", tmp_path, config=cfg)
    with Image.open(page_dir(tmp_path, 0) / "preview.webp") as img:
        assert max(img.size) == 1024
        assert img.width > img.height


def test_canonical_pdf_is_upright_and_clean(ingested: Ingested) -> None:
    _, workdir = ingested("a0_vector_grid.pdf")
    with pymupdf.open(workdir / "canonical.pdf") as doc:
        page = doc[0]
        assert page.rotation == 0
        assert page.rect.x0 == 0
        assert page.rect.y0 == 0
        assert not doc.is_encrypted
        assert not list(page.annots() or [])


def test_a1_landscape(ingested: Ingested) -> None:
    (page,) = ingested("a1_landscape.pdf")[0].pages
    assert (page.width_mm, page.height_mm) == pytest.approx((841, 594), abs=0.5)
    assert page.standard_match is not None
    assert (page.standard_match.id, page.standard_match.orientation.value) == ("A1", "landscape")


def test_rotate_is_baked_into_the_content(ingested: Ingested, fixtures_dir: Path) -> None:
    result, workdir = ingested("rotated_90.pdf")
    (page,) = result.pages
    assert (page.width_mm, page.height_mm) == pytest.approx((420, 297), abs=0.5)
    assert page.boxes_mm[SourceBox.CROP] == pytest.approx((0, 0, 420, 297), abs=0.01)
    with (
        pymupdf.open(fixtures_dir / "rotated_90.pdf") as src,
        pymupdf.open(workdir / "canonical.pdf") as dst,
    ):
        assert src[0].rotation == 90
        assert dst[0].rotation == 0
        assert dst[0].rect == src[0].rect
        # Same picture as the viewer shows for the original (which applies /Rotate itself).
        assert np.abs(_pix(src[0]) - _pix(dst[0])).mean() < 0.5


def test_boxes_are_kept_in_canonical_coordinates(ingested: Ingested, fixtures_dir: Path) -> None:
    result, workdir = ingested("boxes.pdf")
    (page,) = result.pages
    # CropBox (inset 20 mm in a 420x594 MediaBox) defines the page and the origin.
    assert (page.width_mm, page.height_mm) == pytest.approx((380, 554), abs=0.01)
    assert page.boxes_mm[SourceBox.CROP] == pytest.approx((0, 0, 380, 554), abs=0.01)
    assert page.boxes_mm[SourceBox.MEDIA] == pytest.approx((-20, -20, 400, 574), abs=0.01)
    assert page.boxes_mm[SourceBox.TRIM] == pytest.approx((10, 10, 370, 544), abs=0.01)
    assert page.boxes_mm[SourceBox.BLEED] == pytest.approx((5, 5, 375, 549), abs=0.01)
    assert page.standard_match is None
    assert WarningCode.NONSTANDARD_SIZE.value in _codes(page)
    with (
        pymupdf.open(fixtures_dir / "boxes.pdf") as src,
        pymupdf.open(workdir / "canonical.pdf") as dst,
    ):
        assert dst[0].rect == src[0].rect
        assert np.abs(_pix(src[0]) - _pix(dst[0])).mean() < 0.5


def test_annotations_and_widgets_are_flattened(ingested: Ingested, fixtures_dir: Path) -> None:
    result, workdir = ingested("annotations.pdf")
    flat = [w for w in result.warnings if w.code is WarningCode.ANNOTATIONS_FLATTENED]
    assert len(flat) == 1
    assert flat[0].params == {"pages": [1]}
    with (
        pymupdf.open(fixtures_dir / "annotations.pdf") as src,
        pymupdf.open(workdir / "canonical.pdf") as dst,
    ):
        assert list(src[0].annots() or [])
        assert not list(dst[0].annots() or [])
        assert not list(dst[0].widgets() or [])
        assert dst.is_form_pdf == 0
        # The annotation look is part of the content now.
        assert np.abs(_pix(src[0]) - _pix(dst[0])).mean() < 1.0


def test_password_flow(fixtures_dir: Path, tmp_path: Path) -> None:
    src = fixtures_dir / "encrypted.pdf"
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "a")
    assert exc.value.code is ErrorCode.PASSWORD_REQUIRED
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "b", password="wrong")
    assert exc.value.code is ErrorCode.PASSWORD_INVALID
    result = ingest_document(src, tmp_path / "c", password="secret")
    assert result.pages[0].standard_match is not None
    assert result.pages[0].standard_match.id == "A3"
    with pymupdf.open(tmp_path / "c" / "canonical.pdf") as doc:  # opens without a password
        assert not doc.needs_pass
        assert not doc.is_encrypted
        assert doc.page_count == 1


def test_print_not_allowed(fixtures_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(DomainError) as exc:
        ingest_document(fixtures_dir / "encrypted_noprint.pdf", tmp_path)
    assert exc.value.code is ErrorCode.PRINT_NOT_ALLOWED
    assert not (tmp_path / "canonical.pdf").exists()


@pytest.mark.skipif(shutil.which("qpdf") is None, reason="qpdf is the repair fallback")
def test_corrupt_pdf_is_repaired(ingested: Ingested) -> None:
    result, workdir = ingested("corrupt.pdf")
    assert WarningCode.PDF_REPAIRED.value in _doc_codes(result)
    assert len(result.pages) >= 1
    with pymupdf.open(workdir / "canonical.pdf") as doc:
        assert doc.page_count == len(result.pages)
        assert not doc.is_repaired


def test_unrepairable_pdf(tmp_path: Path) -> None:
    src = tmp_path / "junk.pdf"
    src.write_bytes(b"%PDF-1.7\n" + b"this is not a pdf at all\n" * 50)
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "out")
    assert exc.value.code is ErrorCode.CORRUPT_FILE


def test_layers_are_reported_and_kept(ingested: Ingested) -> None:
    result, workdir = ingested("layers_ocg.pdf")
    (page,) = result.pages
    assert WarningCode.LAYERS_PRESENT.value in _codes(page)
    assert [(layer.name, layer.on) for layer in page.layers] == [
        ("Talla 38", True),
        ("Talla 40", True),
        ("Talla 42", True),
    ]
    with pymupdf.open(workdir / "canonical.pdf") as doc:
        ocgs = doc.get_ocgs()
        assert {o["name"] for o in ocgs.values()} == {"Talla 38", "Talla 40", "Talla 42"}
        assert {layer.xref for layer in page.layers} == set(ocgs)


def test_multipage_mixed_sizes(ingested: Ingested) -> None:
    result, workdir = ingested("multipage_mixed.pdf")
    assert [p.index for p in result.pages] == [0, 1, 2]
    assert [p.standard_match.id for p in result.pages if p.standard_match] == ["A0", "A1", "A3"]
    for page in result.pages:
        assert (page_dir(workdir, page.index) / "occupancy.png").is_file()
        assert (page_dir(workdir, page.index) / "preview.webp").is_file()
    with pymupdf.open(workdir / "canonical.pdf") as doc:
        assert doc.page_count == 3
        sizes = [(round(p.rect.width * 25.4 / 72), round(p.rect.height * 25.4 / 72)) for p in doc]
    assert sizes == [(841, 1189), (594, 841), (297, 420)]


def test_progress_events(fixtures_dir: Path, tmp_path: Path) -> None:
    events: list[Progress] = []
    ingest_document(fixtures_dir / "multipage_mixed.pdf", tmp_path, progress=events.append)
    assert events[0].stage == "detect"
    assert events[-1].stage == "done"
    assert events[-1].pct == 100
    pcts = [e.pct for e in events]
    assert pcts == sorted(pcts)
    assert {e.stage for e in events} >= {"convert", "analyze", "preview"}


# --------------------------------------------------------------------------- malicious


def test_pdf_javascript_is_neutralized(fixtures_dir: Path, tmp_path: Path) -> None:
    result = ingest_document(fixtures_dir / "malicious" / "pdf_javascript.pdf", tmp_path)
    assert len(result.pages) == 1
    raw = (tmp_path / "canonical.pdf").read_bytes()
    for needle in (b"JavaScript", b"/JS", b"pwned"):
        assert needle not in raw
    with pymupdf.open(tmp_path / "canonical.pdf") as doc:
        cat = doc.pdf_catalog()
        assert doc.xref_get_key(cat, "OpenAction")[0] == "null"


def test_pdf_with_too_many_pages_is_rejected(fixtures_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(DomainError) as exc:
        ingest_document(fixtures_dir / "malicious" / "pdf_10000_pages.pdf", tmp_path)
    assert exc.value.code is ErrorCode.TOO_MANY_PAGES
    assert exc.value.params == {"pages": 10000, "limit": 100}
    assert not (tmp_path / "canonical.pdf").exists()


def test_page_limit_follows_config(fixtures_dir: Path, tmp_path: Path) -> None:
    cfg = AppConfig(max_pages=2)
    with pytest.raises(DomainError) as exc:
        ingest_document(fixtures_dir / "multipage_mixed.pdf", tmp_path, config=cfg)
    assert exc.value.code is ErrorCode.TOO_MANY_PAGES


def test_page_too_large(fixtures_dir: Path, tmp_path: Path) -> None:
    cfg = AppConfig(max_page_mm=1000)
    with pytest.raises(DomainError) as exc:
        ingest_document(fixtures_dir / "a0_vector_grid.pdf", tmp_path, config=cfg)
    assert exc.value.code is ErrorCode.PAGE_TOO_LARGE


def test_misleading_extension_is_detected_by_content(fixtures_dir: Path, tmp_path: Path) -> None:
    result = ingest_document(fixtures_dir / "malicious" / "misleading_ext.pdf", tmp_path)
    assert result.format is DocFormat.PNG
    assert result.kind == "raster"


# ------------------------------------------------------------------ synthetic PDF cases


def _pdf(path: Path, build: Callable[[pymupdf.Document], None]) -> Path:
    doc = pymupdf.open()
    build(doc)
    doc.save(path)
    doc.close()
    return path


def test_user_unit_is_baked(tmp_path: Path) -> None:
    def build(doc: pymupdf.Document) -> None:
        page = doc.new_page(width=200, height=100)
        page.draw_rect(pymupdf.Rect(10, 10, 60, 40), fill=(0, 0, 0))
        doc.xref_set_key(page.xref, "UserUnit", "2")

    src = _pdf(tmp_path / "uu.pdf", build)
    result = ingest_document(src, tmp_path / "out")
    page = result.pages[0]
    assert (page.width_mm, page.height_mm) == pytest.approx(
        (400 * 25.4 / 72, 200 * 25.4 / 72), abs=0.01
    )
    with pymupdf.open(tmp_path / "out" / "canonical.pdf") as doc:
        assert doc[0].rect.width == pytest.approx(400)
        assert doc.xref_get_key(doc[0].xref, "UserUnit")[0] == "null"
    occ = load_occupancy(page_dir(tmp_path / "out", 0), page)
    bbox = occ.content_bbox()
    assert bbox is not None
    assert bbox.x0 == pytest.approx(20 * 25.4 / 72, abs=1)  # 10 pt scaled by the user unit


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_all_rotations_render_identically(tmp_path: Path, rotation: int) -> None:
    def build(doc: pymupdf.Document) -> None:
        page = doc.new_page(width=300, height=200)
        page.draw_rect(pymupdf.Rect(0, 0, 100, 50), fill=(0, 0, 0))
        page.insert_text((150, 120), "Hello", fontsize=24)
        page.set_rotation(rotation)

    src = _pdf(tmp_path / "rot.pdf", build)
    ingest_document(src, tmp_path / "out")
    with pymupdf.open(src) as a, pymupdf.open(tmp_path / "out" / "canonical.pdf") as b:
        assert b[0].rect == a[0].rect
        assert np.abs(_pix(a[0]) - _pix(b[0])).mean() < 0.5


def test_page_classification(tmp_path: Path) -> None:
    image = Image.fromarray(np.random.default_rng(1).integers(0, 255, (60, 80, 3), dtype=np.uint8))
    png = tmp_path / "noise.png"
    image.save(png)

    def build(doc: pymupdf.Document) -> None:
        full = doc.new_page(width=400, height=300)  # scan: one image over the whole page
        full.insert_image(full.rect, filename=str(png))
        mixed = doc.new_page(width=400, height=300)  # half image, half drawing
        mixed.insert_image(pymupdf.Rect(0, 0, 400, 150), filename=str(png))
        for i in range(30):
            mixed.draw_line((10 + i * 5, 200), (10 + i * 5, 280))
        mixed.insert_text((20, 180), "text")
        vec = doc.new_page(width=400, height=300)
        vec.draw_rect(pymupdf.Rect(10, 10, 200, 100), fill=(0, 0, 1))
        vec.insert_image(pymupdf.Rect(0, 0, 10, 10), filename=str(png))  # a tiny logo
        doc.new_page(width=400, height=300)  # empty

    src = _pdf(tmp_path / "kinds.pdf", build)
    result = ingest_document(src, tmp_path / "out")
    assert [p.kind for p in result.pages] == [
        PageKind.RASTER,
        PageKind.MIXED,
        PageKind.VECTOR,
        PageKind.VECTOR,
    ]
    assert [p.has_text for p in result.pages] == [False, True, False, False]


# ------------------------------------------------------------------------ occupancy


def test_blank_half_page_is_detected(ingested: Ingested) -> None:
    result, workdir = ingested("a0_top_half_only.pdf")
    page = result.pages[0]
    occ = load_occupancy(page_dir(workdir, 0), page)
    bbox = occ.content_bbox()
    assert bbox is not None
    assert bbox.y1 == pytest.approx(595, abs=3)
    assert occ.any_in(Rect(0, 0, 841, 300))
    assert not occ.any_in(Rect(0, 650, 841, 1189))
    assert not occ.any_in(Rect(0, 620, 841, 1189))
    # Wiring with the planner: the lower tiles are blank.
    p = plan(page.to_geometry(), JobSettings(), occupancy=occ)
    assert p.counts.blank > 0
    assert p.counts.printed + p.counts.blank == p.counts.total


def test_grid_page_is_fully_occupied(ingested: Ingested) -> None:
    result, workdir = ingested("a0_vector_grid.pdf")
    occ = load_occupancy(page_dir(workdir, 0), result.pages[0])
    bbox = occ.content_bbox()
    assert bbox is not None
    assert bbox.width == pytest.approx(841, abs=2)
    assert bbox.height == pytest.approx(1189, abs=2)
    assert occ.any_in(Rect(400, 500, 410, 510))


def test_occupancy_png_is_one_bit(ingested: Ingested) -> None:
    _, workdir = ingested("a0_top_half_only.pdf")
    with Image.open(page_dir(workdir, 0) / "occupancy.png") as img:
        assert img.mode == "1"
        assert img.size == (1682, 2378)  # 2 px/mm


def test_page_geometry_for_the_planner(ingested: Ingested) -> None:
    result, _ = ingested("multipage_mixed.pdf")
    geo = result.pages[1].to_geometry(len(result.pages))
    assert geo.index == 1
    assert geo.page_count == 3
    assert geo.width_mm == pytest.approx(594, abs=0.5)
    assert not geo.is_raster


# ------------------------------------------------------------------ /Rotate + offset CropBox


@pytest.mark.parametrize("rotate", [90, 270])
def test_rotate_with_offset_cropbox_and_trimbox(
    rotate: int, tmp_path: Path, cfg: AppConfig
) -> None:
    src = tmp_path / "rot.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.draw_rect(pymupdf.Rect(0, 0, 600, 800), color=None, fill=(0.9, 0.9, 0.9))
    page.draw_rect(pymupdf.Rect(60, 70, 200, 200), color=None, fill=(1, 0, 0))
    page.draw_rect(pymupdf.Rect(300, 400, 440, 690), color=None, fill=(0, 0, 1))
    page.draw_rect(pymupdf.Rect(100, 600, 250, 690), color=None, fill=(0, 0.7, 0))
    page.set_cropbox(pymupdf.Rect(50, 60, 450, 700))
    page.set_trimbox(pymupdf.Rect(80, 90, 400, 650))
    page.set_rotation(rotate)
    doc.save(src)
    doc.close()

    result = ingest_document(src, tmp_path / "work", config=cfg)
    (meta,) = result.pages
    pt = 25.4 / 72
    assert (meta.width_mm, meta.height_mm) == pytest.approx((640 * pt, 400 * pt), abs=0.01)
    # Trim 320x560 pt unrotated, inside the crop at offset (30, 30) from its top-left.
    trim = meta.boxes_mm[SourceBox.TRIM]
    expected = (50, 30, 610, 350) if rotate == 90 else (30, 50, 590, 370)
    assert trim == pytest.approx(tuple(v * pt for v in expected), abs=0.01)
    media = meta.boxes_mm[SourceBox.MEDIA]
    assert (media[2] - media[0], media[3] - media[1]) == pytest.approx(
        (800 * pt, 600 * pt), abs=0.01
    )

    with pymupdf.open(src) as a, pymupdf.open(tmp_path / "work" / "canonical.pdf") as b:
        pa, pb = _pix(a[0], 50), _pix(b[0], 50)
    assert pa.shape == pb.shape
    assert np.abs(pa - pb).mean() < 2.0
    assert (np.abs(pa - pb) > 64).mean() < 0.01
