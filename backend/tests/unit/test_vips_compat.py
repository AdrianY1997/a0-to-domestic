"""The Pillow + numpy stand-in for pyvips (``a0td.imaging.vips_compat``) against the real thing."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from a0td.domain.settings import ColorSettings
from a0td.imaging import backend_name, use_backend, vips, vips_compat
from a0td.render.color import apply_color, whiten

pyvips = pytest.importorskip("pyvips")

_NP = {"uchar": np.uint8, "ushort": np.uint16, "float": np.float32}


def _both(arr: np.ndarray) -> tuple[Any, Any]:
    h, w = arr.shape[:2]
    bands = 1 if arr.ndim == 2 else arr.shape[2]
    fmt = "uchar" if arr.dtype == np.uint8 else "ushort"
    data = arr.tobytes()
    return (
        pyvips.Image.new_from_memory(data, w, h, bands, fmt),
        vips_compat.Image.new_from_memory(data, w, h, bands, fmt),
    )


def _np(im: Any) -> np.ndarray:
    dtype = _NP[im.format]
    return np.frombuffer(im.write_to_memory(), dtype).reshape(im.height, im.width, im.bands)


def _same(a: Any, b: Any, tol: float = 0) -> None:
    x, y = _np(a).astype(np.float64), _np(b).astype(np.float64)
    assert x.shape == y.shape
    assert np.abs(x - y).max() <= tol


@pytest.fixture
def rgb() -> np.ndarray:
    return np.random.default_rng(7).integers(0, 256, (60, 80, 3), dtype=np.uint8)


def test_backend_selection_is_switchable() -> None:
    assert backend_name() in {"pyvips", "pillow"}
    with use_backend("pillow"):
        assert backend_name() == "pillow"
    with use_backend("pyvips"):
        assert backend_name() == "pyvips"


def test_grey_conversion_matches_libvips(rgb: np.ndarray) -> None:
    v, p = _both(rgb)
    _same(v.colourspace("b-w"), p.colourspace("b-w"), tol=1)
    _same(v.extract_band(0, n=3).colourspace("b-w").cast("uchar"), p.colourspace("b-w"), tol=1)


def test_flatten_matches_libvips() -> None:
    rgba = np.random.default_rng(1).integers(0, 256, (30, 40, 4), dtype=np.uint8)
    la = np.random.default_rng(2).integers(0, 256, (30, 40, 2), dtype=np.uint8)
    v, p = _both(rgba)
    _same(v.flatten(background=[255, 255, 255]), p.flatten(background=[255, 255, 255]), tol=1)
    v, p = _both(la)
    _same(v.flatten(background=[255]), p.flatten(background=[255]), tol=1)


def test_linear_cast_truncates_and_saturates() -> None:
    arr = np.array([[0, 10, 11, 12, 200, 255]], np.uint8)
    v, p = _both(arr)
    _same(v.linear(1.05, 0).cast("uchar"), p.linear(1.05, 0).cast("uchar"))
    _same(v.linear(1.5, 3).cast("uchar"), p.linear(1.5, 3).cast("uchar"))
    wide = np.array([[0, 257, 300, 65535]], np.uint16)
    v, p = _both(wide)
    _same((v / 257.0).rint().cast("uchar"), (p / 257.0).rint().cast("uchar"))


def test_conv_comparisons_and_ifthenelse_match(rgb: np.ndarray) -> None:
    grey = rgb[:, :, 0] > 200
    v, p = _both((grey * 255).astype(np.uint8))
    k = [[1, 1, 1], [1, 0, 1], [1, 1, 1]]
    vk, pk = pyvips.Image.new_from_array(k), vips_compat.Image.new_from_array(k)
    nv, npl = v.conv(vk), p.conv(pk)
    _same(nv, npl)  # both replicate the edges
    _same(v & (nv == 0), p & (npl == 0))
    _same(v >= 255, p >= 255)
    v3, p3 = _both(rgb)
    _same((v < 128).ifthenelse(255, v3), (p < 128).ifthenelse(255, p3))


def test_geometry_operations_match(rgb: np.ndarray) -> None:
    v, p = _both(rgb)
    _same(v.extract_area(5, 7, 30, 20), p.extract_area(5, 7, 30, 20))
    for angle in ("d90", "d180", "d270"):
        _same(v.rot(angle), p.rot(angle))
    _same(v.fliphor(), p.fliphor())
    _same(v.extract_band(1), p.extract_band(1))
    for kernel in ("nearest", "linear", "lanczos3"):
        rv = v.resize(0.37, vscale=0.5, kernel=kernel)
        rp = p.resize(0.37, vscale=0.5, kernel=kernel)
        assert (rv.width, rv.height) == (rp.width, rp.height)
        if kernel != "nearest":  # random noise: only the overall level is comparable
            assert np.abs(_np(rv).astype(int) - _np(rp).astype(int)).mean() < 40


def test_tiff_roundtrip_keeps_size_bands_resolution_and_bilevel(tmp_path: Path) -> None:
    rgb = np.random.default_rng(3).integers(0, 256, (40, 50, 3), dtype=np.uint8)
    _, p = _both(rgb)
    path = tmp_path / "c.tif"
    p.copy(xres=2.0, yres=2.0).tiffsave(
        str(path), tile=True, tile_width=256, compression="deflate", predictor="horizontal"
    )
    back = pyvips.Image.new_from_file(str(path))
    assert (back.width, back.height, back.bands) == (50, 40, 3)
    assert back.xres == pytest.approx(2.0)
    assert np.array_equal(_np(back), rgb)
    bw = (np.random.default_rng(4).random((33, 47)) > 0.5).astype(np.uint8) * 255
    _, pb = _both(bw)
    path = tmp_path / "b.tif"
    pb.tiffsave(str(path), bitdepth=1, compression="deflate")
    back = pyvips.Image.new_from_file(str(path))
    assert (back.bands, back.get("bits-per-sample")) == (1, 1)
    assert np.array_equal(_np(back)[:, :, 0], bw)
    again = vips_compat.Image.new_from_file(str(path))
    assert again.get("bits-per-sample") == 1
    assert np.array_equal(_np(again)[:, :, 0], bw)


def test_pixel_operations_agree_between_backends(rgb: np.ndarray) -> None:
    paper = np.full((200, 200), 215, np.uint8)
    paper[50, 20:180] = 20
    paper[120, 120] = 10  # dust
    cases = (
        (paper, whiten),
        (rgb, lambda im: apply_color(im, ColorSettings(mode="bw", threshold=130))),
        (rgb, lambda im: apply_color(im, ColorSettings(mode="grayscale", invert=True))),
    )
    results: dict[str, list[np.ndarray]] = {}
    for name in ("pyvips", "pillow"):
        with use_backend(name):
            outs = []
            for arr, fn in cases:
                h, w = arr.shape[:2]
                bands = 1 if arr.ndim == 2 else arr.shape[2]
                im = vips.Image.new_from_memory(arr.tobytes(), w, h, bands, "uchar")
                outs.append(_np(fn(im)))
            results[name] = outs
    whitened, bw, grey = zip(results["pyvips"], results["pillow"], strict=True)
    assert whitened[0].shape == whitened[1].shape
    assert np.abs(whitened[0].astype(int) - whitened[1].astype(int)).max() <= 1
    assert np.abs(grey[0].astype(int) - grey[1].astype(int)).max() <= 1
    # a threshold turns the 1-level grey rounding difference into the odd flipped pixel
    assert (bw[0] != bw[1]).mean() < 0.02


def test_thumbnail_never_upscales_and_keeps_aspect(tmp_path: Path, rgb: np.ndarray) -> None:
    path = tmp_path / "t.tif"
    _, p = _both(rgb)
    p.tiffsave(str(path), compression="deflate")
    for box in (30, 1000):
        tv = pyvips.Image.thumbnail(str(path), box, height=box, size="down")
        tp = vips_compat.Image.thumbnail(str(path), box, height=box, size="down")
        assert (tv.width, tv.height) == (tp.width, tp.height)


def test_unreadable_files_raise_the_backend_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(vips_compat.Error):
        vips_compat.Image.new_from_file(str(bad))
    with pytest.raises(vips_compat.Error):
        vips_compat.Image.new_from_file(str(tmp_path / "missing.png"))
