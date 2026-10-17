"""Document endpoints: password flow (CA-6), raster physical size (CA-4), errors, upload limits."""

from collections.abc import Callable
from pathlib import Path

import pytest
from apihelpers import ingest, job_body, upload, wait_document
from fastapi.testclient import TestClient

from a0td.api.app import create_app
from a0td.config import AppConfig

TIGHT_PAPER = {"paper": "CUSTOM", "custom_paper_mm": {"width": 60, "height": 80}}


def _problem(resp) -> dict:  # type: ignore[no-untyped-def]
    assert resp.headers["content-type"].startswith("application/problem+json"), resp.text
    return resp.json()


def test_password_flow_ca6(client: TestClient, fixtures_dir: Path) -> None:
    r = upload(client, fixtures_dir / "encrypted.pdf")
    assert r.status_code == 202
    doc_id = r.json()["id"]
    doc = wait_document(client, doc_id)
    assert doc["status"] == "password_required"
    assert doc["error_code"] == "PASSWORD_REQUIRED"
    # not usable yet
    plan = client.post("/api/v1/plan", json={"settings": job_body(doc_id)})
    assert _problem(plan)["code"] == "DOCUMENT_NOT_READY"
    assert plan.status_code == 409

    # wrong password: PASSWORD_INVALID, and the document is not blocked
    r = client.post(f"/api/v1/documents/{doc_id}/password", json={"password": "nope"})
    assert r.status_code == 202
    assert r.json()["status"] == "processing"
    doc = wait_document(client, doc_id)
    assert doc["status"] == "password_required"
    assert doc["error_code"] == "PASSWORD_INVALID"

    # right password
    r = client.post(f"/api/v1/documents/{doc_id}/password", json={"password": "secret"})
    assert r.status_code == 202
    doc = wait_document(client, doc_id)
    assert doc["status"] == "ready", doc
    assert doc["error_code"] is None
    assert client.get(f"/api/v1/documents/{doc_id}/pages/0/thumb").status_code == 200

    # the password endpoint only applies while waiting for one
    again = client.post(f"/api/v1/documents/{doc_id}/password", json={"password": "secret"})
    assert again.status_code == 422
    assert _problem(again)["code"] == "INVALID_SETTINGS"


def test_password_in_the_upload(client: TestClient, fixtures_dir: Path) -> None:
    doc = ingest(client, fixtures_dir / "encrypted.pdf", password="secret")
    assert doc["status"] == "ready"


def test_print_not_allowed_is_not_bypassed(client: TestClient, fixtures_dir: Path) -> None:
    doc = ingest(client, fixtures_dir / "encrypted_noprint.pdf")
    assert doc["status"] == "failed"
    assert doc["error_code"] == "PRINT_NOT_ALLOWED"


def test_unsupported_and_corrupt_files(client: TestClient) -> None:
    doc = ingest(client, content=b"this is not a document at all" * 20, name="x.pdf")
    assert doc["status"] == "failed"
    assert doc["error_code"] == "UNSUPPORTED_FORMAT"
    assert "supported" in doc["error_params"]


def test_raster_physical_size_ca4(client: TestClient, fixtures_dir: Path) -> None:
    doc = ingest(client, fixtures_dir / "photo_nodpi.jpg")
    assert doc["status"] == "ready", doc
    assert doc["format"] == "jpeg"
    page = doc["pages"][0]
    assert page["dpi_source"] == "assumed"
    assert page["px"] == {"w": 4000, "h": 5657}
    doc_id = doc["id"]

    settings = job_body(doc_id, target={"paper": "A4"})
    plan = client.post("/api/v1/plan", json={"settings": settings}).json()
    codes = {w["code"] for w in plan["warnings"]}
    assert {"PHYSICAL_SIZE_ASSUMED", "LOW_DPI"} <= codes
    assert plan["effective_dpi"] == pytest.approx(120.8, abs=0.5)

    fit = job_body(doc_id, target={"paper": "A4"}, scale={"mode": "fit_sheet"})
    plan = client.post("/api/v1/plan", json={"settings": fit}).json()
    assert "LOW_DPI" not in {w["code"] for w in plan["warnings"]}

    # the user fixes the physical size
    bad = client.post(f"/api/v1/documents/{doc_id}/pages/0/physical-size", json={})
    assert bad.status_code == 422
    assert _problem(bad)["code"] == "INVALID_SETTINGS"
    r = client.post(f"/api/v1/documents/{doc_id}/pages/0/physical-size", json={"dpi": 300})
    assert r.status_code == 200, r.text
    assert r.json()["dpi"] == 300
    assert r.json()["dpi_source"] == "user"
    stored = client.get(f"/api/v1/documents/{doc_id}").json()["pages"][0]
    assert stored["dpi"] == 300
    plan = client.post("/api/v1/plan", json={"settings": settings}).json()
    assert "LOW_DPI" not in {w["code"] for w in plan["warnings"]}
    assert (
        client.post(
            f"/api/v1/documents/{doc_id}/pages/5/physical-size", json={"dpi": 300}
        ).status_code
        == 404
    )


def test_blank_sheets_use_the_stored_occupancy(client: TestClient, fixtures_dir: Path) -> None:
    doc = ingest(client, fixtures_dir / "a0_top_half_only.pdf")
    settings = job_body(doc["id"], tiling={"skip_blank": True})
    plan = client.post("/api/v1/plan", json={"settings": settings}).json()
    assert plan["counts"]["blank"] > 0
    assert plan["counts"]["printed"] < plan["counts"]["total"]


def test_plan_errors(client: TestClient, fixtures_dir: Path) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    doc_id = doc["id"]
    r = client.post("/api/v1/plan", json={"settings": job_body(doc_id, bogus=1)})
    assert r.status_code == 422
    body = _problem(r)
    assert body["code"] == "INVALID_SETTINGS"
    assert body["errors"]
    r = client.post("/api/v1/plan", json={"settings": {"target": {"paper": "A4"}}})
    assert _problem(r)["code"] == "INVALID_SETTINGS"  # no document_id
    r = client.post(
        "/api/v1/plan", json={"settings": job_body("00000000-0000-4000-8000-000000000000")}
    )
    assert r.status_code == 404
    r = client.post("/api/v1/plan", json={"settings": job_body(doc_id), "page_index": 7})
    assert r.status_code == 404
    r = client.post(
        "/api/v1/plan",
        json={
            "settings": job_body(
                doc_id, target=TIGHT_PAPER, tiling={"join": "overlap", "overlap_mm": 50}
            )
        },
    )
    assert r.status_code == 422
    assert _problem(r)["code"] in {"OVERLAP_TOO_LARGE", "CONTENT_AREA_TOO_SMALL"}
    # job creation validates with the planner before queueing anything
    r = client.post(
        "/api/v1/jobs",
        json={
            "settings": job_body(
                doc_id, target=TIGHT_PAPER, tiling={"join": "overlap", "overlap_mm": 50}
            )
        },
    )
    assert r.status_code == 422
    r = client.post("/api/v1/jobs", json={"settings": job_body(doc_id, source={"pages": "3"})})
    assert r.status_code == 422
    assert _problem(r)["errors"][0]["field"] == "settings.source.pages"
    r = client.post(
        "/api/v1/jobs",
        json={"settings": job_body(doc_id, scale={"mode": "fit_grid", "cols": 100, "rows": 100})},
    )
    assert r.status_code == 422
    assert client.get("/api/v1/jobs").json() == []


def test_too_many_sheets(
    make_config: Callable[..., AppConfig], fixtures_dir: Path, fake_engine: None
) -> None:
    with TestClient(create_app(make_config(max_sheets=20))) as client:
        doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
        settings = job_body(doc["id"], scale={"mode": "percent", "percent": 400})
        for url in ("/api/v1/plan", "/api/v1/jobs"):
            body = {"settings": settings}
            r = client.post(url, json=body)
            assert r.status_code == 422, url
            assert _problem(r)["code"] == "TOO_MANY_SHEETS"
        assert client.get("/api/v1/jobs").json() == []


def test_sheet_preview(
    client: TestClient, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    body = {"settings": job_body(doc["id"]), "page_index": 0, "sheet_id": "A1", "max_px": 400}
    r = client.post("/api/v1/plan/sheet-preview", json=body)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")
    monkeypatch.setenv("A0TD_RENDER_ENGINE", "no_such_engine_module")
    r = client.post("/api/v1/plan/sheet-preview", json=body)
    assert r.status_code == 501
    assert _problem(r)["code"] == "RENDER_UNAVAILABLE"


def test_upload_errors(client: TestClient) -> None:
    r = client.post("/api/v1/documents", data={"password": "x"})  # no file, urlencoded
    assert r.status_code == 422
    assert _problem(r)["code"] == "INVALID_SETTINGS"
    r = client.post("/api/v1/documents", files={"other": ("a.pdf", b"x")})
    assert r.status_code == 422
    assert _problem(r)["errors"][0]["field"] == "file"
    assert client.get("/api/v1/documents").json() == []


def test_upload_size_cap_streams_and_cleans_up(
    make_config: Callable[..., AppConfig], fake_engine: None
) -> None:
    config = make_config(max_upload_mb=1)
    with TestClient(create_app(config)) as client:
        r = upload(client, content=b"0" * (3 * 1024 * 1024), name="big.pdf")
        assert r.status_code == 413
        body = _problem(r)
        assert body["code"] == "FILE_TOO_LARGE"
        assert body["params"] == {"max_mb": 1}
        assert client.get("/api/v1/documents").json() == []
        assert list((config.data_dir / "documents").iterdir()) == []
        # a file within the limit is accepted
        ok = upload(client, content=b"%PDF-1.4 small", name="small.pdf")
        assert ok.status_code == 202


def test_hostile_file_names_never_reach_paths(client: TestClient, config: AppConfig) -> None:
    names = [
        "../../etc/passwd.pdf",
        "..\\..\\windows\\evil.pdf",
        "C:\\Users\\x\\y.pdf",
        'a"b\r\nX-Evil: 1.pdf',
        "con.pdf",
        "ñandú 日本語.pdf",
        "no_extension",
        "x." + "a" * 40,
    ]
    ids = []
    for name in names:
        r = upload(client, content=b"not a real pdf", name=name)
        assert r.status_code == 202, (name, r.text)
        ids.append(r.json()["id"])
        assert "\r" not in r.json()["name"]
        assert "\n" not in r.json()["name"]
    docs_dir = (config.data_dir / "documents").resolve()
    created = sorted(p.name for p in docs_dir.iterdir())
    assert created == sorted(ids)  # one directory per upload, named by its uuid
    for doc_id in ids:
        wait_document(client, doc_id)
        files = {p.name for p in (docs_dir / doc_id).iterdir()}
        assert any(f.startswith("original.") for f in files)
        assert all(not f.startswith("..") for f in files)
    assert not (config.data_dir.parent / "etc").exists()
    assert not (config.data_dir / "etc").exists()
    assert client.get("/api/v1/documents/..%2F..%2Fetc").status_code == 404
    assert client.get("/api/v1/documents/not-a-uuid/pages/0/thumb").status_code == 404
    assert client.get("/api/v1/jobs/not-a-uuid").status_code == 404


def test_catalogs(client: TestClient) -> None:
    printers = client.get("/api/v1/printers").json()
    assert {p["id"] for p in printers} >= {"builtin-generic", "builtin-inkjet", "builtin-laser"}
    assert client.get("/api/v1/printers/builtin-laser").json()["kind"] == "laser"
    assert client.get("/api/v1/printers/nope").status_code == 404
    presets = client.get("/api/v1/presets").json()
    assert len(presets) == 7
    assert (
        client.get("/api/v1/presets/builtin-sewing-a4").json()["settings"]["tiling"]["join"]
        == "butt"
    )
    assert client.get("/api/v1/presets/nope").status_code == 404
    assert len(client.get("/api/v1/papers").json()) == 19
