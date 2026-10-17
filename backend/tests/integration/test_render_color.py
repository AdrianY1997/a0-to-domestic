"""Colour modes and rasterized output (COL-02..05, SAL-04)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
import pytest
import pyvips
from numpy.typing import NDArray

from a0td.domain.settings import ColorSettings
from a0td.render import RenderResult
from a0td.render.color import GRAY_CACHE_NAME, apply_color, whiten
from a0td.render.engine import render_sheet_preview
from render_support import direct_vector, make_settings, mismatch_fraction, reassemble

Render = Callable[..., tuple[RenderResult, Path]]
SMALL: dict[str, Any] = {"mode": "percent", "percent": 8}
NO_MARKS: dict[str, Any] = {"assembly_page": "none", "labels": False, "ruler": False}
PNG_MAGIC = bytes([0x89]) + b"PNG"


def _rgb(page: pymupdf.Page) -> NDArray[np.uint8]:
    pix = page.get_pixmap(dpi=60, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)


def _vips(arr: NDArray[np.uint8]) -> Any:
    h, w = arr.shape[:2]
    bands = 1 if arr.ndim == 2 else arr.shape[2]
    return pyvips.Image.new_from_memory(arr.tobytes(), w, h, bands, "uchar")


def _np(im: Any) -> NDArray[np.uint8]:
    return np.frombuffer(im.write_to_memory(), np.uint8).reshape(im.height, im.width, im.bands)


# ------------------------------------------------------------------------ pixel operations


def test_bw_threshold_and_invert() -> None:
    arr = np.array([[0, 100, 159, 160, 161, 255]], np.uint8)
    out = _np(apply_color(_vips(arr), ColorSettings(mode="bw", threshold=160)))
    assert out[0, :, 0].tolist() == [0, 0, 0, 0, 255, 255]
    inv = _np(apply_color(_vips(arr), ColorSettings(mode="bw", threshold=160, invert=True)))
    assert inv[0, :, 0].tolist() == [255, 255, 255, 255, 0, 0]


def test_high_threshold_turns_any_non_white_black() -> None:
    rgb = np.full((4, 4, 3), 255, np.uint8)
    rgb[1, 1] = (255, 240, 240)  # pale red
    out = _np(apply_color(_vips(rgb), ColorSettings(mode="bw", threshold=250)))
    assert out.shape[2] == 1
    assert out[1, 1, 0] == 0
    assert out[0, 0, 0] == 255


def test_whiten_lifts_background_and_removes_dust_but_keeps_lines() -> None:
    rng = np.random.default_rng(1)
    arr = rng.integers(200, 226, (200, 200), dtype=np.uint8)  # grey paper
    arr[50, 20:180] = 20  # a 1 px line
    arr[120, 120] = 10  # an isolated speck
    out = _np(whiten(_vips(arr)))[:, :, 0]
    assert (out[10:40, 10:40] == 255).all()  # background is paper white
    assert out[50, 20:180].max() < 128  # the line survives
    assert out[120, 120] == 255  # dust gone


def test_grayscale_of_raster_has_one_band() -> None:
    rgb = np.zeros((4, 4, 3), np.uint8)
    rgb[:, :, 0] = 255
    assert apply_color(_vips(rgb), ColorSettings(mode="grayscale")).bands == 1


# ------------------------------------------------------------------------------ end to end


def test_grayscale_vector_stays_vector_and_is_cached(
    render: Render, rfx_ingest: Callable[[str], Path]
) -> None:
    result, pdf = render(
        "a0_vector_grid.pdf", scale=SMALL, color={"mode": "grayscale"}, marks=NO_MARKS
    )
    assert result.warnings == []
    with pymupdf.open(pdf) as doc:
        assert doc.page_count >= 1
        for page in doc:
            assert page.get_images() == []
            rgb = _rgb(page).astype(int)
            assert int(np.abs(rgb[:, :, 0] - rgb[:, :, 1]).max()) <= 1
            assert int(np.abs(rgb[:, :, 1] - rgb[:, :, 2]).max()) <= 1
    assert (rfx_ingest("a0_vector_grid.pdf") / "cache" / GRAY_CACHE_NAME).exists()


def test_bw_vector_is_rasterized_one_bit(render: Render) -> None:
    result, pdf = render(
        "a0_vector_grid.pdf", scale=SMALL, color={"mode": "bw", "threshold": 200}, marks=NO_MARKS
    )
    assert result.warnings == []
    with pymupdf.open(pdf) as doc:
        imgs = [img for page in doc for img in page.get_images(full=True)]
        assert imgs, "the black and white page must be embedded as pixels"
        assert all(img[4] == 1 for img in imgs)


def test_invert_scan_makes_paper_dark(render: Render) -> None:
    _r, normal = render("scan_a0_200dpi_g4.tif", scale=SMALL, marks=NO_MARKS)
    with pymupdf.open(normal) as doc:
        light = float(_rgb(doc[0]).mean())
    _r, inverted = render(
        "scan_a0_200dpi_g4.tif", scale=SMALL, marks=NO_MARKS, color={"invert": True}
    )
    with pymupdf.open(inverted) as doc:
        dark = float(_rgb(doc[0]).mean())
    assert light > 200
    assert dark < light - 15


def test_whiten_background_on_scan_runs(render: Render) -> None:
    result, pdf = render(
        "scan_a0_200dpi_g4.tif", scale=SMALL, marks=NO_MARKS, color={"whiten_background": True}
    )
    assert result.warnings == []
    with pymupdf.open(pdf) as doc:
        assert doc.page_count >= 1


def test_rasterize_vector_at_requested_dpi(render: Render) -> None:
    result, pdf = render(
        "a0_vector_grid.pdf", scale=SMALL, output={"rasterize": True, "dpi": 150}, marks=NO_MARKS
    )
    assert result.warnings == []
    plan = result.plans[0]
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        assert page.get_images(), "rasterized output embeds images"
        assert page.get_text().strip() == ""
        w_px = max(img[2] for img in page.get_images(full=True))
        # effective resolution = dpi / scale; a crop is at most the content area wide
        assert w_px <= (plan.content_mm[0] / 25.4) * 150 / plan.scale + 3


def test_bilevel_downscale_stays_one_bit(render: Render) -> None:
    _r, pdf = render(
        "scan_a0_200dpi_g4.tif",
        scale={"mode": "percent", "percent": 100},
        output={"max_dpi": 100},
        marks=NO_MARKS,
        tiling={"only_sheets": ["A1"]},
    )
    with pymupdf.open(pdf) as doc:
        imgs = doc[0].get_images(full=True)
        assert imgs
        assert all(img[4] == 1 for img in imgs)  # COL-05: nearest neighbour keeps 1 bit


@pytest.mark.parametrize("mode", ["grayscale", "bw"])
def test_preview_honours_colour_mode(mode: str, rfx_ingest: Callable[[str], Path]) -> None:
    settings = make_settings(scale=SMALL, color={"mode": mode}, marks=NO_MARKS)
    png = render_sheet_preview(rfx_ingest("scan_a0_200dpi_g4.tif"), settings, 0, "A1", 300)
    assert png.startswith(PNG_MAGIC)


# ----------------------------------------------------------------------- WP-15 audit fixes


def _reassembly_matches(
    render: Render, rfx_ingest: Callable[[str], Path], name: str, rotate: int = 0, **kw: Any
) -> None:
    marks = dict(NO_MARKS, assembly_page="none")
    result, pdf = render(name, marks=marks, source={"rotate": rotate}, **kw)
    plan = result.plans[0]
    dpi = 150
    with pymupdf.open(pdf) as doc:
        got, xs, ys = reassemble(doc, plan, 0, dpi)
    want = direct_vector(rfx_ingest(name) / "canonical.pdf", 0, plan, dpi, rotate)
    assert got.shape[0] == pytest.approx(want.shape[0], abs=2)
    assert got.shape[1] == pytest.approx(want.shape[1], abs=2)
    assert mismatch_fraction(got, want, seams_x=xs, seams_y=ys, tol=3) < 0.03


def test_rasterize_effective_dpi_is_returned_and_tagged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from a0td.render import color

    src = tmp_path / "p.pdf"
    d = pymupdf.open()
    pg = d.new_page(width=720, height=360)  # 10 x 5 in
    pg.draw_rect(pymupdf.Rect(0, 0, 72, 72), fill=(0, 0, 0))
    d.save(src)
    d.close()
    monkeypatch.setattr(color, "MAX_RASTER_PIXELS", 50 * 50 * 50)  # 10x5 in -> 50 dpi
    monkeypatch.setattr(color, "BAND_ROWS", 37)  # many bands, odd height
    im, eff = color._rasterize_page(src, 0, 300, True, tmp_path / "raw")
    assert eff == pytest.approx(50.0, rel=1e-6)
    assert (im.width, im.height) == (500, 250)
    arr = np.frombuffer(im.write_to_memory(), np.uint8).reshape(250, 500)
    assert arr[:48, :48].max() < 20  # the 1 in square is 50 px at 50 dpi
    assert arr[:, 53:].min() == 255
    assert arr[53:, :].min() == 255


def test_low_cap_keeps_exact_geometry(
    monkeypatch: pytest.MonkeyPatch, render: Render, rfx_ingest: Callable[[str], Path]
) -> None:
    from a0td.render import color

    monkeypatch.setattr(color, "MAX_RASTER_PIXELS", 15_500_000)  # A0 -> ~100 dpi instead of 150
    _reassembly_matches(
        render, rfx_ingest, "a0_vector_grid.pdf", output={"rasterize": True, "dpi": 150}
    )


@pytest.mark.parametrize(
    ("name", "rotate"),
    [("a0_vector_grid.pdf", 0), ("boxes.pdf", 270), ("rotated_90.pdf", 0)],
)
def test_bw_raster_path_matches_vector_geometry(
    render: Render, rfx_ingest: Callable[[str], Path], name: str, rotate: int
) -> None:
    _reassembly_matches(
        render, rfx_ingest, name, rotate, color={"mode": "bw", "threshold": 200},
        output={"dpi": 150},
    )  # fmt: skip


def test_gray_cache_sidecar_invalidates_and_leaves_no_partials(tmp_path: Path) -> None:
    from a0td.render.color import GRAY_META_NAME, grayscale_pdf

    src = tmp_path / "c.pdf"
    d = pymupdf.open()
    d.new_page(width=200, height=200).draw_rect(pymupdf.Rect(10, 10, 50, 50), fill=(1, 0, 0))
    d.save(src)
    d.close()
    cache = tmp_path / "cache"
    out = grayscale_pdf(src, cache, tmp_path / "s")
    stamp = out.stat().st_mtime_ns
    assert grayscale_pdf(src, cache, tmp_path / "s").stat().st_mtime_ns == stamp  # reused
    d = pymupdf.open(src)
    d[0].draw_rect(pymupdf.Rect(60, 60, 90, 90), fill=(0, 1, 0))
    d.save(tmp_path / "c2.pdf")
    d.close()
    (tmp_path / "c2.pdf").replace(src)  # same name, different content
    grayscale_pdf(src, cache, tmp_path / "s")
    assert out.stat().st_mtime_ns != stamp  # regenerated
    assert sorted(p.name for p in cache.iterdir()) == sorted([GRAY_CACHE_NAME, GRAY_META_NAME])


def test_raster_page_without_resolution_is_an_error(tmp_path: Path) -> None:
    from a0td.domain.errors import DomainError
    from a0td.ingest import Page
    from a0td.render.color import _page_ppm

    page = Page.model_construct(index=0, width_mm=100.0, height_mm=50.0, dpi=None, px=None)
    with pytest.raises(DomainError):
        _page_ppm(page)


def test_one_bit_source_stays_one_bit_in_bw(tmp_path: Path) -> None:
    from a0td.ingest import Page
    from a0td.render.color import _prepare_raster_page

    arr = np.full((64, 64), 255, np.uint8)
    arr[8:20, 8:20] = 0
    src = tmp_path / "bi.tif"
    _vips(arr).tiffsave(str(src), bitdepth=1, compression="ccittfax4")
    page = Page.model_construct(index=0, width_mm=64.0, height_mm=64.0, dpi=25.4, px=None)
    dst = tmp_path / "o.tif"
    _prepare_raster_page(src, page, make_settings(color={"mode": "bw"}), dst)
    out = pyvips.Image.new_from_file(str(dst))
    assert out.bands == 1
    assert out.get("bits-per-sample") == 1
    assert out.xres == pytest.approx(1.0)


def test_palette_source_is_converted_to_srgb(tmp_path: Path) -> None:
    from PIL import Image

    from a0td.ingest import Page
    from a0td.render.color import _prepare_raster_page

    pal = Image.new("P", (16, 16), 0)
    pal.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    pal.paste(1, (8, 0, 16, 16))
    src = tmp_path / "pal.tif"
    pal.save(src)
    page = Page.model_construct(index=0, width_mm=16.0, height_mm=16.0, dpi=25.4, px=None)
    dst = tmp_path / "o.tif"
    _prepare_raster_page(src, page, make_settings(color={"mode": "color", "invert": True}), dst)
    out = _np(pyvips.Image.new_from_file(str(dst)))
    assert out.shape[2] == 3
    assert out[0, 0].tolist() == [0, 255, 255]  # red inverted
    assert out[0, 15].tolist() == [255, 255, 0]  # blue inverted
