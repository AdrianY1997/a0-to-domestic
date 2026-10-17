import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from a0td.cli.main import app
from a0td.domain.settings import BUILTIN_PRESETS

runner = CliRunner()


@pytest.fixture
def grid_pdf(rfx_dir: Path) -> str:
    return str(rfx_dir / "a0_vector_grid.pdf")


def test_formats() -> None:
    res = runner.invoke(app, ["formats"])
    assert res.exit_code == 0
    assert "pdf" in res.output


def test_info_json(grid_pdf: str) -> None:
    res = runner.invoke(app, ["info", grid_pdf, "--json"])
    assert res.exit_code == 0, res.output
    assert len(json.loads(res.output)["pages"]) == 1


def test_info_table(grid_pdf: str) -> None:
    res = runner.invoke(app, ["info", grid_pdf])
    assert res.exit_code == 0
    assert "A0" in res.output


def test_plan_json(grid_pdf: str) -> None:
    res = runner.invoke(app, ["plan", grid_pdf, "--json", "--page", "1"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)[0]["counts"]["total"] == 21
    assert runner.invoke(app, ["plan", grid_pdf, "--paper", "A3"]).exit_code == 0


def test_plan_bad_args(grid_pdf: str) -> None:
    assert runner.invoke(app, ["plan", grid_pdf, "--page", "9"]).exit_code == 1
    assert runner.invoke(app, ["plan", grid_pdf, "--margins", "1,2"]).exit_code == 1
    assert runner.invoke(app, ["plan", grid_pdf, "--overlap", "1"]).exit_code == 1


def test_plan_settings_file(grid_pdf: str, tmp_path: Path) -> None:
    f = tmp_path / "job.json"
    f.write_text(json.dumps({"target": {"paper": "A3"}}))
    res = runner.invoke(app, ["plan", grid_pdf, "--settings", str(f), "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)[0]["paper"]["id"] == "A3"


def test_tile_22_pages(grid_pdf: str, tmp_path: Path) -> None:
    out = tmp_path / "out"
    res = runner.invoke(app, ["tile", grid_pdf, "-o", str(out)])
    assert res.exit_code == 0, res.output
    pdfs = list(out.glob("*.pdf"))
    assert pdfs
    with pymupdf.open(pdfs[0]) as doc:
        assert doc.page_count == 22


def test_fit(grid_pdf: str, tmp_path: Path) -> None:
    res = runner.invoke(app, ["fit", grid_pdf, "-o", str(tmp_path / "o")])
    assert res.exit_code == 0, res.output
    assert list((tmp_path / "o").glob("*.pdf"))


def test_password_required(rfx_dir: Path) -> None:
    res = runner.invoke(app, ["info", str(rfx_dir / "encrypted.pdf")])
    assert res.exit_code == 1
    assert "PASSWORD_REQUIRED" in res.output


def test_calibration_page(tmp_path: Path) -> None:
    res = runner.invoke(app, ["calibration-page", "--paper", "A4", "-o", str(tmp_path)])
    assert res.exit_code == 0, res.output
    with pymupdf.open(tmp_path / "calibration-A4.pdf") as doc:
        assert doc.page_count == 1
        assert abs(doc[0].rect.width - 595.28) < 0.1


def test_calibration_page_unknown_paper(tmp_path: Path) -> None:
    res = runner.invoke(app, ["calibration-page", "--paper", "ZZ", "-o", str(tmp_path)])
    assert res.exit_code == 1
    assert "INVALID_SETTINGS" in res.output


def _plan(grid_pdf: str, *args: str) -> dict:
    res = runner.invoke(app, ["plan", grid_pdf, "--json", "--page", "1", *args])
    assert res.exit_code == 0, res.output
    return json.loads(res.output)[0]


def test_plan_fit_modes(grid_pdf: str) -> None:
    g = _plan(grid_pdf, "--fit-grid", "2x2")["grid"]
    assert (g["cols"], g["rows"]) == (2, 2)
    assert _plan(grid_pdf, "--fit-count", "4")["counts"]["total"] <= 4
    assert _plan(grid_pdf, "--fit-sheet")["counts"]["total"] == 1


def test_plan_preset_join_skip_blank(grid_pdf: str) -> None:
    assert runner.invoke(app, ["plan", grid_pdf, "--preset", "nope"]).exit_code == 1
    assert runner.invoke(app, ["plan", grid_pdf, "--printer", "nope"]).exit_code == 1
    assert _plan(grid_pdf, "--join", "butt")["overlap_mm"] == 0
    _plan(grid_pdf, "--no-skip-blank")
    _plan(grid_pdf, "--preset", BUILTIN_PRESETS[0].id)


def test_plan_new_flags(grid_pdf: str) -> None:
    _plan(grid_pdf, "--final-size", "600")
    _plan(grid_pdf, "--final-size", "x900")
    _plan(grid_pdf, "--drawing-scale", "50:100")
    _plan(grid_pdf, "--align", "start,end", "--order", "columns", "--naming", "sequential")
    for bad in (
        ["--final-size", "abc"],
        ["--drawing-scale", "50"],
        ["--align", "left,right"],
        ["--order", "diagonal"],
        ["--naming", "zzz"],
        ["--fit-grid", "3"],
        ["--margins", "a,b"],
    ):
        assert runner.invoke(app, ["plan", grid_pdf, *bad]).exit_code == 1, bad


@pytest.mark.parametrize("content", ["[1]", "42", "{not json"])
def test_settings_not_object(grid_pdf: str, tmp_path: Path, content: str) -> None:
    f = tmp_path / "bad.json"
    f.write_text(content)
    res = runner.invoke(app, ["plan", grid_pdf, "--settings", str(f)])
    assert res.exit_code == 1
    assert "INVALID_SETTINGS" in res.output


def test_default_output_dir(grid_pdf: str, tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    src.write_bytes(Path(grid_pdf).read_bytes())
    res = runner.invoke(app, ["fit", str(src)])
    assert res.exit_code == 0, res.output
    assert [p for p in tmp_path.glob("*.pdf") if p.name != "in.pdf"]
