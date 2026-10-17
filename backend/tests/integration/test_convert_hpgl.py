"""HPGL / plotter converter: Inkscape's HPGL import (SPEC 7.1, WP-17)."""

from pathlib import Path

import pymupdf
import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.ingest import ingest_document
from a0td.ingest.converters import hpgl as hpgl_mod
from a0td.ingest.converters.base import tool_available
from a0td.ingest.converters.hpgl import (
    HpglConverter,
    decode_pe,
    frame_inches,
    plot_extent_in,
    strip_labels,
)
from a0td.ingest.models import DocFormat
from convert_support import assert_not_blank, assert_size, convert

needs_inkscape = pytest.mark.skipif(not tool_available("inkscape"), reason="Inkscape not installed")
needs_extension = pytest.mark.skipif(
    not (hpgl_mod.EXTENSION_DIR / hpgl_mod.EXTENSION_SCRIPT).is_file(),
    reason="Inkscape HPGL extension not installed",
)


# ------------------------------------------------------------------------- pure helpers


def test_converter_availability_follows_inkscape() -> None:
    assert HpglConverter().available() is tool_available("inkscape")


def test_plot_extent_follows_absolute_and_relative_moves() -> None:
    assert plot_extent_in("IN;PU0,0;PD1016,2032;") == pytest.approx((1.0, 2.0))
    # PR moves relative to the current point; PU / PD keep the mode of the last PA / PR.
    assert plot_extent_in("IN;PA1016,0;PR;PD1016,1016;") == pytest.approx((2.0, 1.0))
    assert plot_extent_in("IN;PA0,0;PU500,500;PA;PD2032,500;") == pytest.approx((2.0, 500 / 1016))


def test_plot_extent_counts_circles_and_ignores_empty_plots() -> None:
    assert plot_extent_in("PA1016,1016;CI1016;") == pytest.approx((2.0, 2.0))
    assert plot_extent_in("IN;SP1;") is None


def _pe_number(n: int) -> str:
    raw = (-n << 1 | 1) if n < 0 else n << 1
    out = ""
    while raw >= 64:
        out += chr(63 + (raw & 63))
        raw >>= 6
    return out + chr(191 + raw)


def test_plot_extent_follows_pe_polylines() -> None:
    pe = "PE=" + _pe_number(1016) + _pe_number(1016) + _pe_number(1016) + _pe_number(2032) + ";"
    assert plot_extent_in("IN;PU;" + pe) == pytest.approx((2.0, 3.0))
    assert decode_pe(_pe_number(-5) + _pe_number(7), 10, 10)[0] == [(5.0, 17.0)]


def test_plot_extent_maps_user_units_onto_the_ip_box() -> None:
    # 10 x 10 user units on a 2032 x 1016 plu box (2 x 1 in); the point (5, 5) is its centre
    assert plot_extent_in("IN;IP0,0,2032,1016;SC0,10,0,10;PA5,5;") == pytest.approx((1.0, 0.5))
    assert plot_extent_in("IN;IP0,0,2032,1016;SC0,10,0,10;PA10,10;") == pytest.approx((2.0, 1.0))
    # IP / SC off again (IN): plain plotter units
    assert plot_extent_in("IN;IP0,0,2032,1016;SC0,10,0,10;IN;PA1016,1016;") == pytest.approx(
        (1.0, 1.0)
    )


def test_frame_adds_a_margin_and_has_a_minimum() -> None:
    assert frame_inches("PA1016,2032;", 10000) == pytest.approx((1.1, 2.1))
    assert frame_inches("PA10,10;", 10000) == pytest.approx((1.0, 1.0))
    assert frame_inches("IN;", 10000) == (8.0, 10.0)  # the extension's default


def test_frame_over_the_page_limit_is_refused() -> None:
    with pytest.raises(DomainError) as exc:
        frame_inches("PA1016000,0;", 10000)
    assert exc.value.code is ErrorCode.PAGE_TOO_LARGE


def test_strip_labels_removes_text_and_escape_sequences() -> None:
    text = "IN;PU0,0;LBHello\x03;PD10,10;\x1b%0B"
    assert strip_labels(text) == "IN;PU0,0;;PD10,10;"


# ----------------------------------------------------------------------- real conversion


@needs_inkscape
@needs_extension
def test_hpgl_plot_is_sized_from_its_pen_coordinates(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    result, work = convert(fixtures_dir, "hpgl_plot.plt", tmp_path, cfg)
    assert result.format is DocFormat.HPGL
    assert result.kind == "pdf"
    # 33640 x 23760 plotter units (0.025 mm) = 841 x 594 mm, plus the 0.1 in margin.
    assert_size(result, 843.8, 596.6, tol=2)
    assert_not_blank(work)
    assert result.warnings == []


@needs_inkscape
@needs_extension
def test_hpgl_drawing_is_complete_not_cut_at_the_default_frame(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    result, work = convert(fixtures_dir, "hpgl_plot.plt", tmp_path, cfg)
    with pymupdf.open(work / "canonical.pdf") as pdf:
        page = pdf[0]
        xs = [r.x1 for d in page.get_drawings() for r in [d["rect"]]]
        assert max(xs) > page.rect.width * 0.95  # the frame reaches the right edge of the page
        assert page.rect.width > 8 * 72  # bigger than the extension's 8 x 10 in default
    assert result.pages[0].width_mm > 800


@needs_inkscape
@needs_extension
def test_hpgl_labels_are_reported_as_dropped(tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "labels.hpgl"
    src.write_bytes(b"IN;SP1;PU0,0;PD10160,0,10160,7620,0,7620,0,0;PU1000,1000;LBHELLO\x03;SP0;\n")
    result = ingest_document(src, tmp_path / "doc", config=cfg)
    [warning] = result.warnings
    assert warning.code is WarningCode.LOW_FIDELITY_FORMAT
    assert warning.params == {"format": "hpgl", "reason": "labels_dropped"}
    assert result.pages[0].width_mm == pytest.approx(254 + 2.54, abs=2)


@needs_inkscape
@needs_extension
def test_hpgl_extension_failure_is_a_clear_error(
    tmp_path: Path, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "plot.plt"
    src.write_bytes(b"IN;SP1;PU0,0;PD100,100;\n")
    monkeypatch.setattr(hpgl_mod, "SYSTEM_PYTHON", "/bin/false")
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "doc", config=cfg)
    assert exc.value.code is ErrorCode.CORRUPT_FILE


@needs_inkscape
@needs_extension
def test_small_plot_gets_a_small_page_and_a_big_one_is_not_cut(
    tmp_path: Path, cfg: AppConfig
) -> None:
    small = tmp_path / "small.hpgl"
    small.write_bytes(b"IN;SP1;PU0,0;PD2032,0,2032,1016,0,1016,0,0;\n")  # 2 x 1 in
    res = ingest_document(small, tmp_path / "small", config=cfg)
    assert res.pages[0].width_mm == pytest.approx(25.4 * 2.1, abs=1)
    assert res.pages[0].height_mm == pytest.approx(25.4 * 1.1, abs=1)
    big = tmp_path / "big.hpgl"  # 20 x 12 in, beyond the extension's 8 x 10 in default
    big.write_bytes(b"IN;SP1;PU0,0;PD20320,0,20320,12192,0,12192,0,0;\n")
    res = ingest_document(big, tmp_path / "big", config=cfg)
    assert res.pages[0].width_mm == pytest.approx(25.4 * 20.1, abs=1)
    assert res.pages[0].height_mm == pytest.approx(25.4 * 12.1, abs=1)
