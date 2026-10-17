"""DXF / DWG over the API: options at upload, ``GET /cad`` and the re-ingest endpoint (WP-17)."""

import time
from pathlib import Path

import pytest
from apihelpers import upload, wait_document
from fastapi.testclient import TestClient

PLAN = "dxf_model_plan.dxf"


@pytest.fixture
def plan(fixtures_dir: Path) -> Path:
    path = fixtures_dir / PLAN
    if not path.exists():
        pytest.skip(f"{PLAN} was not generated")
    return path


def _upload_cad(client: TestClient, path: Path, **fields: str) -> dict:  # type: ignore[type-arg]
    r = client.post(
        "/api/v1/documents", files={"file": (path.name, path.read_bytes())}, data=fields
    )
    assert r.status_code == 202, r.text
    return wait_document(client, r.json()["id"])


def test_options_sent_with_the_upload_reach_the_converter(client: TestClient, plan: Path) -> None:
    doc = _upload_cad(client, plan, cad_scale="50", cad_color="black", cad_layout="Model")
    assert doc["status"] == "ready", doc
    assert doc["format"] == "dxf"
    assert doc["pages"][0]["width_mm"] == pytest.approx(250, abs=2)
    cad = client.get(f"/api/v1/documents/{doc['id']}/cad").json()
    assert cad["scale"] == 50
    assert cad["scale_auto"] is False
    assert cad["color"] == "black"
    assert [x["name"] for x in cad["layouts"]] == ["Model", "Layout1"]


def test_without_options_the_scale_is_automatic(client: TestClient, plan: Path) -> None:
    doc = _upload_cad(client, plan)
    cad = client.get(f"/api/v1/documents/{doc['id']}/cad").json()
    assert cad["scale"] == 20
    assert cad["scale_auto"] is True


def test_invalid_upload_options_are_a_422_and_leave_nothing_behind(
    client: TestClient, plan: Path
) -> None:
    r = client.post(
        "/api/v1/documents",
        files={"file": (plan.name, plan.read_bytes())},
        data={"cad_color": "purple"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_SETTINGS"
    assert body["errors"][0]["field"] == "cad_color"
    assert client.get("/api/v1/documents").json() == []


def test_options_skip_the_deduplication(client: TestClient, plan: Path) -> None:
    first = _upload_cad(client, plan)
    again = client.post(
        "/api/v1/documents",
        files={"file": (plan.name, plan.read_bytes())},
        data={"cad_scale": "100"},
    )
    assert again.status_code == 202
    assert again.json()["reused"] is False
    assert again.json()["id"] != first["id"]


def test_reingest_with_other_options(client: TestClient, plan: Path) -> None:
    doc = _upload_cad(client, plan)
    doc_id = doc["id"]
    assert doc["pages"][0]["width_mm"] == pytest.approx(610, abs=3)
    r = client.post(f"/api/v1/documents/{doc_id}/cad-options", json={"scale": 100, "color": "gray"})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "processing"
    done = wait_document(client, doc_id)
    assert done["status"] == "ready"
    assert done["pages"][0]["width_mm"] == pytest.approx(130, abs=2)
    cad = client.get(f"/api/v1/documents/{doc_id}/cad").json()
    assert cad["scale"] == 100
    assert cad["color"] == "gray"


def test_reingest_failure_keeps_the_previous_rendering_and_allows_a_retry(
    client: TestClient, plan: Path
) -> None:
    doc = _upload_cad(client, plan)
    doc_id = doc["id"]
    before = doc["pages"][0]["width_mm"]
    r = client.post(f"/api/v1/documents/{doc_id}/cad-options", json={"layout": "Nope"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert job["status"] == "failed"
    assert job["error_code"] == "INVALID_SETTINGS"
    after = wait_document(client, doc_id)
    assert after["status"] == "ready"
    assert after["pages"][0]["width_mm"] == pytest.approx(before)
    assert client.get(f"/api/v1/documents/{doc_id}/cad").status_code == 200
    assert client.get(f"/api/v1/documents/{doc_id}/pages/0/preview").status_code == 200
    # a retry with valid options works
    assert (
        client.post(f"/api/v1/documents/{doc_id}/cad-options", json={"scale": 100}).status_code
        == 202
    )
    assert wait_document(client, doc_id)["pages"][0]["width_mm"] == pytest.approx(130, abs=2)


def test_unknown_units_are_a_422_at_post_time(client: TestClient, plan: Path) -> None:
    doc_id = _upload_cad(client, plan)["id"]
    r = client.post(f"/api/v1/documents/{doc_id}/cad-options", json={"units": "parsec"})
    assert r.status_code == 422
    r = client.post(
        "/api/v1/documents",
        files={"file": (plan.name, plan.read_bytes())},
        data={"cad_units": "parsec"},
    )
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "cad_units"


def test_cad_uploads_are_never_deduplicated(client: TestClient, plan: Path) -> None:
    first = _upload_cad(client, plan)
    client.post(f"/api/v1/documents/{first['id']}/cad-options", json={"scale": 100})
    wait_document(client, first["id"])
    again = client.post("/api/v1/documents", files={"file": (plan.name, plan.read_bytes())})
    assert again.status_code == 202
    assert again.json()["reused"] is False
    assert again.json()["id"] != first["id"]
    done = wait_document(client, again.json()["id"])
    assert done["pages"][0]["width_mm"] == pytest.approx(610, abs=3)


def test_cad_endpoints_reject_other_documents(client: TestClient, fixtures_dir: Path) -> None:
    pdf = fixtures_dir / "a0_vector_grid.pdf"
    doc = wait_document(client, upload(client, pdf).json()["id"])
    assert client.get(f"/api/v1/documents/{doc['id']}/cad").status_code == 404
    r = client.post(f"/api/v1/documents/{doc['id']}/cad-options", json={"scale": 10})
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_SETTINGS"
    assert client.post("/api/v1/documents/nope/cad-options", json={}).status_code == 404


def test_cad_options_body_is_validated(client: TestClient, plan: Path) -> None:
    doc_id = _upload_cad(client, plan)["id"]
    for body in ({"scale": 0}, {"color": "purple"}, {"unknown": 1}):
        r = client.post(f"/api/v1/documents/{doc_id}/cad-options", json=body)
        assert r.status_code == 422, body
