"""CLI flags for DXF / DWG: --cad-layout, --cad-units, --cad-scale, --cad-color (WP-17)."""

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from a0td.cli.main import app

runner = CliRunner()


@pytest.fixture
def plan(rfx_dir: Path) -> str:
    path = rfx_dir / "dxf_model_plan.dxf"
    if not path.exists():
        pytest.skip("dxf_model_plan.dxf was not generated")
    return str(path)


@pytest.fixture
def sheet(rfx_dir: Path) -> str:
    path = rfx_dir / "dxf_a0_layout.dxf"
    if not path.exists():
        pytest.skip("dxf_a0_layout.dxf was not generated")
    return str(path)


def test_info_defaults_to_the_automatic_cad_choices(plan: str) -> None:
    res = runner.invoke(app, ["info", plan, "--json"])
    assert res.exit_code == 0, res.output
    cad = json.loads(res.output)["cad"]
    assert cad["layout"] == "Model"
    assert cad["scale"] == 20
    assert cad["scale_auto"] is True


def test_info_cad_flags(plan: str) -> None:
    res = runner.invoke(
        app,
        [
            "info",
            plan,
            "--json",
            "--cad-layout",
            "model",
            "--cad-scale",
            "50",
            "--cad-color",
            "black",
        ],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["cad"]["scale"] == 50
    assert data["cad"]["color"] == "black"
    assert data["pages"][0]["width_mm"] == pytest.approx(250, abs=2)


def test_info_cad_units_flag(plan: str) -> None:
    res = runner.invoke(app, ["info", plan, "--json", "--cad-units", "cm", "--cad-scale", "200"])
    assert res.exit_code == 0, res.output
    cad = json.loads(res.output)["cad"]
    assert cad["units"] == "cm"
    assert cad["units_source"] == "user"


def test_info_table_lists_the_layouts(sheet: str) -> None:
    res = runner.invoke(app, ["info", sheet])
    assert res.exit_code == 0, res.output
    assert "cad: layout 'A0 sheet'" in res.output
    assert "Model, A0 sheet" in res.output


def test_unknown_layout_is_an_error(sheet: str) -> None:
    res = runner.invoke(app, ["info", sheet, "--cad-layout", "Nope"])
    assert res.exit_code == 1
    assert "INVALID_SETTINGS" in res.output


def test_bad_cad_flag_values(plan: str) -> None:
    assert runner.invoke(app, ["info", plan, "--cad-color", "purple"]).exit_code == 2
    assert runner.invoke(app, ["info", plan, "--cad-scale", "0"]).exit_code == 2
    res = runner.invoke(app, ["info", plan, "--cad-units", "parsec"])
    assert res.exit_code == 1
    assert "INVALID_SETTINGS" in res.output


def test_cad_flags_do_not_matter_for_other_formats(rfx_dir: Path) -> None:
    res = runner.invoke(
        app, ["info", str(rfx_dir / "a0_vector_grid.pdf"), "--cad-scale", "50", "--json"]
    )
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["cad"] is None


def test_plan_accepts_cad_flags(plan: str) -> None:
    res = runner.invoke(app, ["plan", plan, "--json", "--cad-scale", "100", "--paper", "A4"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)[0]["paper"]["id"] == "A4"


def test_tile_renders_a_dxf_at_the_chosen_scale(plan: str, tmp_path: Path) -> None:
    out = tmp_path / "out"
    res = runner.invoke(
        app,
        ["tile", plan, "-o", str(out), "--cad-scale", "50", "--cad-color", "gray", "--json"],
    )
    assert res.exit_code == 0, res.output
    pdfs = [p for p in json.loads(res.output)["outputs"] if p.endswith(".pdf")]
    assert pdfs
    with pymupdf.open(pdfs[0]) as doc:
        assert doc.page_count >= 2  # 250 x 170 mm does not fit one A4 sheet with margins


def test_fit_renders_a_dxf_sheet(sheet: str, tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["fit", sheet, "-o", str(tmp_path / "out"), "--cad-layout", "A0 sheet", "--json"]
    )
    assert res.exit_code == 0, res.output
