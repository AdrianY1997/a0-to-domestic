"""WP-08 audit fixes: SSE termination, stdout draining, purge safety, atomic retries, 500s."""

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from apihelpers import ingest, job_body, parse_sse, wait_document, wait_for, wait_job
from fastapi.testclient import TestClient

from a0td.api.app import create_app
from a0td.config import AppConfig
from a0td.jobs import manager as manager_module
from a0td.storage import models as m
from a0td.storage.files import new_id
from a0td.storage.retention import ORPHAN_GRACE_S, purge

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def _collect_sse(client: TestClient, url: str, out: dict[str, Any]) -> threading.Thread:
    def run() -> None:  # TestClient only returns once the whole stream has ended
        with client.stream("GET", url) as resp:
            out["text"] = "".join(resp.iter_text())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(1.5)  # let the subscription register
    return thread


def _slow_job(client: TestClient, doc_id: str, mp: pytest.MonkeyPatch, tmp_path: Path) -> str:
    mp.setenv("FAKE_RENDER_MODE", "slow")
    mp.setenv("FAKE_PID_FILE", str(tmp_path / f"{new_id()}.pid"))
    r = client.post("/api/v1/jobs", json={"settings": job_body(doc_id)})
    assert r.status_code == 202, r.text
    job_id: str = r.json()["id"]
    wait_job(client, job_id, statuses=("running",))
    return job_id


@posix_only
def test_delete_running_job_ends_sse_stream(
    client: TestClient, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    job_id = _slow_job(client, doc["id"], monkeypatch, tmp_path)
    out: dict[str, Any] = {}
    thread = _collect_sse(client, f"/api/v1/jobs/{job_id}/events", out)
    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    thread.join(20)
    assert not thread.is_alive(), "the SSE stream never terminated"
    events = parse_sse(out["text"])
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "JOB_CANCELLED"
    assert client.app.state.services.manager.hub.last(job_id) is None  # type: ignore[attr-defined]


@posix_only
def test_row_deleted_while_running_still_publishes_terminal_event(
    client: TestClient, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    svc = client.app.state.services  # type: ignore[attr-defined]
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    svc.config.job_timeout_s = 3
    job_id = _slow_job(client, doc["id"], monkeypatch, tmp_path)
    out: dict[str, Any] = {}
    thread = _collect_sse(client, f"/api/v1/jobs/{job_id}/events", out)
    with svc.db.session() as s:  # the DELETE race: the row disappears under the manager
        s.delete(s.get(m.Job, job_id))
    thread.join(30)
    assert not thread.is_alive(), "the SSE stream hung after the row vanished"
    assert parse_sse(out["text"])[-1][1]["code"] == "JOB_CANCELLED"
    assert svc.manager.hub.last(job_id) is None


@posix_only
def test_oversized_stdout_line_keeps_draining(
    make_config: Callable[..., AppConfig],
    fixtures_dir: Path,
    fake_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manager_module, "STDOUT_LIMIT", 64 * 1024)
    with TestClient(create_app(make_config(job_timeout_s=40))) as client:
        doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
        monkeypatch.setenv("FAKE_RENDER_MODE", "bigline")
        r = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])})
        started = time.monotonic()
        job = wait_job(client, r.json()["id"], timeout=60)
        # Not drained -> the worker would block on a full pipe until the 40 s timeout.
        assert time.monotonic() - started < 30
        assert job["status"] == "succeeded", job


@posix_only
def test_group_is_killed_after_a_normal_worker_exit(
    client: TestClient, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    pid_file = tmp_path / "orphan.pid"
    monkeypatch.setenv("FAKE_RENDER_MODE", "orphan")
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    r = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])})
    assert wait_job(client, r.json()["id"], timeout=60)["status"] == "succeeded"
    wait_for(lambda: {"alive": _alive(int(pid_file.read_text()))}, lambda d: not d["alive"], 15)


def test_purge_all_keeps_fresh_orphan_dirs(client: TestClient) -> None:
    svc = client.app.state.services  # type: ignore[attr-defined]
    fresh, stale = new_id(), new_id()
    for doc_id in (fresh, stale):
        svc.files.doc_dir(doc_id).mkdir(parents=True)
    long_ago = time.time() - ORPHAN_GRACE_S - 100
    os.utime(svc.files.doc_dir(stale), (long_ago, long_ago))
    purge(svc.db, svc.files, everything=True)  # (the startup retention pass may race us)
    assert svc.files.doc_dir(fresh).exists()  # an upload in flight has a dir but no row yet
    assert not svc.files.doc_dir(stale).exists()


def test_concurrent_password_retries_create_one_job(client: TestClient, fixtures_dir: Path) -> None:
    doc = ingest(client, fixtures_dir / "encrypted.pdf")
    assert doc["status"] == "password_required"
    results: list[int] = []
    barrier = threading.Barrier(4)

    def retry() -> None:
        barrier.wait()
        r = client.post(f"/api/v1/documents/{doc['id']}/password", json={"password": "nope"})
        results.append(r.status_code)

    threads = [threading.Thread(target=retry) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sorted(results) == [202, 422, 422, 422]
    wait_document(client, doc["id"])
    svc = client.app.state.services  # type: ignore[attr-defined]
    with svc.db.session() as s:
        jobs = s.query(m.Job).filter(m.Job.document_id == doc["id"]).all()
    assert len(jobs) == 2  # the original ingest + exactly one retry


def test_thumbnail_cache_is_written_atomically(
    client: TestClient, fixtures_dir: Path, config: AppConfig
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    r = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])})
    job = wait_job(client, r.json()["id"])
    name = job["outputs"][0]["name"]
    url = f"/api/v1/jobs/{job['id']}/outputs/{name}/pages/0/thumb"
    first = client.get(url)
    assert first.status_code == 200
    assert client.get(url).content == first.content  # served from the cache
    thumbs = config.data_dir / "jobs" / job["id"] / "thumbs"
    assert [p.suffix for p in thumbs.iterdir()] == [".webp"]  # no temp leftovers


def test_unhandled_error_is_problem_json_with_security_headers(
    config: AppConfig, fake_engine: None
) -> None:
    app = create_app(config)

    @app.get("/api/v1/_boom")
    def boom() -> None:
        raise RuntimeError("secret path /data/documents/x")

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/v1/_boom")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "INTERNAL_ERROR"
    assert "secret" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in r.headers
    assert r.headers["x-frame-options"] == "DENY"
