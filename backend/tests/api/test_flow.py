"""End-to-end API flow with real ingest worker subprocesses and the fake render engine."""

from pathlib import Path

import pytest
from apihelpers import ingest, job_body, parse_sse, upload, wait_document, wait_job
from fastapi.testclient import TestClient


def test_upload_ready_plan_job_download(client: TestClient, fixtures_dir: Path) -> None:
    r = upload(client, fixtures_dir / "a0_vector_grid.pdf")
    assert r.status_code == 202, r.text
    doc = r.json()
    assert doc["status"] == "processing"
    assert r.headers["location"] == f"/api/v1/documents/{doc['id']}"
    assert doc["name"] == "a0_vector_grid.pdf"
    assert len(doc["sha256"]) == 64

    doc = wait_document(client, doc["id"])
    assert doc["status"] == "ready", doc
    assert doc["format"] == "pdf"
    assert doc["page_count"] == 1
    page = doc["pages"][0]
    assert page["width_mm"] == pytest.approx(841, abs=1)
    assert page["height_mm"] == pytest.approx(1189, abs=1)

    # previews
    thumb = client.get(f"/api/v1/documents/{doc['id']}/pages/0/thumb")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"
    small = client.get(f"/api/v1/documents/{doc['id']}/pages/0/preview?max_px=200")
    assert small.status_code == 200
    assert client.get(f"/api/v1/documents/{doc['id']}/pages/9/preview").status_code == 404

    # plan (CA-1): A0 on A4, 100 %, overlap 10 mm, margins 5 mm -> 3 x 7 = 21 sheets
    body = {
        "settings": job_body(
            doc["id"],
            target={"paper": "A4", "margins_mm": {"top": 5, "right": 5, "bottom": 5, "left": 5}},
        ),
        "page_index": 0,
        "include_alternatives": True,
    }
    plan = client.post("/api/v1/plan", json=body)
    assert plan.status_code == 200, plan.text
    plan_json = plan.json()
    assert plan_json["counts"]["total"] == 21
    assert plan_json["orientation"] == "landscape"
    assert plan_json["alternatives"]

    # job
    r = client.post(
        "/api/v1/jobs",
        json={"settings": body["settings"], "outputs": ["pdf", "png_zip"]},
    )
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["progress"] == 100
    assert job["settings"]["output"]["formats"] == ["pdf", "png_zip"]
    assert [o["name"] for o in job["outputs"]] == ["tiles ñ.pdf", "sheets.zip"]
    assert job["plans"][0]["counts"]["total"] == 21
    assert job["warnings"][0]["exact_scale"] is False
    assert job["warnings"][0]["printer"] == "builtin-generic"

    listed = client.get("/api/v1/jobs").json()
    assert [j["id"] for j in listed] == [job["id"]]
    assert listed[0]["plans"] is None  # summaries only

    out = client.get(f"/api/v1/jobs/{job['id']}/outputs/tiles%20%C3%B1.pdf")
    assert out.status_code == 200
    assert out.headers["content-type"] == "application/pdf"
    assert out.content.startswith(b"%PDF")
    disposition = out.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''tiles%20%C3%B1.pdf" in disposition
    assert 'filename="tiles _.pdf"' in disposition

    inline = client.get(f"/api/v1/jobs/{job['id']}/outputs/tiles%20%C3%B1.pdf?inline=1")
    assert inline.headers["content-disposition"].startswith("inline;")
    zipped = client.get(f"/api/v1/jobs/{job['id']}/outputs/sheets.zip?inline=1")
    assert zipped.headers["content-type"] == "application/zip"
    assert zipped.headers["content-disposition"].startswith("attachment;")  # only PDFs go inline

    page_thumb = client.get(f"/api/v1/jobs/{job['id']}/outputs/tiles%20%C3%B1.pdf/pages/0/thumb")
    assert page_thumb.status_code == 200
    assert page_thumb.headers["content-type"] == "image/webp"
    assert client.get(f"/api/v1/jobs/{job['id']}/outputs/nope.pdf").status_code == 404
    assert (
        client.get(
            f"/api/v1/jobs/{job['id']}/outputs/tiles%20%C3%B1.pdf/pages/99/thumb"
        ).status_code
        == 404
    )

    # delete job removes its files
    assert client.delete(f"/api/v1/jobs/{job['id']}").status_code == 204
    assert client.get(f"/api/v1/jobs/{job['id']}").status_code == 404

    # delete document
    assert client.delete(f"/api/v1/documents/{doc['id']}").status_code == 204
    assert client.get(f"/api/v1/documents/{doc['id']}").status_code == 404
    assert client.get("/api/v1/documents").json() == []


def test_exact_scale_and_test_sheet_only_reach_the_engine(
    client: TestClient, fixtures_dir: Path
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    settings = job_body(
        doc["id"], tiling={"join": "butt"}, scale={"mode": "percent", "percent": 100}
    )
    r = client.post("/api/v1/jobs", json={"settings": settings, "test_sheet_only": True})
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["test_sheet_only"] is True
    assert job["warnings"][0]["exact_scale"] is True  # percent 100 + butt
    assert job["warnings"][0]["test_sheet_only"] is True

    # a pattern preset keeps the scale exact even with the overlap join
    r = client.post(
        "/api/v1/jobs",
        json={"settings": job_body(doc["id"]), "preset_id": "builtin-sewing-a4"},
    )
    job = wait_job(client, r.json()["id"])
    assert job["warnings"][0]["exact_scale"] is True


def test_document_events_sse(client: TestClient, fixtures_dir: Path) -> None:
    r = upload(client, fixtures_dir / "a1_landscape.pdf")
    doc_id = r.json()["id"]
    with client.stream("GET", f"/api/v1/documents/{doc_id}/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "".join(resp.iter_text())
    events = parse_sse(text)
    names = [n for n, _ in events]
    assert names[-1] == "done"
    assert set(names[:-1]) <= {"progress"}
    final = events[-1][1]
    assert final["status"] == "succeeded"
    assert final["document"]["status"] == "ready"
    # a late subscriber gets the terminal event straight away
    with client.stream("GET", f"/api/v1/documents/{doc_id}/events") as resp:
        late = parse_sse("".join(resp.iter_text()))
    assert [n for n, _ in late] == ["done"]


def test_job_events_sse(client: TestClient, fixtures_dir: Path) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    r = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])})
    job_id = r.json()["id"]
    with client.stream("GET", f"/api/v1/jobs/{job_id}/events") as resp:
        events = parse_sse("".join(resp.iter_text()))
    assert events[-1][0] == "done"
    assert events[-1][1]["job"]["id"] == job_id
    assert events[-1][1]["job"]["status"] == "succeeded"


def test_job_failure_is_reported(
    client: TestClient, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    monkeypatch.setenv("FAKE_RENDER_MODE", "fail")
    r = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])})
    job_id = r.json()["id"]
    with client.stream("GET", f"/api/v1/jobs/{job_id}/events") as resp:
        events = parse_sse("".join(resp.iter_text()))
    name, data = events[-1]
    assert name == "error"
    assert data["code"] == "CONTENT_AREA_TOO_SMALL"
    assert data["params"] == {"x": 1}
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["error_code"] == "CONTENT_AREA_TOO_SMALL"
    assert client.get(f"/api/v1/jobs/{job_id}/outputs/x.pdf").status_code == 409


def test_worker_crash_is_contained(
    client: TestClient, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    monkeypatch.setenv("FAKE_RENDER_MODE", "crash")
    r = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "failed"
    assert job["error_code"] == "CONVERSION_FAILED"
    assert job["error_params"]["returncode"] == 3
    assert client.get("/healthz").status_code == 200  # the API survived


def test_unsafe_output_name_is_sanitized(
    client: TestClient, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    monkeypatch.setenv("FAKE_RENDER_MODE", "weird_name")
    r = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded"
    assert job["outputs"][0]["name"] == "x.pdf"
    assert client.get(f"/api/v1/jobs/{job['id']}/outputs/x.pdf").status_code == 200
