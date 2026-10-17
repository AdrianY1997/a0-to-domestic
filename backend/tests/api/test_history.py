"""History filters (PRE-03) and upload reuse by SHA-256 (ING-10)."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from apihelpers import ingest, job_body, upload, wait_document, wait_job
from fastapi.testclient import TestClient

from a0td.storage import models as m
from a0td.storage.models import utcnow


def _update_doc(client: TestClient, doc_id: str, **values: Any) -> None:
    with client.app.state.services.db.session() as s:  # type: ignore[attr-defined]
        doc = s.get(m.Document, doc_id)
        for key, value in values.items():
            setattr(doc, key, value)


def test_reuse_identical_upload(client: TestClient, fixtures_dir: Path) -> None:
    src = fixtures_dir / "a0_vector_grid.pdf"
    first = ingest(client, src)
    r = upload(client, src)
    assert r.status_code == 202
    body = r.json()
    assert body["id"] == first["id"]
    assert body["reused"] is True
    assert body["status"] == "ready"
    assert len(client.get("/api/v1/documents").json()) == 1
    other = upload(client, content=b"%PDF-1.4 junk", name="x.pdf")
    assert other.json()["id"] != first["id"]
    assert other.json()["reused"] is False


def test_no_reuse_with_password(client: TestClient, fixtures_dir: Path) -> None:
    src = fixtures_dir / "a0_vector_grid.pdf"
    first = ingest(client, src)
    r = upload(client, src, password="whatever")
    assert r.json()["id"] != first["id"]
    assert r.json()["reused"] is False


def test_history_search_and_pagination(client: TestClient, fixtures_dir: Path) -> None:
    doc = ingest(client, fixtures_dir / "a0_vector_grid.pdf")
    for i in range(3):
        d = upload(client, content=f"%PDF-1.4 {i}".encode(), name=f"other-{i}.pdf").json()
        wait_document(client, d["id"])
    r = client.get("/api/v1/documents", params={"q": "GRID"})
    assert [d["id"] for d in r.json()] == [doc["id"]]
    assert r.headers["x-total-count"] == "1"
    page = client.get("/api/v1/documents", params={"limit": 2, "offset": 0})
    assert len(page.json()) == 2
    assert page.headers["x-total-count"] == "4"
    assert len(client.get("/api/v1/documents", params={"limit": 2, "offset": 3}).json()) == 1
    assert client.get("/api/v1/documents", params={"q": "%"}).json() == []

    job = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])}).json()
    wait_job(client, job["id"])
    jobs = client.get("/api/v1/jobs", params={"q": "grid"})
    assert [j["id"] for j in jobs.json()] == [job["id"]]
    assert jobs.headers["x-total-count"] == "1"
    assert client.get("/api/v1/jobs", params={"q": "nomatch"}).json() == []
    assert client.get("/api/v1/jobs", params={"offset": 1}).json() == []


def test_no_reuse_of_expired_document(client: TestClient, fixtures_dir: Path) -> None:
    src = fixtures_dir / "a0_vector_grid.pdf"
    first = ingest(client, src)
    _update_doc(client, first["id"], expires_at=utcnow() - timedelta(hours=1))
    r = upload(client, src)
    assert r.json()["id"] != first["id"]
    assert r.json()["reused"] is False


@pytest.mark.parametrize("status", [m.DOC_PROCESSING, m.DOC_FAILED, m.DOC_PASSWORD_REQUIRED])
def test_no_reuse_of_non_ready_document(
    client: TestClient, fixtures_dir: Path, status: str
) -> None:
    src = fixtures_dir / "a0_vector_grid.pdf"
    first = ingest(client, src)
    _update_doc(client, first["id"], status=status)
    r = upload(client, src)
    assert r.json()["id"] != first["id"]
    assert r.json()["reused"] is False


def test_no_reuse_of_previously_encrypted_document(client: TestClient, fixtures_dir: Path) -> None:
    src = fixtures_dir / "encrypted.pdf"
    unlocked = ingest(client, src, password="secret")
    assert unlocked["status"] == "ready"
    r = upload(client, src)  # same encrypted bytes, no password: must not get decrypted content
    body = r.json()
    assert body["reused"] is False
    assert body["id"] != unlocked["id"]
    assert wait_document(client, body["id"])["status"] == "password_required"


def test_unlocked_via_password_endpoint_is_never_reused(
    client: TestClient, fixtures_dir: Path
) -> None:
    src = fixtures_dir / "encrypted.pdf"
    first = upload(client, src).json()
    assert wait_document(client, first["id"])["status"] == "password_required"
    client.post(f"/api/v1/documents/{first['id']}/password", json={"password": "secret"})
    assert wait_document(client, first["id"])["status"] == "ready"
    again = upload(client, src).json()
    assert again["id"] != first["id"]
    assert again["reused"] is False
