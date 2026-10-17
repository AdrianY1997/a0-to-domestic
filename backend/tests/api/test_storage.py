"""Storage: file layout, migrations, retention and system endpoints."""

import os
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path, PureWindowsPath

import pytest
from apihelpers import ingest, job_body, wait_job
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from a0td.api.app import create_app
from a0td.api.routers.jobs import content_disposition
from a0td.config import AppConfig
from a0td.domain.errors import DomainError
from a0td.domain.settings import JobSettings
from a0td.jobs.exact_scale import is_exact_scale
from a0td.storage import models as m
from a0td.storage import repo
from a0td.storage.db import Database
from a0td.storage.files import Files, display_name, new_id, safe_ext, safe_file_name
from a0td.storage.models import utcnow
from a0td.storage.retention import purge

BACKEND_DIR = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------- files


def test_safe_names() -> None:
    assert safe_ext("plano.PDF") == "pdf"
    assert safe_ext("..\\..\\x.tar.gz") == "gz"
    assert safe_ext("noext") == "bin"
    assert safe_ext("x.p/d f") == "bin"
    assert safe_ext("x." + "a" * 20) == "bin"
    assert safe_ext(None) == "bin"
    assert display_name("C:\\a\\b\\plano.pdf") == "plano.pdf"
    assert display_name("../../x\r\n.pdf") == "x.pdf"
    assert display_name("") == "document"
    assert safe_file_name('a<b>:"c"|d?.pdf') == "a_b___c__d_.pdf"
    assert safe_file_name("../evil/x.pdf") == "x.pdf"
    assert safe_file_name("..") == "output"


def test_files_layout_and_confinement(config: AppConfig) -> None:
    files = Files(config)
    doc_id, job_id = new_id(), new_id()
    assert files.db_path == config.data_dir / "app.db"
    assert files.doc_dir(doc_id) == config.data_dir / "documents" / doc_id
    assert files.page_dir(doc_id, 3) == config.data_dir / "documents" / doc_id / "pages" / "3"
    assert files.output_dir(job_id) == config.data_dir / "jobs" / job_id / "output"
    assert files.log_path(job_id).name == "log.jsonl"
    assert files.job_tmp(job_id) == config.tmp_dir / job_id
    assert files.original_path(doc_id, "pdf").name == "original.pdf"
    assert files.original_path(doc_id, "../x").name == "original.bin"
    for bad in ("..", "../x", "a/b", "C:\\x", "", "not-a-uuid"):
        with pytest.raises(DomainError):
            files.doc_dir(bad)
        with pytest.raises(DomainError):
            files.job_dir(bad)
    files.ensure_dirs()
    for bad in ("..", "../x", "a/b", "a\\b", "", ".", "x:y"):
        with pytest.raises(DomainError):
            files.output_path(job_id, bad)
    assert files.output_path(job_id, "ok.pdf").parent == files.output_dir(job_id)
    assert files.writable()


def test_windows_style_paths_stay_inside() -> None:
    # What a Windows client may send as a "file name" never survives as a path component.
    for raw in ("C:\\Users\\x\\..\\..\\secret.pdf", "\\\\server\\share\\a.pdf", "CON", "a:b.pdf"):
        name = display_name(raw)
        assert "\\" not in name
        assert "/" not in name
        assert PureWindowsPath(safe_file_name(raw)).name == safe_file_name(raw)
        assert ":" not in safe_file_name(raw)


def test_content_disposition_is_header_safe() -> None:
    header = content_disposition("attachment", 'plan "A"\r\nSet-Cookie: x=1;ñ.pdf')
    assert "\r" not in header
    assert "\n" not in header
    assert header.startswith('attachment; filename="plan _A_Set-Cookie: x=1__.pdf"')
    assert "filename*=UTF-8''" in header
    assert "%C3%B1" in header
    assert "%22A%22" in header
    assert content_disposition("inline", "")  # never empty


# ------------------------------------------------------------------------ migrations


def test_migrations_create_schema_and_are_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "x" / "app.db")
    db.migrate()
    db.migrate()
    tables = set(inspect(db.engine).get_table_names())
    assert {"documents", "jobs", "printers", "presets", "app_settings", "alembic_version"} <= tables
    with db.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    assert db.check()
    db.dispose()


def test_alembic_cli_uses_the_data_dir(tmp_path: Path) -> None:
    """The entrypoint runs ``alembic upgrade head`` from backend/ with A0TD_DATA_DIR set."""
    env = {**os.environ, "A0TD_DATA_DIR": str(tmp_path / "d")}
    for _ in range(2):  # twice: idempotent
        done = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert done.returncode == 0, done.stderr
    db = Database(tmp_path / "d" / "app.db")
    assert "jobs" in inspect(db.engine).get_table_names()
    db.dispose()


# -------------------------------------------------------------------------- retention


def _seed(
    config: AppConfig, *, hours_ago: float, job_status: str = m.JOB_SUCCEEDED
) -> tuple[Database, Files, str, str]:
    files = Files(config)
    files.ensure_dirs()
    db = Database(files.db_path)
    db.migrate()
    doc_id, job_id = new_id(), new_id()
    stamp = utcnow() - timedelta(hours=hours_ago)
    with db.session() as s:
        doc = repo.create_document(
            s, config, doc_id=doc_id, name="d.pdf", size_bytes=1, sha256="0" * 64
        )
        doc.status = m.DOC_READY
        doc.created_at, doc.expires_at = stamp, stamp + timedelta(hours=config.retention_hours)
        s.flush()
        job = repo.create_job(s, config, job_id=job_id, document_id=doc_id, kind=m.KIND_RENDER)
        job.status = job_status
        job.created_at, job.expires_at = stamp, stamp + timedelta(hours=config.retention_hours)
        doc.expires_at = stamp + timedelta(hours=config.retention_hours)
    files.doc_dir(doc_id).mkdir(parents=True)
    (files.doc_dir(doc_id) / "original.pdf").write_bytes(b"x")
    files.output_dir(job_id).mkdir(parents=True)
    (files.output_dir(job_id) / "o.pdf").write_bytes(b"x")
    files.job_tmp(job_id).mkdir(parents=True)
    return db, files, doc_id, job_id


def test_purge_removes_only_expired(config: AppConfig) -> None:
    db, files, old_doc, old_job = _seed(config, hours_ago=100)  # retention is 72 h
    fresh = _seed(config, hours_ago=1)
    result = purge(db, files)
    assert (result.documents, result.jobs) == (1, 1)
    assert not files.doc_dir(old_doc).exists()
    assert not files.job_dir(old_job).exists()
    assert not files.job_tmp(old_job).exists()
    assert files.doc_dir(fresh[2]).exists()
    assert files.job_dir(fresh[3]).exists()
    with db.session() as s:
        assert s.get(m.Document, old_doc) is None
        assert s.get(m.Job, old_job) is None
        assert s.get(m.Document, fresh[2]) is not None
    db.dispose()


def test_purge_keeps_documents_with_active_jobs_and_sweeps_orphans(config: AppConfig) -> None:
    db, files, doc_id, job_id = _seed(config, hours_ago=100, job_status=m.JOB_RUNNING)
    old_orphan, young_orphan = new_id(), new_id()
    for orphan in (old_orphan, young_orphan):
        files.doc_dir(orphan).mkdir(parents=True)
    long_ago = time.time() - 7200
    os.utime(files.doc_dir(old_orphan), (long_ago, long_ago))
    result = purge(db, files)
    assert (result.documents, result.jobs, result.orphans) == (0, 0, 1)
    assert files.doc_dir(doc_id).exists()
    assert files.job_dir(job_id).exists()
    assert not files.doc_dir(old_orphan).exists()
    assert files.doc_dir(young_orphan).exists()  # may be an upload in flight
    db.dispose()


def test_purge_everything_and_endpoint(
    client: TestClient, fixtures_dir: Path, config: AppConfig
) -> None:
    doc = ingest(client, fixtures_dir / "a1_landscape.pdf")
    job = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])}).json()
    wait_job(client, job["id"])
    r = client.post("/api/v1/storage/purge", json={"scope": "expired"})
    assert r.json() == {"documents": 0, "jobs": 0, "orphans": 0}
    assert client.get(f"/api/v1/documents/{doc['id']}").status_code == 200
    r = client.post("/api/v1/storage/purge", json={"scope": "all"})
    assert r.json()["documents"] == 1
    assert client.get(f"/api/v1/documents/{doc['id']}").status_code == 404
    assert client.get(f"/api/v1/jobs/{job['id']}").status_code == 404
    assert list((config.data_dir / "documents").iterdir()) == []
    assert list((config.data_dir / "jobs").iterdir()) == []
    assert client.post("/api/v1/storage/purge", json={"scope": "bogus"}).status_code == 422


def test_periodic_retention_runs_at_startup(
    make_config: Callable[..., AppConfig], fake_engine: None
) -> None:
    config = make_config()
    db, files, old_doc, _ = _seed(config, hours_ago=100)
    db.dispose()
    with TestClient(create_app(config)) as client:
        deadline = time.monotonic() + 10
        while files.doc_dir(old_doc).exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not files.doc_dir(old_doc).exists()
        assert client.get("/api/v1/documents").json() == []


def test_job_creation_extends_document_expiry(config: AppConfig) -> None:
    db, files, doc_id, _ = _seed(config, hours_ago=70)
    with db.session() as s:
        before = s.get(m.Document, doc_id)
        assert before is not None
        old_expiry = before.expires_at
        job = repo.create_job(s, config, job_id=new_id(), document_id=doc_id, kind=m.KIND_RENDER)
        assert before.expires_at == job.expires_at > old_expiry
    db.dispose()
    _ = files


# ---------------------------------------------------------------------------- system


def test_health_ready_info(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "checks": {"database": True, "storage": True}}
    monkeypatch.delenv("A0TD_VERSION", raising=False)
    info = client.get("/api/v1/info").json()
    from a0td.version import __version__

    assert info["version"] == __version__
    assert info["auth_mode"] == "none"
    assert info["limits"]["max_sheets"] == 500
    assert info["converters"]
    assert any("pdf" in c["formats"] for c in info["converters"])
    monkeypatch.setenv("A0TD_VERSION", "9.9.9-test")
    assert client.get("/api/v1/info").json()["version"] == "9.9.9-test"


def test_readyz_fails_without_storage(client: TestClient, config: AppConfig) -> None:
    import shutil

    client.app.state.services.db.dispose()  # type: ignore[attr-defined]
    shutil.rmtree(config.data_dir)
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"
    assert r.json()["checks"]["storage"] is False


def test_security_headers_on_every_response(client: TestClient) -> None:
    for url in ("/healthz", "/api/v1/papers", "/api/v1/nope", "/api/openapi.json"):
        h = client.get(url).headers
        assert "default-src 'self'" in h["content-security-policy"]
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
        assert h["x-frame-options"] == "DENY"
    assert "access-control-allow-origin" not in client.get("/api/v1/papers").headers
    docs = client.get("/api/docs")
    assert "cdn.jsdelivr.net" in docs.headers["content-security-policy"]


def test_exact_scale_helper() -> None:
    def make(**kw: object) -> JobSettings:
        return JobSettings.model_validate(kw)

    assert not is_exact_scale(make())
    assert is_exact_scale(make(tiling={"join": "butt"}))  # percent 100 + butt
    assert not is_exact_scale(
        make(tiling={"join": "butt"}, scale={"mode": "percent", "percent": 50})
    )
    assert not is_exact_scale(make(scale={"mode": "percent", "percent": 100}))
    assert is_exact_scale(make(), "builtin-sewing-a4")
    assert not is_exact_scale(make(), "builtin-real-a4")


def test_openapi_describes_the_f1_endpoints(client: TestClient) -> None:
    paths = client.get("/api/openapi.json").json()["paths"]
    expected = [
        "/api/v1/documents",
        "/api/v1/documents/{doc_id}",
        "/api/v1/documents/{doc_id}/password",
        "/api/v1/documents/{doc_id}/pages/{n}/physical-size",
        "/api/v1/documents/{doc_id}/pages/{n}/preview",
        "/api/v1/documents/{doc_id}/pages/{n}/thumb",
        "/api/v1/documents/{doc_id}/events",
        "/api/v1/plan",
        "/api/v1/plan/sheet-preview",
        "/api/v1/jobs",
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/events",
        "/api/v1/jobs/{job_id}/cancel",
        "/api/v1/jobs/{job_id}/outputs/{name}",
        "/api/v1/jobs/{job_id}/outputs/{name}/pages/{n}/thumb",
        "/api/v1/papers",
        "/api/v1/printers",
        "/api/v1/presets",
        "/api/v1/info",
        "/healthz",
        "/readyz",
    ]
    for path in expected:
        assert path in paths, path
    assert "multipart/form-data" in paths["/api/v1/documents"]["post"]["requestBody"]["content"]
