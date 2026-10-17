"""CAD converters: DXF (ezdxf, PyMuPDF backend) and DWG (LibreDWG / ODA) (SPEC 7.1, WP-17)."""

import contextlib
import os
import stat
import time
from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.ingest import CadColor, CadOptions, IngestResult, default_registry, ingest_document
from a0td.ingest.converters import cad as cad_mod
from a0td.ingest.converters.base import ConvertContext, tool_available
from a0td.ingest.converters.cad import (
    DwgConverter,
    DxfConverter,
    choose_scale,
    deadline,
    normalize_unit,
)
from a0td.ingest.models import DocFormat
from convert_support import assert_not_blank, assert_size, convert, fixture_path

needs_dwg = pytest.mark.skipif(not tool_available("dwg2dxf"), reason="LibreDWG not installed")

PLAN = "dxf_model_plan.dxf"
LAYOUT = "dxf_a0_layout.dxf"
DWG = "dwg_model_plan.dwg"


def _ingest(
    fixtures_dir: Path, name: str, tmp_path: Path, cfg: AppConfig, **opts: object
) -> tuple[IngestResult, Path]:
    work = tmp_path / "doc"
    result = ingest_document(
        fixture_path(fixtures_dir, name),
        work,
        config=cfg,
        cad_options=CadOptions(**opts),  # type: ignore[arg-type]
    )
    return result, work


def _stroke_colors(work: Path) -> set[tuple[float, ...]]:
    with pymupdf.open(work / "canonical.pdf") as pdf:
        return {
            tuple(round(c, 2) for c in d["color"])
            for d in pdf[0].get_drawings()
            if d.get("color") is not None
        }


# --------------------------------------------------------------------------- registry


def test_dxf_is_available_without_external_tools() -> None:
    assert DxfConverter().available()
    reg = default_registry()
    assert reg.resolve(DocFormat.DXF).id == "dxf"
    assert DocFormat.DXF in reg.available_formats()


def test_dwg_availability_follows_libredwg(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DwgConverter().available() is tool_available("dwg2dxf")
    monkeypatch.setattr(cad_mod, "tool_available", lambda name: False)
    assert not DwgConverter().available()


# ------------------------------------------------------------------------ paper space


def test_dxf_paper_layout_keeps_the_layout_page_size(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    result, work = convert(fixtures_dir, LAYOUT, tmp_path, cfg)
    assert result.format is DocFormat.DXF
    assert result.kind == "pdf"
    assert_size(result, 1189, 841, tol=0.05)
    assert result.pages[0].standard_match is not None
    assert result.pages[0].standard_match.id == "A0"
    assert_not_blank(work)
    cad = result.cad
    assert cad is not None
    assert cad.layout == "A0 sheet"
    assert cad.scale is None
    assert [(x.name, x.kind) for x in cad.layouts] == [("Model", "model"), ("A0 sheet", "paper")]
    assert (cad.layouts[1].width_mm, cad.layouts[1].height_mm) == (1189.0, 841.0)
    assert result.pages[0].has_text or result.pages[0].kind.value in ("vector", "mixed")


def test_cad_info_is_saved_in_meta(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    _, work = convert(fixtures_dir, LAYOUT, tmp_path, cfg)
    loaded = IngestResult.load(work)
    assert loaded.cad is not None
    assert loaded.cad.layout == "A0 sheet"


def test_model_space_can_be_chosen_in_a_file_with_a_sheet(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    result, _ = _ingest(fixtures_dir, LAYOUT, tmp_path, cfg, layout="model", scale=100)
    assert result.cad is not None
    assert result.cad.layout == "Model"
    assert result.cad.scale == 100
    assert result.pages[0].width_mm == pytest.approx(12000 / 100 + 10, abs=2)


def test_unknown_layout_is_rejected(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    with pytest.raises(DomainError) as exc:
        _ingest(fixtures_dir, LAYOUT, tmp_path, cfg, layout="Nope")
    assert exc.value.code is ErrorCode.INVALID_SETTINGS
    assert exc.value.params["layouts"] == ["Model", "A0 sheet"]


# ------------------------------------------------------------------------- model space


def test_model_space_gets_an_automatic_standard_scale(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    result, work = convert(fixtures_dir, PLAN, tmp_path, cfg)
    cad = result.cad
    assert cad is not None
    assert cad.layout == "Model"
    assert cad.units == "mm"
    assert cad.units_source == "insunits"
    assert cad.scale == 20
    assert cad.scale_auto
    assert result.pages[0].width_mm == pytest.approx(12000 / 20 + 10, abs=3)
    assert max(result.pages[0].width_mm, result.pages[0].height_mm) <= 1189
    assert not result.pages[0].has_warning(WarningCode.PHYSICAL_SIZE_ASSUMED)
    assert_not_blank(work)


def test_model_space_scale_one_to_fifty(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    result, _ = _ingest(fixtures_dir, PLAN, tmp_path, cfg, scale=50)
    assert result.cad is not None
    assert result.cad.scale == 50
    assert not result.cad.scale_auto
    assert result.pages[0].width_mm == pytest.approx(12000 / 50 + 10, abs=2)


def test_units_override_changes_the_size_by_the_unit_ratio(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    # The same numbers read as centimetres are 10 times larger on paper.
    result, _ = _ingest(fixtures_dir, PLAN, tmp_path, cfg, units="cm", scale=200)
    assert result.cad is not None
    assert result.cad.units == "cm"
    assert result.cad.units_source == "user"
    assert result.pages[0].width_mm == pytest.approx(12000 * 10 / 200 + 10, abs=3)


def test_scale_that_makes_the_page_too_large_is_refused(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    with pytest.raises(DomainError) as exc:
        _ingest(fixtures_dir, PLAN, tmp_path, cfg, scale=0.5)  # 24 m wide
    assert exc.value.code is ErrorCode.PAGE_TOO_LARGE


def test_unknown_units_are_rejected_by_the_options_model() -> None:
    with pytest.raises(ValueError, match="parsec"):
        CadOptions(units="parsec")
    assert CadOptions(units="Inches").units == "in"


# ------------------------------------------------------------------------- colour policy


def test_color_policy_color_keeps_the_drawing_colours(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    _, work = _ingest(fixtures_dir, PLAN, tmp_path, cfg, color="color")
    colors = _stroke_colors(work)
    assert (1.0, 0.0, 0.0) in colors  # the red walls
    assert len(colors) > 2


def test_color_policy_black_draws_everything_black(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    result, work = _ingest(fixtures_dir, PLAN, tmp_path, cfg, color="black")
    assert _stroke_colors(work) == {(0.0, 0.0, 0.0)}
    assert result.cad is not None
    assert result.cad.color is CadColor.BLACK
    assert_not_blank(work)


def test_color_policy_gray_has_no_hue(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    _, work = _ingest(fixtures_dir, PLAN, tmp_path, cfg, color="gray")
    colors = _stroke_colors(work)
    assert colors
    assert all(len(c) == 1 or len(set(c)) == 1 for c in colors), colors


def test_lineweights_are_drawn(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    _, work = _ingest(fixtures_dir, PLAN, tmp_path, cfg)
    with pymupdf.open(work / "canonical.pdf") as pdf:
        widths = {round(d["width"], 2) for d in pdf[0].get_drawings() if d.get("width")}
    assert len(widths) >= 2, widths


# ----------------------------------------------------------------------- helpers, limits


def test_choose_scale_picks_the_smallest_standard_scale_that_fits_a0() -> None:
    assert choose_scale(500, 300, 10000) == 1
    assert choose_scale(12000, 8000, 10000) == 20
    assert (
        choose_scale(1e9, 1e9, 10000) == 10000
    )  # nothing fits: the largest, and the caller errors


def test_normalize_unit_accepts_names_and_aliases() -> None:
    assert normalize_unit(" Inches ") == "in"
    assert normalize_unit("MM") == "mm"
    with pytest.raises(DomainError):
        normalize_unit("furlong")


@pytest.mark.skipif(not hasattr(__import__("signal"), "setitimer"), reason="needs setitimer")
def test_deadline_raises_conversion_timeout() -> None:
    start = time.monotonic()
    with pytest.raises(DomainError) as exc, deadline(0.2):
        time.sleep(5)
    assert exc.value.code is ErrorCode.CONVERSION_TIMEOUT
    assert time.monotonic() - start < 3
    with deadline(5):  # a block that finishes in time is left alone
        pass


@pytest.mark.skipif(not hasattr(__import__("signal"), "setitimer"), reason="needs setitimer")
def test_deadline_still_fires_when_the_renderer_swallows_the_exception() -> None:

    def swallowing_renderer() -> None:
        with contextlib.suppress(Exception):  # what ezdxf does around a failing entity
            time.sleep(5)

    with pytest.raises(DomainError) as exc, deadline(0.2):
        swallowing_renderer()
    assert exc.value.code is ErrorCode.CONVERSION_TIMEOUT


def test_images_named_in_a_drawing_are_never_opened(tmp_path: Path, cfg: AppConfig) -> None:
    import ezdxf
    from PIL import Image

    secret = tmp_path / "secret.png"
    Image.new("RGB", (20, 20), (255, 0, 0)).save(secret)
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (500, 500))
    image_def = doc.add_image_def(filename=str(secret), size_in_pixel=(20, 20))
    msp.add_image(image_def, insert=(0, 0), size_in_units=(400, 400), rotation=0)
    src = tmp_path / "image.dxf"
    doc.saveas(src)
    result = ingest_document(src, tmp_path / "doc", config=cfg)
    with pymupdf.open(tmp_path / "doc" / "canonical.pdf") as pdf:
        assert pdf[0].get_images() == []
    assert result.pages[0].kind.value == "vector"


def test_empty_drawing_is_a_corrupt_file(tmp_path: Path, cfg: AppConfig) -> None:
    import ezdxf

    src = tmp_path / "empty.dxf"
    ezdxf.new("R2010").saveas(src)
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "doc", config=cfg)
    assert exc.value.code is ErrorCode.CORRUPT_FILE


def test_damaged_dxf_does_not_crash(tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "broken.dxf"
    src.write_bytes(b"  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  8\n0\n 10\nnot-a-number\n")
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "doc", config=cfg)
    assert exc.value.code in (ErrorCode.CORRUPT_FILE, ErrorCode.CONVERSION_FAILED)


def test_binary_dxf_is_read(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    import ezdxf

    src = tmp_path / "binary.dxf"
    ezdxf.readfile(fixture_path(fixtures_dir, PLAN)).saveas(src, fmt="bin")
    assert src.read_bytes().startswith(b"AutoCAD Binary DXF")
    result = ingest_document(src, tmp_path / "doc", config=cfg)
    assert result.format is DocFormat.DXF
    assert result.cad is not None


# ----------------------------------------------------------------------------------- DWG


@needs_dwg
def test_dwg_goes_through_libredwg_to_the_dxf_path(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    result, work = convert(fixtures_dir, DWG, tmp_path, cfg)
    assert result.format is DocFormat.DWG
    assert result.mime == "image/vnd.dwg"
    assert result.cad is not None
    assert result.cad.layout == "Model"
    assert result.pages[0].width_mm == pytest.approx(12000 / 20 + 10, abs=3)
    assert_not_blank(work)
    # LibreDWG's DXF of this file is unusable as is: the entities-only retry says so.
    codes = [w.code for w in result.warnings]
    codes = [c for c in codes if c is not WarningCode.PHYSICAL_SIZE_ASSUMED]  # unitless fixture
    assert codes in ([], [WarningCode.LOW_FIDELITY_FORMAT])


def test_old_dwg_version_is_reported_clearly(tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "old.dwg"
    src.write_bytes(b"AC1009" + b"\x00" * 200)
    ctx = ConvertContext(config=cfg, format=DocFormat.DWG, scratch=tmp_path / "s")
    with pytest.raises(DomainError) as exc:
        DwgConverter().convert(src, tmp_path / "work", ctx)
    assert exc.value.code is ErrorCode.CONVERSION_FAILED
    assert exc.value.params == {"reason": "dwg_version_unsupported", "version": "AC1009"}
    assert "R11/R12" in exc.value.detail


@needs_dwg
def test_unreadable_dwg_reports_the_version(tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "junk.dwg"
    src.write_bytes(b"AC1027" + os.urandom(2048))
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "doc", config=cfg)
    assert exc.value.code is ErrorCode.CONVERSION_FAILED
    assert exc.value.params["version"] == "AC1027"


@pytest.mark.skipif(os.name != "posix", reason="needs a POSIX shell")
def test_oda_converter_is_used_when_configured(
    fixtures_dir: Path,
    tmp_path: Path,
    make_cfg: Callable[..., AppConfig],
) -> None:
    """A stand-in for the ODA File Converter: ``oda <in dir> <out dir> <version> DXF 0 1``."""
    dxf = fixture_path(fixtures_dir, LAYOUT)
    fake = tmp_path / "ODAFileConverter"
    fake.write_text(f'#!/bin/sh\ncp "{dxf}" "$2/drawing.dxf"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    cfg = make_cfg(oda_converter=fake)
    src = tmp_path / "in.dwg"
    src.write_bytes(b"AC1032" + b"\x00" * 300)
    ctx = ConvertContext(config=cfg, format=DocFormat.DWG, scratch=tmp_path / "s")
    (tmp_path / "work").mkdir()
    canon = DwgConverter().convert(src, tmp_path / "work", ctx)
    assert canon.source_format is DocFormat.DWG
    assert canon.cad is not None
    assert canon.cad.layout == "A0 sheet"
    assert not canon.warnings


def test_oda_needs_to_be_executable(tmp_path: Path, make_cfg: Callable[..., AppConfig]) -> None:
    not_exec = tmp_path / "oda"
    not_exec.write_text("#!/bin/sh\n")
    not_exec.chmod(0o600)
    cfg = make_cfg(oda_converter=not_exec)
    assert DwgConverter._oda(ConvertContext(config=cfg)) is None


def _line_dxf(path: Path, *, insunits: int | None = 4) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    if insunits is not None:
        doc.header["$INSUNITS"] = insunits
    doc.modelspace().add_line((0, 0), (1000, 500))
    doc.saveas(path)


def test_unitless_drawing_warns_that_the_size_is_assumed(tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "unitless.dxf"
    _line_dxf(src, insunits=0)
    result = ingest_document(src, tmp_path / "doc", config=cfg)
    assert result.cad is not None
    assert result.cad.units_source == "assumed"
    [w] = [w for w in result.warnings if w.code is WarningCode.PHYSICAL_SIZE_ASSUMED]
    assert w.params == {"reason": "cad_units"}
    known = tmp_path / "mm.dxf"
    _line_dxf(known)
    again = ingest_document(known, tmp_path / "doc2", config=cfg)
    assert not any(w.code is WarningCode.PHYSICAL_SIZE_ASSUMED for w in again.warnings)


def test_entity_cap_refuses_a_too_complex_drawing(
    tmp_path: Path, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "many.dxf"
    _line_dxf(src)
    monkeypatch.setenv("A0TD_CAD_MAX_ENTITIES", "0")  # not positive: default applies
    ingest_document(src, tmp_path / "ok", config=cfg)
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for i in range(50):
        msp.add_line((i, 0), (i, 10))
    doc.saveas(src)
    monkeypatch.setenv("A0TD_CAD_MAX_ENTITIES", "10")
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "doc", config=cfg)
    assert exc.value.code is ErrorCode.PIXEL_LIMIT_EXCEEDED


def test_insert_arrays_count_as_all_their_copies(tmp_path: Path, cfg: AppConfig) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    block = doc.blocks.new("B")
    block.add_line((0, 0), (1, 1))
    doc.modelspace().add_blockref("B", (0, 0)).dxf.row_count = 100
    assert cad_mod.count_entities(doc, doc.modelspace(), 10_000) == 101
    with pytest.raises(DomainError) as exc:
        cad_mod.count_entities(doc, doc.modelspace(), 50)
    assert exc.value.code is ErrorCode.PIXEL_LIMIT_EXCEEDED


def test_self_referencing_and_deeply_nested_blocks_are_refused() -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    loop = doc.blocks.new("LOOP")
    loop.add_blockref("LOOP", (0, 0))
    doc.modelspace().add_blockref("LOOP", (0, 0))
    with pytest.raises(DomainError) as exc:
        cad_mod.count_entities(doc, doc.modelspace(), 1_000_000)
    assert exc.value.params["reason"] == "insert_cycle"
    deep = ezdxf.new("R2010")
    deep.blocks.new("N0").add_line((0, 0), (1, 1))
    for i in range(1, cad_mod.MAX_INSERT_DEPTH + 3):
        deep.blocks.new(f"N{i}").add_blockref(f"N{i - 1}", (0, 0))
    deep.modelspace().add_blockref(f"N{cad_mod.MAX_INSERT_DEPTH + 2}", (0, 0))
    with pytest.raises(DomainError) as exc:
        cad_mod.count_entities(deep, deep.modelspace(), 1_000_000)
    assert exc.value.params["reason"] == "insert_depth"


def test_extents_ignore_off_and_frozen_layers(tmp_path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.layers.add("far").off()
    doc.layers.add("frozen").freeze()
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 10))
    msp.add_line((0, 0), (5000, 5000), dxfattribs={"layer": "far"})
    msp.add_line((0, 0), (9000, 9000), dxfattribs={"layer": "frozen"})
    assert cad_mod._extents_mm(doc, msp, 1.0) == pytest.approx((10, 10))


def test_model_space_distances_and_page_are_exact(tmp_path: Path, cfg: AppConfig) -> None:
    import ezdxf

    src = tmp_path / "ruler.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0))
    msp.add_line((0, 0), (0, 600))
    doc.saveas(src)
    result = ingest_document(src, tmp_path / "doc", config=cfg, cad_options=CadOptions(scale=50))
    assert_size(result, 20 + 10, 12 + 10, tol=0.05)  # 1000 x 600 mm at 1:50 plus 5 mm margins
    with pymupdf.open(tmp_path / "doc" / "canonical.pdf") as pdf:
        xs = [
            pt.x
            for d in pdf[0].get_drawings()
            for it in d["items"]
            if it[0] == "l"
            for pt in (it[1], it[2])
        ]
        ys = [
            pt.y
            for d in pdf[0].get_drawings()
            for it in d["items"]
            if it[0] == "l"
            for pt in (it[1], it[2])
        ]
    mm = 25.4 / 72
    assert (max(xs) - min(xs)) * mm == pytest.approx(20.0, abs=0.05)
    assert (max(ys) - min(ys)) * mm == pytest.approx(12.0, abs=0.05)
