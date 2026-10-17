"""Cancel, timeouts, concurrency limit and startup recovery of the job manager."""

import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from apihelpers import ingest, job_body, parse_sse, wait_for, wait_job
from fastapi.testclient import TestClient

from a0td.api.app import create_app
from a0td.config import AppConfig
from a0td.domain.settings import JobSettings
from a0td.storage import models as m
from a0td.storage import repo
from a0td.storage.db import Database
from a0td.storage.files import Files, new_id

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def _start_slow_job(
    client: TestClient, doc_id: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[str, Path]:
    pid_file = tmp_path / f"child-{new_id()}.pid"
    monkeypatch.setenv("FAKE_RENDER_MODE", "slow")
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    r = client.post("/api/v1/jobs", json={"settings": job_body(doc_id)})
    assert r.status_code == 202, r.text
    return r.json()["id"], pid_file


@posix_only
def test_cancel_running_job_kills_the_whole_process_group(
    client: TestClient,
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: AppConfig,
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    job_id, pid_file = _start_slow_job(client, doc["id"], monkeypatch, tmp_path)
    wait_for(
        lambda: {"ok": pid_file.exists(), **client.get(f"/api/v1/jobs/{job_id}").json()},
        lambda j: j["ok"] and j["status"] == "running" and j["progress"] > 0,
    )
    grandchild = int(pid_file.read_text())
    assert _alive(grandchild)

    started = time.monotonic()
    r = client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert r.status_code == 202
    job = wait_job(client, job_id, timeout=30)
    assert job["status"] == "cancelled"
    assert job["error_code"] == "JOB_CANCELLED"
    assert time.monotonic() - started < 15
    wait_for(lambda: {"alive": _alive(grandchild)}, lambda d: not d["alive"], timeout=15)
    assert not (config.tmp_dir / job_id).exists()  # private temp dir removed
    assert not (config.data_dir / "jobs" / job_id / "output").exists()

    # the terminal event of a cancelled job
    with client.stream("GET", f"/api/v1/jobs/{job_id}/events") as resp:
        events = parse_sse("".join(resp.iter_text()))
    assert events == [("error", {"code": "JOB_CANCELLED", "detail": "job cancelled", "params": {}})]
    # cancelling again is harmless
    assert client.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 202


@posix_only
def test_workers_limit_queue_and_cancel_queued(
    make_config: Callable[..., AppConfig],
    fixtures_dir: Path,
    fake_engine: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with TestClient(create_app(make_config(workers=1))) as client:
        doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
        first, _ = _start_slow_job(client, doc["id"], monkeypatch, tmp_path)
        second, _ = _start_slow_job(client, doc["id"], monkeypatch, tmp_path)
        wait_job(client, first, statuses=("running",))
        time.sleep(0.5)
        assert client.get(f"/api/v1/jobs/{second}").json()["status"] == "queued"  # one worker only
        # a queued job is cancelled without ever starting
        assert client.post(f"/api/v1/jobs/{second}/cancel").status_code == 202
        queued = client.get(f"/api/v1/jobs/{second}").json()
        assert queued["status"] == "cancelled"
        assert queued["started_at"] is None
        client.post(f"/api/v1/jobs/{first}/cancel")
        assert wait_job(client, first, timeout=30)["status"] == "cancelled"


@posix_only
def test_delete_running_job_and_document(
    client: TestClient, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    job_id, pid_file = _start_slow_job(client, doc["id"], monkeypatch, tmp_path)
    wait_for(lambda: {"ok": pid_file.exists()}, lambda d: d["ok"])
    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert not _alive(int(pid_file.read_text()))

    job_id, pid_file = _start_slow_job(client, doc["id"], monkeypatch, tmp_path)
    wait_for(lambda: {"ok": pid_file.exists()}, lambda d: d["ok"])
    assert client.delete(f"/api/v1/documents/{doc['id']}").status_code == 204
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert not _alive(int(pid_file.read_text()))


@posix_only
def test_job_timeout(
    client: TestClient,
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    client.app.state.services.config.job_timeout_s = 3  # type: ignore[attr-defined]
    job_id, pid_file = _start_slow_job(client, doc["id"], monkeypatch, tmp_path)
    job = wait_job(client, job_id, timeout=30)
    assert job["status"] == "failed"
    assert job["error_code"] == "CONVERSION_TIMEOUT"
    assert job["error_params"] == {"timeout_s": 3}
    wait_for(lambda: {"alive": _alive(int(pid_file.read_text()))}, lambda d: not d["alive"])


def _insert(config: AppConfig, build: Callable[[Database, Files], None]) -> None:
    files = Files(config)
    files.ensure_dirs()
    db = Database(files.db_path)
    db.migrate()
    build(db, files)
    db.dispose()


def test_startup_recovery(
    make_config: Callable[..., AppConfig],
    fixtures_dir: Path,
    fake_engine: None,
) -> None:
    config = make_config()
    # first run: a real, ready document
    with TestClient(create_app(config)) as client:
        ready = ingest(client, fixtures_dir / "a1_landscape.pdf")
        assert ready["status"] == "ready"
    files = Files(config)
    settings = JobSettings.model_validate(job_body(ready["id"])).model_dump(mode="json")
    ids = {k: new_id() for k in ("running", "queued", "ingest_doc", "ingest_job", "lost_doc")}

    def build(db: Database, files: Files) -> None:
        with db.session() as s:
            now_doc = repo.create_document(
                s, config, doc_id=ids["ingest_doc"], name="again.pdf", size_bytes=1, sha256="0" * 64
            )
            assert now_doc.status == m.DOC_PROCESSING
            repo.create_document(
                s, config, doc_id=ids["lost_doc"], name="lost.pdf", size_bytes=1, sha256="0" * 64
            )
            s.flush()
            for key, status in (("running", m.JOB_RUNNING), ("queued", m.JOB_QUEUED)):
                job = repo.create_job(
                    s,
                    config,
                    job_id=ids[key],
                    document_id=ready["id"],
                    kind=m.KIND_RENDER,
                    settings=settings,
                    options={},
                )
                job.status = status
            ingest_job = repo.create_job(
                s,
                config,
                job_id=ids["ingest_job"],
                document_id=ids["ingest_doc"],
                kind=m.KIND_INGEST,
            )
            ingest_job.status = m.JOB_QUEUED
        # the second document re-uses the uploaded file of the first one
        files.doc_dir(ids["ingest_doc"]).mkdir(parents=True)
        original = files.find_original(ready["id"])
        assert original is not None
        shutil.copy(original, files.doc_dir(ids["ingest_doc"]) / original.name)
        files.doc_dir(ids["lost_doc"]).mkdir(parents=True)
        # stale temp dir of a job that died with the server
        (config.tmp_dir / ids["running"]).mkdir(parents=True)
        (config.tmp_dir / ids["running"] / "junk").write_text("x")

    _insert(config, build)
    assert files.find_original(ready["id"]) is not None

    with TestClient(create_app(config)) as client:
        interrupted = client.get(f"/api/v1/jobs/{ids['running']}").json()
        assert interrupted["status"] == "failed"
        assert interrupted["error_code"] == "INTERRUPTED"
        assert not (config.tmp_dir / ids["running"]).exists()

        queued = wait_job(client, ids["queued"])
        assert queued["status"] == "succeeded", queued

        doc = wait_for(
            lambda: client.get(f"/api/v1/documents/{ids['ingest_doc']}").json(),
            lambda d: d["status"] in ("ready", "failed"),
        )
        assert doc["status"] == "ready", doc
        assert doc["job_id"] == ids["ingest_job"]

        lost = client.get(f"/api/v1/documents/{ids['lost_doc']}").json()
        assert lost["status"] == "failed"
        assert lost["error_code"] == "INTERRUPTED"
