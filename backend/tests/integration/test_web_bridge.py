"""The web local mode bridge (SPEC 17bis) in CPython: ingest -> plan -> render per format.

The raster tests run on the Pillow backend (what the browser uses); SVG and grayscale vector pages
run with the external tools hidden so the MuPDF / in-memory paths of the web build are taken.
"""

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from a0td.config import get_config
from a0td.domain.errors import DomainError
from a0td.imaging import use_backend
from a0td.web import bridge

PNG_MAGIC = bytes([0x89]) + b"PNG"
M5 = {"top": 5, "right": 5, "bottom": 5, "left": 5}


@pytest.fixture(scope="module")
def br(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    saved = dict(os.environ)
    os.environ["A0TD_WEB"] = "1"
    bridge.configure(tmp_path_factory.mktemp("a0td-web"))
    try:
        yield bridge
    finally:
        os.environ.clear()
        os.environ.update(saved)
        get_config.cache_clear()
        bridge._state.update(root=None, config=None, registry=None)


@pytest.fixture
def fx(fixtures_dir: Path) -> Path:
    return fixtures_dir


@pytest.fixture
def no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """The browser has no subprocesses: ``shutil.which`` finds nothing."""
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    bridge._state["registry"] = None


def _settings(doc_id: str, **extra: Any) -> str:
    base: dict[str, Any] = {"document_id": doc_id, "target": {"margins_mm": M5}}
    base.update(extra)
    return json.dumps(base)


def _ingest(br: Any, path: Path, name: str | None = None, **kw: Any) -> dict[str, Any]:
    out = json.loads(br.ingest(str(path), name or path.name, **kw))
    assert isinstance(out, dict)
    return out


# ------------------------------------------------------------------------------ catalogs


def test_catalogs_are_json(br: Any) -> None:
    info = json.loads(br.info())
    assert info["local"] is True
    assert info["limits"]["max_dpi"] == 300
    assert info["limits"]["max_megapixels"] <= 250
    assert {c["id"] for c in info["converters"]} >= {"pdf", "raster", "svg", "xps"}
    assert {p["id"] for p in json.loads(br.papers())} >= {"A4", "LETTER", "A3"}
    assert "builtin-generic" in {p["id"] for p in json.loads(br.printers())}
    assert any(p["id"].startswith("builtin-") for p in json.loads(br.presets()))
    assert br.calibration_pdf("A4", "en")[:5] == b"%PDF-"


def test_subprocess_converters_report_unavailable_without_crashing(br: Any, no_tools: None) -> None:
    by_id = {c["id"]: c for c in json.loads(br.info())["converters"]}
    assert by_id["office"]["available"] is False
    assert by_id["postscript"]["available"] is False
    assert by_id["pdf"]["available"] is True
    assert by_id["svg"]["available"] is True  # MuPDF in the web build


def test_unavailable_format_is_a_failed_document(br: Any, no_tools: None, fx: Path) -> None:
    doc = _ingest(br, fx / "docx_simple.docx")
    assert doc["status"] == "failed"
    assert doc["error_code"] == "FORMAT_NOT_AVAILABLE_IN_IMAGE"
    assert doc["error_params"]["format"] == "docx"
    assert not (Path(bridge._root()) / "documents" / doc["id"]).exists()


# --------------------------------------------------------------------- vector end to end


def test_vector_ingest_plan_render(br: Any, fx: Path) -> None:
    events: list[tuple[str, float, str | None, dict[str, Any]]] = []
    doc = _ingest(br, fx / "a0_vector_grid.pdf", progress=lambda *a: events.append(a))
    assert doc["status"] == "ready"
    assert (doc["format"], doc["page_count"]) == ("pdf", 1)
    assert doc["pages"][0]["width_mm"] == pytest.approx(841, abs=1)
    assert {e[0] for e in events} >= {"detect", "done"}
    assert [e[1] for e in events] == sorted(e[1] for e in events)

    settings = _settings(doc["id"])
    plan = json.loads(br.plan(settings, 0, True, None))
    assert (plan["grid"]["cols"], plan["grid"]["rows"]) == (3, 7)
    assert plan["counts"]["printed"] == 21
    assert plan["alternatives"]

    png = br.sheet_preview(settings, 0, "A1", 400)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert br.page_preview(doc["id"], 0, 300)[:4] == b"\x89PNG"

    stages: list[float] = []
    res = json.loads(br.render(settings, None, False, lambda s, p, k, params: stages.append(p)))
    assert stages
    assert stages[-1] >= 99
    (out,) = res["outputs"]
    assert out["kind"] == "pdf"
    assert out["pages"] == 22
    assert Path(out["path"]).parent == Path(bridge._root()) / "jobs" / res["job_id"] / "output"
    assert Path(out["path"]).stat().st_size == out["size_bytes"]
    assert res["warnings"] == []
    with pymupdf.open(out["path"]) as pdf:
        assert pdf.page_count == 22
        assert {(round(p.rect.width), round(p.rect.height)) for p in pdf} == {(842, 595)}
    thumb = br.output_page_thumb(res["job_id"], out["name"], 3, 200)
    assert thumb[:4] == PNG_MAGIC
    with pytest.raises(bridge.BridgeError):
        br.output_page_thumb(res["job_id"], "../x.pdf", 0, 200)
    br.delete_job(res["job_id"])
    assert not Path(out["path"]).exists()


def test_test_sheet_only_and_zip_outputs(br: Any, fx: Path) -> None:
    doc = _ingest(br, fx / "a0_vector_grid.pdf")
    only = json.loads(br.render(_settings(doc["id"]), None, True))
    assert only["outputs"][0]["pages"] == 1
    zipped = json.loads(
        br.render(_settings(doc["id"], output={"formats": ["png_zip"], "dpi": 150}), None, False)
    )
    assert [o["kind"] for o in zipped["outputs"]] == ["zip"]


def test_resolution_is_capped_with_local_limit_only_when_it_matters(br: Any, fx: Path) -> None:
    doc = _ingest(br, fx / "a0_vector_grid.pdf")
    plain = json.loads(br.render(_settings(doc["id"], output={"dpi": 600}), None, True))
    assert plain["warnings"] == []  # a vector PDF output does not depend on dpi
    raster = json.loads(
        br.render(_settings(doc["id"], output={"dpi": 600, "rasterize": True}), None, True)
    )
    (note,) = raster["warnings"]
    assert note["code"] == "LOCAL_LIMIT"
    assert note["params"] == {"limit": "dpi", "requested": 600, "applied": 300}


def test_layers_and_physical_size_flow(br: Any, fx: Path) -> None:
    doc = _ingest(br, fx / "layers_ocg.pdf")
    layers = doc["pages"][0]["layers"]
    assert len(layers) == 3
    shown = br.page_preview(doc["id"], 0, 300)
    hidden = br.page_preview(doc["id"], 0, 300, [layers[0]["name"]], None)
    assert shown[:4] == hidden[:4] == b"\x89PNG"
    assert shown != hidden


def test_grayscale_vector_without_ghostscript_is_rasterized(
    br: Any, fx: Path, no_tools: None
) -> None:
    doc = _ingest(br, fx / "a0_vector_grid.pdf")
    res = json.loads(br.render(_settings(doc["id"], color={"mode": "grayscale"}), None, True))
    codes = [(w["code"], w["params"]) for w in res["warnings"]]
    assert ("LOCAL_LIMIT", {"limit": "gray_vector"}) in codes
    assert res["outputs"][0]["kind"] == "pdf"


def test_cancel_from_the_progress_callback(br: Any, fx: Path) -> None:
    doc = _ingest(br, fx / "a0_vector_grid.pdf")
    jobs = Path(bridge._root()) / "jobs"
    before = set(jobs.iterdir())
    with pytest.raises(bridge.BridgeError) as exc:
        br.render(_settings(doc["id"]), None, False, lambda *_a: True)
    assert json.loads(str(exc.value))["code"] == "JOB_CANCELLED"
    assert set(jobs.iterdir()) == before


# --------------------------------------------------------------------------- raster (Pillow)


def test_raster_photo_on_the_pillow_backend(br: Any, fx: Path) -> None:
    with use_backend("pillow"):
        doc = _ingest(br, fx / "photo_nodpi.jpg")
        assert doc["status"] == "ready"
        page = doc["pages"][0]
        assert page["kind"] == "raster"
        assert {w["code"] for w in page["warnings"]} >= {"PHYSICAL_SIZE_ASSUMED"}
        fixed = json.loads(br.set_physical_size(doc["id"], 0, paper=None, dpi=150))
        assert fixed["dpi"] == 150
        assert fixed["dpi_source"] == "user"
        assert json.loads(br.get_document(doc["id"]))["pages"][0]["dpi"] == 150

        settings = _settings(doc["id"], scale={"mode": "percent", "percent": 12})
        plan = json.loads(br.plan(settings, 0, False, None))
        assert plan["counts"]["printed"] >= 1
        res = json.loads(br.render(settings, None, False))
        out = res["outputs"][0]
        with pymupdf.open(out["path"]) as pdf:
            assert pdf.page_count == plan["counts"]["printed"] + 1
        assert br.sheet_preview(settings, 0, plan["sheets"][0]["id"], 300)[:4] == b"\x89PNG"
        # pixel operations go through the in-memory path
        bw = json.loads(
            br.render(
                _settings(doc["id"], color={"mode": "bw", "whiten_background": True}), None, True
            )
        )
        assert bw["outputs"][0]["kind"] == "pdf"


def test_cmyk_jpeg_warns_icc_ignored_without_a_colour_engine(
    br: Any, fx: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a0td.imaging import vips_compat

    monkeypatch.setattr(vips_compat, "icc_supported", lambda: False)
    with use_backend("pillow"):
        doc = _ingest(br, fx / "cmyk.jpg")
    assert doc["status"] == "ready"
    assert "ICC_IGNORED" in {w["code"] for w in doc["warnings"]}


def test_g4_tiff_and_multipage_tiff(br: Any, fx: Path) -> None:
    with use_backend("pillow"):
        scan = _ingest(br, fx / "scan_a0_200dpi_g4.tif")
        multi = _ingest(br, fx / "multipage.tif")
    assert scan["status"] == multi["status"] == "ready"
    assert scan["pages"][0]["px"] == {"w": 6622, "h": 9362}
    assert multi["page_count"] == 3


@pytest.mark.parametrize("name", ["photo_nodpi.avif", "photo_150dpi.heic"])
def test_modern_formats_through_pillow_heif(br: Any, fx: Path, name: str) -> None:
    with use_backend("pillow"):
        doc = _ingest(br, fx / name)
    assert doc["status"] == "ready"
    assert doc["pages"][0]["px"] == {"w": 800, "h": 600}


# ---------------------------------------------------------------------- svg, xps, dxf (MuPDF)


def test_svg_through_mupdf(br: Any, fx: Path, no_tools: None) -> None:
    physical = _ingest(br, fx / "svg_physical.svg")
    assert physical["status"] == "ready"
    assert physical["pages"][0]["width_mm"] == pytest.approx(200, abs=0.1)
    assert physical["pages"][0]["height_mm"] == pytest.approx(100, abs=0.1)
    unitless = _ingest(br, fx / "svg_no_units.svg")
    assert unitless["status"] == "ready"
    assert "PHYSICAL_SIZE_ASSUMED" in {w["code"] for w in unitless["pages"][0]["warnings"]}
    # unitless SVG: set the size afterwards, like a raster
    page = json.loads(br.set_physical_size(unitless["id"], 0, width_mm=300))
    assert page["width_mm"] == pytest.approx(300, abs=0.1)
    res = json.loads(br.render(_settings(physical["id"]), None, False))
    assert res["outputs"][0]["pages"] >= 2


def test_xps_through_mupdf(br: Any, fx: Path, no_tools: None) -> None:
    doc = _ingest(br, fx / "xps_a4.xps")
    assert doc["status"] == "ready"
    assert doc["pages"][0]["width_mm"] == pytest.approx(297, abs=0.5)


def test_dxf_when_ezdxf_is_installed(br: Any, fx: Path, no_tools: None) -> None:
    pytest.importorskip("ezdxf")
    doc = _ingest(br, fx / "dxf_model_plan.dxf", options={"cad": {"scale": 50}})
    assert doc["status"] == "ready"
    assert doc["pages"][0]["width_mm"] == pytest.approx(255, abs=10)
    cad = json.loads(br.cad_info(doc["id"]))
    assert cad["scale"] == 50
    assert {"layouts", "units", "units_source"} <= set(cad)
    with pytest.raises(bridge.BridgeError):
        br.cad_info(_ingest(br, fx / "a0_vector_grid.pdf")["id"])


# ----------------------------------------------------------------------- errors and limits


def test_encrypted_pdf_asks_for_a_password_then_opens(br: Any, fx: Path) -> None:
    waiting = _ingest(br, fx / "encrypted.pdf")
    assert (waiting["status"], waiting["error_code"]) == ("password_required", "PASSWORD_REQUIRED")
    wrong = _ingest(br, fx / "encrypted.pdf", password="nope", options={"doc_id": waiting["id"]})
    assert (wrong["status"], wrong["error_code"]) == ("password_required", "PASSWORD_INVALID")
    ok = _ingest(br, fx / "encrypted.pdf", password="secret", options={"doc_id": waiting["id"]})
    assert ok["status"] == "ready"
    assert ok["id"] == waiting["id"]


def test_corrupt_and_oversized_inputs_fail_cleanly(br: Any, fx: Path, no_tools: None) -> None:
    corrupt = _ingest(br, fx / "corrupt.pdf")
    assert corrupt["status"] == "failed"
    assert corrupt["error_code"] in {
        "CORRUPT_FILE",
        "CONVERSION_FAILED",
    }
    bomb = _ingest(br, fx / "malicious" / "pixel_bomb.png")
    assert bomb["status"] == "failed"
    assert bomb["error_code"] == "PIXEL_LIMIT_EXCEEDED"
    assert bomb["error_params"]["local"] is True


def test_errors_are_problem_json(br: Any, fx: Path) -> None:
    with pytest.raises(bridge.BridgeError) as exc:
        br.plan('{"scale": {"mode": "percent", "percent": -4}}', 0)
    body = json.loads(str(exc.value))
    assert body["code"] == "INVALID_SETTINGS"
    assert body["status"] == 422
    assert body["errors"][0]["field"].startswith("settings.")
    assert isinstance(exc.value, DomainError)
    doc = _ingest(br, fx / "a0_vector_grid.pdf")
    with pytest.raises(bridge.BridgeError) as missing:
        br.plan(_settings("11111111-1111-4111-8111-111111111111"), 0)
    assert json.loads(str(missing.value))["code"] == "NOT_FOUND"
    with pytest.raises(bridge.BridgeError) as page:
        br.plan(_settings(doc["id"]), 5)
    assert json.loads(str(page.value))["status"] == 404
    bad = json.loads(br.call_json("plan", json.dumps(['{"document_id": "x"}'])))
    assert bad["ok"] is False
    assert bad["error"]["code"] == "INVALID_SETTINGS"
    assert json.loads(br.call_json("papers"))["ok"] is True
    assert json.loads(br.call_json("nope"))["error"]["code"] == "NOT_FOUND"


def test_export_import_roundtrip(br: Any, fx: Path) -> None:
    doc = _ingest(br, fx / "a0_vector_grid.pdf")
    blob = br.export_document(doc["id"])
    br.delete_document(doc["id"])
    with pytest.raises(bridge.BridgeError):
        br.get_document(doc["id"])
    restored = json.loads(br.import_document(doc["id"], blob))
    assert restored["status"] == "ready"
    assert json.loads(br.plan(_settings(doc["id"]), 0))["counts"]["printed"] == 21
