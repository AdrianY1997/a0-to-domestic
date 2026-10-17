"""Raster ingestion end to end (SPEC 6.1, 6.2 ANA-03, 7.3)."""

import io
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import pyvips
from PIL import Image

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.domain.geometry import Rect
from a0td.domain.planner import plan
from a0td.domain.settings import JobSettings
from a0td.ingest import (
    DocFormat,
    DpiSource,
    IngestResult,
    Page,
    PageKind,
    SizeOverride,
    apply_physical_size,
    ingest_document,
    load_occupancy,
    page_dir,
    set_physical_size,
)

Ingested = Callable[[str], tuple[IngestResult, Path]]


def _rect(r: Rect | None) -> list[float]:
    assert r is not None
    return r.as_list()


def _codes(page: Page) -> set[str]:
    return {w.code.value for w in page.warnings}


def _tif(workdir: Path, index: int = 0) -> pyvips.Image:
    return pyvips.Image.new_from_file(str(workdir / f"page-{index}.tif"))


def _pixels(workdir: Path, index: int = 0) -> np.ndarray:
    im = _tif(workdir, index)
    return np.ndarray(
        buffer=im.write_to_memory(), dtype=np.uint8, shape=(im.height, im.width, im.bands)
    )


def _white_with_square(
    path: Path,
    size: tuple[int, int] = (800, 1000),
    box: tuple[int, int, int, int] = (200, 300, 400, 500),
    dpi: float | None = 100,
) -> Path:
    arr = np.full((size[1], size[0]), 255, dtype=np.uint8)
    arr[box[1] : box[3], box[0] : box[2]] = 0
    Image.fromarray(arr).save(path, dpi=(dpi, dpi) if dpi else None)
    return path


# --------------------------------------------------------------------------- fixtures


def test_g4_scan_stays_one_bit_and_tiled(ingested: Ingested) -> None:
    result, workdir = ingested("scan_a0_200dpi_g4.tif")
    assert result.format is DocFormat.TIFF
    assert result.kind == "raster"
    assert result.page_files == ["page-0.tif"]
    (page,) = result.pages
    assert page.kind is PageKind.RASTER
    assert (page.px.w, page.px.h) == (6622, 9362)  # type: ignore[union-attr]
    assert page.dpi == pytest.approx(200, abs=0.01)
    assert page.dpi_source is DpiSource.METADATA
    assert (page.width_mm, page.height_mm) == pytest.approx((840.99, 1188.97), abs=0.05)
    assert page.standard_match is not None
    assert page.standard_match.id == "A0"
    assert _codes(page) == set()
    assert not page.has_text
    with Image.open(workdir / "page-0.tif") as img:
        assert img.mode == "1"
        assert img.tag_v2[322] == 256  # TileWidth
        assert img.tag_v2[259] in (8, 32946)  # deflate
    assert (page_dir(workdir, 0) / "preview.webp").is_file()


def test_no_dpi_photo_assumes_a0(ingested: Ingested) -> None:
    result, workdir = ingested("photo_nodpi.jpg")
    (page,) = result.pages
    assert page.dpi_source is DpiSource.ASSUMED
    assert WarningCode.PHYSICAL_SIZE_ASSUMED.value in _codes(page)
    assert page.standard_match is not None
    assert page.standard_match.id == "A0"
    assert page.standard_match.orientation.value == "portrait"  # the image orientation
    assert page.width_mm < page.height_mm
    assert max(page.width_mm, page.height_mm) == pytest.approx(1189, abs=0.5)
    assert page.dpi == pytest.approx(25.4 * 5657 / 1189, rel=0.002)
    assert page.to_geometry().physical_size_assumed
    im = _tif(workdir)
    assert (im.width, im.height, im.bands) == (4000, 5657, 3)


def test_transparent_png_is_flattened_on_white(ingested: Ingested, fixtures_dir: Path) -> None:
    result, workdir = ingested("transparent.png")
    (page,) = result.pages
    assert page.dpi_source is DpiSource.METADATA
    assert (page.width_mm, page.height_mm) == pytest.approx((67.7, 50.8), abs=0.05)
    assert _codes(page) == {WarningCode.NONSTANDARD_SIZE.value}
    px = _pixels(workdir)
    assert px.shape[2] == 3
    with Image.open(fixtures_dir / "transparent.png") as img:
        rgba = np.asarray(img.convert("RGBA")).astype(int)
    alpha = rgba[..., 3] / 255.0
    expected = rgba[..., :3] * alpha[..., None] + 255 * (1 - alpha[..., None])
    assert np.abs(px.astype(int) - expected).mean() < 2.0


def test_alpha_math(tmp_path: Path) -> None:
    arr = np.zeros((40, 40, 4), dtype=np.uint8)
    arr[:20, :, :] = (255, 0, 0, 0)  # transparent red
    arr[20:, :20, :] = (0, 0, 0, 128)  # half black
    arr[20:, 20:, :] = (0, 0, 255, 255)  # opaque blue
    src = tmp_path / "alpha.png"
    Image.fromarray(arr, "RGBA").save(src, dpi=(10, 10))
    ingest_document(src, tmp_path / "out", config=AppConfig(max_page_mm=10000))
    px = _pixels(tmp_path / "out")
    assert px.shape == (40, 40, 3)
    assert tuple(px[5, 5]) == (255, 255, 255)
    assert tuple(px[30, 5]) == pytest.approx((127, 127, 127), abs=2)
    assert tuple(px[30, 30]) == (0, 0, 255)


def test_cmyk_jpeg_becomes_srgb(ingested: Ingested) -> None:
    result, workdir = ingested("cmyk.jpg")
    (page,) = result.pages
    assert page.dpi == pytest.approx(150)
    assert (page.width_mm, page.height_mm) == pytest.approx((67.7, 50.8), abs=0.05)
    im = _tif(workdir)
    assert (im.bands, im.interpretation) == (3, "srgb")


def test_multipage_tiff(ingested: Ingested) -> None:
    result, workdir = ingested("multipage.tif")
    assert [p.index for p in result.pages] == [0, 1, 2]
    assert result.page_files == ["page-0.tif", "page-1.tif", "page-2.tif"]
    for page in result.pages:
        assert page.dpi == pytest.approx(100)
        assert (page.width_mm, page.height_mm) == pytest.approx((76.2, 101.6), abs=0.01)
        assert (page_dir(workdir, page.index) / "occupancy.png").is_file()
    # the pages are different images
    assert not np.array_equal(_pixels(workdir, 0), _pixels(workdir, 1))


# ------------------------------------------------------------------------ color depth


def test_sixteen_bit_becomes_eight(tmp_path: Path) -> None:
    ramp = np.tile(np.linspace(0, 65535, 64, dtype=np.uint16), (32, 1))
    rgb = np.stack([ramp, ramp // 2, np.zeros_like(ramp)], axis=2)
    pyvips.Image.new_from_memory(rgb.tobytes(), 64, 32, 3, "ushort").copy(
        interpretation="rgb16"
    ).tiffsave(str(tmp_path / "deep.tif"), xres=100 / 25.4, yres=100 / 25.4, resunit="inch")
    ingest_document(tmp_path / "deep.tif", tmp_path / "out")
    im = _tif(tmp_path / "out")
    assert im.format == "uchar"
    assert (im.bands, im.interpretation) == (3, "srgb")
    px = _pixels(tmp_path / "out")
    assert px[0, 0, 0] == 0
    assert px[0, -1, 0] == 255
    assert px[0, -1, 1] == pytest.approx(127, abs=2)


def test_sixteen_bit_gray(tmp_path: Path) -> None:
    ramp = np.tile(np.linspace(0, 65535, 64, dtype=np.uint16), (32, 1))
    pyvips.Image.new_from_memory(ramp.tobytes(), 64, 32, 1, "ushort").copy(
        interpretation="grey16"
    ).tiffsave(str(tmp_path / "gray16.tif"), xres=3.0, yres=3.0)
    ingest_document(tmp_path / "gray16.tif", tmp_path / "out")
    im = _tif(tmp_path / "out")
    assert (im.format, im.bands) == ("uchar", 1)
    assert _pixels(tmp_path / "out")[0, -1, 0] == 255


def test_gray_alpha_and_palette(tmp_path: Path) -> None:
    la = np.zeros((20, 20, 2), dtype=np.uint8)
    la[..., 0] = 0
    la[:10, :, 1] = 0  # transparent top half -> white
    la[10:, :, 1] = 255  # opaque black bottom half
    Image.fromarray(la, "LA").save(tmp_path / "la.png", dpi=(10, 10))
    ingest_document(tmp_path / "la.png", tmp_path / "la")
    px = _pixels(tmp_path / "la")
    assert px.shape[2] == 1
    assert (px[2, 2, 0], px[15, 2, 0]) == (255, 0)

    pal = Image.new("P", (20, 20))
    pal.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
    pal.paste(1, (0, 0, 10, 20))
    pal.save(tmp_path / "pal.png", dpi=(10, 10))
    ingest_document(tmp_path / "pal.png", tmp_path / "pal")
    px = _pixels(tmp_path / "pal")
    assert px.shape[2] == 3
    assert tuple(px[5, 2]) == (0, 255, 0)
    assert tuple(px[5, 15]) == (255, 0, 0)


@pytest.mark.parametrize("ext", ["bmp", "gif", "webp", "tif", "jpg"])
def test_common_formats(tmp_path: Path, ext: str) -> None:
    arr = np.full((60, 80, 3), 255, dtype=np.uint8)
    arr[10:30, 10:40] = (200, 30, 30)
    src = tmp_path / f"img.{ext}"
    img = Image.fromarray(arr)
    img.save(src, dpi=(25.4 * 2, 25.4 * 2)) if ext in ("jpg", "tif") else img.save(src)
    result = ingest_document(src, tmp_path / "out")
    assert result.kind == "raster"
    assert (result.pages[0].px.w, result.pages[0].px.h) == (80, 60)  # type: ignore[union-attr]
    assert len(result.pages) == 1
    assert result.format.value in {"bmp", "gif", "webp", "tiff", "jpeg"}


def test_animated_gif_uses_first_frame(tmp_path: Path) -> None:
    frames = [Image.new("RGB", (30, 20), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(tmp_path / "a.gif", save_all=True, append_images=frames[1:], loop=0)
    result = ingest_document(tmp_path / "a.gif", tmp_path / "out")
    assert len(result.pages) == 1
    assert _pixels(tmp_path / "out")[0, 0, 0] > 200  # first (red) frame


def test_exif_orientation_is_applied(tmp_path: Path) -> None:
    arr = np.full((20, 40, 3), 255, dtype=np.uint8)
    arr[:, :10] = 0  # black stripe on the left
    img = Image.fromarray(arr)
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 clockwise to display
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95, exif=exif.tobytes(), dpi=(25.4, 25.4))
    (tmp_path / "o.jpg").write_bytes(buf.getvalue())
    result = ingest_document(tmp_path / "o.jpg", tmp_path / "out")
    page = result.pages[0]
    assert (page.px.w, page.px.h) == (20, 40)  # type: ignore[union-attr]
    px = _pixels(tmp_path / "out")
    assert px[2, 10, 0] < 50  # the left stripe is now the top
    assert px[35, 10, 0] > 200


# ----------------------------------------------------------------------- ANA-03 rules


@pytest.mark.parametrize(
    ("size", "dpi", "assumed"),
    [
        ((400, 300), 150, False),  # 67 x 51 mm
        ((400, 300), 1, True),  # ~10 m: implausible
        ((400, 300), 0.5, True),
        ((10, 10), 300, True),  # < 50 mm
        ((20000, 100), 300, False),  # 1.7 m banner
        ((100000, 100), 300, True),  # 8.5 m
    ],
)
def test_implausible_dpi_is_assumed(
    tmp_path: Path, size: tuple[int, int], dpi: float, assumed: bool
) -> None:
    arr = np.full((size[1], size[0]), 255, dtype=np.uint8)
    Image.fromarray(arr).save(tmp_path / "x.png", dpi=(dpi, dpi))
    result = ingest_document(tmp_path / "x.png", tmp_path / "out")
    page = result.pages[0]
    assert (page.dpi_source is DpiSource.ASSUMED) is assumed
    assert (WarningCode.PHYSICAL_SIZE_ASSUMED.value in _codes(page)) is assumed
    if assumed:  # the image fits inside A0 in its own orientation
        assert page.width_mm <= 1189.001
        assert page.height_mm <= 1189.001
        assert min(page.width_mm, page.height_mm) <= 841.001


def test_assumed_a0_follows_image_orientation(tmp_path: Path) -> None:
    arr = np.full((300, 424, 3), 255, dtype=np.uint8)  # landscape, A-series ratio
    Image.fromarray(arr).save(tmp_path / "land.png")
    page = ingest_document(tmp_path / "land.png", tmp_path / "out").pages[0]
    assert (page.width_mm, page.height_mm) == pytest.approx((1189, 841), abs=1.5)
    assert page.standard_match is not None
    assert page.standard_match.orientation.value == "landscape"


def test_set_physical_size_by_width_height_dpi_paper(tmp_path: Path) -> None:
    src = _white_with_square(tmp_path / "a.png", dpi=None)  # 800x1000 px, no dpi
    work = tmp_path / "doc"
    result = ingest_document(src, work)
    assert result.pages[0].dpi_source is DpiSource.ASSUMED

    r = set_physical_size(work, 0, width_mm=400)
    page = r.pages[0]
    assert (page.width_mm, page.height_mm) == pytest.approx((400, 500))
    assert page.dpi == pytest.approx(50.8)
    assert page.dpi_source is DpiSource.USER
    assert WarningCode.PHYSICAL_SIZE_ASSUMED.value not in _codes(page)
    assert IngestResult.load(work).pages[0] == page  # persisted

    page = set_physical_size(work, 0, height_mm=594).pages[0]
    assert (page.width_mm, page.height_mm) == pytest.approx((475.2, 594))

    page = set_physical_size(work, 0, dpi=200).pages[0]
    assert (page.width_mm, page.height_mm) == pytest.approx((101.6, 127))
    assert page.dpi == pytest.approx(200)

    page = set_physical_size(work, 0, paper="A3").pages[0]  # fits 297 x 420 keeping the aspect
    assert page.width_mm <= 297.001
    assert page.height_mm == pytest.approx(
        420 if 420 / 1000 < 297 / 800 else 297 * 1000 / 800, abs=0.01
    )
    assert page.dpi_source is DpiSource.USER

    page = set_physical_size(work, 0, paper="a0").pages[0]
    assert page.width_mm == pytest.approx(841, abs=0.01)  # the width is the limiting side
    assert page.height_mm == pytest.approx(1051.25, abs=0.01)


def test_override_recomputes_occupancy(tmp_path: Path) -> None:
    src = _white_with_square(tmp_path / "a.png", dpi=None)  # square at x 200-400, y 300-500 px
    work = tmp_path / "doc"
    ingest_document(src, work)
    page = set_physical_size(work, 0, width_mm=400).pages[0]  # 0.5 mm per px
    occ = load_occupancy(page_dir(work, 0), page)
    assert _rect(occ.content_bbox()) == pytest.approx(Rect(100, 150, 200, 250).as_list(), abs=1)
    page = set_physical_size(work, 0, width_mm=800).pages[0]  # 1 mm per px
    occ = load_occupancy(page_dir(work, 0), page)
    assert _rect(occ.content_bbox()) == pytest.approx(Rect(200, 300, 400, 500).as_list(), abs=1)


def test_apply_physical_size_validation(tmp_path: Path) -> None:
    result = ingest_document(_white_with_square(tmp_path / "a.png"), tmp_path / "doc")
    page = result.pages[0]
    for kwargs in (
        {},
        {"width_mm": 100, "dpi": 100},
        {"paper": "NOPE"},
        {"width_mm": -3},
        {"dpi": 0},
        {"width_mm": float("nan")},
    ):
        with pytest.raises(DomainError) as exc:
            apply_physical_size(page, **kwargs)  # type: ignore[arg-type]
        assert exc.value.code is ErrorCode.INVALID_SETTINGS
    with pytest.raises(DomainError) as exc:
        apply_physical_size(page, width_mm=20000)
    assert exc.value.code is ErrorCode.PAGE_TOO_LARGE
    with pytest.raises(DomainError) as exc:
        set_physical_size(tmp_path / "doc", 5, dpi=100)
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_override_only_applies_to_rasters(fixtures_dir: Path, tmp_path: Path) -> None:
    result = ingest_document(fixtures_dir / "a1_landscape.pdf", tmp_path)
    with pytest.raises(DomainError) as exc:
        apply_physical_size(result.pages[0], dpi=100)
    assert exc.value.code is ErrorCode.INVALID_SETTINGS


def test_size_override_during_ingest(tmp_path: Path) -> None:
    src = _white_with_square(tmp_path / "a.png", dpi=None)
    result = ingest_document(src, tmp_path / "doc", size_override=SizeOverride(dpi=100))
    page = result.pages[0]
    assert (page.width_mm, page.height_mm) == pytest.approx((203.2, 254))
    assert page.dpi_source is DpiSource.USER
    assert IngestResult.load(tmp_path / "doc").pages[0] == page


# ------------------------------------------------------------------------ occupancy


def test_raster_occupancy_bbox(tmp_path: Path) -> None:
    src = _white_with_square(tmp_path / "a.png", dpi=100)  # 25.4 mm per 100 px
    work = tmp_path / "doc"
    result = ingest_document(src, work)
    page = result.pages[0]
    assert (page.width_mm, page.height_mm) == pytest.approx((203.2, 254), abs=0.01)
    occ = load_occupancy(page_dir(work, 0), page)
    mm = 25.4 / 100
    assert _rect(occ.content_bbox()) == pytest.approx(
        Rect(200 * mm, 300 * mm, 400 * mm, 500 * mm).as_list(), abs=1
    )
    assert occ.any_in(Rect(60, 80, 70, 90))
    assert not occ.any_in(Rect(0, 0, 40, 40))
    assert not occ.any_in(Rect(120, 20, 203, 254))
    p = plan(page.to_geometry(), JobSettings(), occupancy=occ)
    assert p.counts.total >= 1


def test_raster_specks_are_removed_and_background_follows_p99(tmp_path: Path) -> None:
    arr = np.full((1000, 800), 225, dtype=np.uint8)  # yellowed paper, not pure white
    arr[::97, ::89] = 0  # isolated one-pixel dust
    arr[300:310, 100:700] = 20  # a real horizontal rule
    Image.fromarray(arr).save(tmp_path / "scan.png", dpi=(50.8, 50.8))  # 2 px per mm
    work = tmp_path / "doc"
    page = ingest_document(tmp_path / "scan.png", work).pages[0]
    occ = load_occupancy(page_dir(work, 0), page)
    bbox = occ.content_bbox()
    assert bbox is not None
    assert bbox.y0 == pytest.approx(150, abs=1.5)
    assert bbox.y1 == pytest.approx(155, abs=1.5)
    assert bbox.x0 == pytest.approx(50, abs=1.5)
    assert bbox.x1 == pytest.approx(350, abs=1.5)
    assert not occ.any_in(Rect(0, 0, 400, 140))


def test_sensitivity_maps(tmp_path: Path) -> None:
    arr = np.full((400, 400), 255, dtype=np.uint8)
    arr[100:200, 100:200] = 245  # faint tint: delta 10
    Image.fromarray(arr).save(tmp_path / "faint.png", dpi=(50.8, 50.8))
    work = tmp_path / "doc"
    page = ingest_document(tmp_path / "faint.png", work).pages[0]
    from a0td.domain.settings import BlankSensitivity

    tint = Rect(55, 55, 95, 95)
    assert load_occupancy(page_dir(work, 0), page, BlankSensitivity.STRICT).any_in(tint)
    assert not load_occupancy(page_dir(work, 0), page, BlankSensitivity.NORMAL).any_in(tint)
    assert not load_occupancy(page_dir(work, 0), page, BlankSensitivity.TOLERANT).any_in(tint)


def test_blank_image_has_no_content(tmp_path: Path) -> None:
    Image.fromarray(np.full((300, 300), 255, dtype=np.uint8)).save(
        tmp_path / "w.png", dpi=(50.8, 50.8)
    )
    page = ingest_document(tmp_path / "w.png", tmp_path / "doc").pages[0]
    occ = load_occupancy(page_dir(tmp_path / "doc", 0), page)
    assert occ.content_bbox() is None
    assert not occ.any_in(Rect(0, 0, 1000, 1000))


# ------------------------------------------------------------------------ malicious


def test_pixel_bomb_is_rejected(fixtures_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(DomainError) as exc:
        ingest_document(fixtures_dir / "malicious" / "pixel_bomb.png", tmp_path)
    assert exc.value.code is ErrorCode.PIXEL_LIMIT_EXCEEDED
    assert not list(tmp_path.glob("page-*.tif"))


def test_megapixel_limit_follows_config(tmp_path: Path) -> None:
    Image.fromarray(np.full((1500, 1500), 255, dtype=np.uint8)).save(
        tmp_path / "big.png"
    )  # 2.25 Mpx
    with pytest.raises(DomainError) as exc:
        ingest_document(tmp_path / "big.png", tmp_path / "a", config=AppConfig(max_megapixels=2))
    assert exc.value.code is ErrorCode.PIXEL_LIMIT_EXCEEDED
    ingest_document(tmp_path / "big.png", tmp_path / "b", config=AppConfig(max_megapixels=3))


def test_tiff_page_limit(fixtures_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(DomainError) as exc:
        ingest_document(fixtures_dir / "multipage.tif", tmp_path, config=AppConfig(max_pages=2))
    assert exc.value.code is ErrorCode.TOO_MANY_PAGES


def test_corrupt_image(tmp_path: Path) -> None:
    src = tmp_path / "bad.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "out")
    assert exc.value.code in (ErrorCode.CORRUPT_FILE, ErrorCode.CONVERSION_FAILED)


def test_no_scratch_left_behind(tmp_path: Path) -> None:
    ingest_document(_white_with_square(tmp_path / "a.png"), tmp_path / "doc")
    assert not (tmp_path / "doc" / ".work").exists()


def test_anisotropic_dpi_reaches_physical_size(tmp_path: Path, cfg: AppConfig) -> None:
    path = tmp_path / "aniso.png"
    arr = np.full((1000, 800), 255, dtype=np.uint8)
    Image.fromarray(arr).save(path, dpi=(100, 200))
    result = ingest_document(path, tmp_path / "work", config=cfg)
    (page,) = result.pages
    assert page.dpi_source is DpiSource.METADATA
    assert (page.width_mm, page.height_mm) == pytest.approx((203.2, 127.0), abs=0.05)
