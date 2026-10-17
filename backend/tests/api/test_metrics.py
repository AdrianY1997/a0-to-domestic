"""Prometheus metrics (ADM-04)."""

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from apihelpers import ingest, job_body, wait_job
from fastapi.testclient import TestClient

from a0td.api.app import create_app
from a0td.config import AppConfig

pytest.importorskip("prometheus_client")


def _value(text: str, name: str, **labels: str) -> float:
    pairs = ",".join(f'{k}="{v}"' for k, v in labels.items())
    pattern = (
        rf"^{re.escape(name)}{{?{re.escape(pairs)}}}? ([0-9.e+-]+)$"
        if labels
        else (rf"^{re.escape(name)} ([0-9.e+-]+)$")
    )
    match = re.search(pattern, text, re.MULTILINE)
    assert match, f"{name}{labels} not in:\n{text}"
    return float(match.group(1))


def test_metrics_are_off_by_default(client: TestClient) -> None:
    assert client.get("/metrics").status_code == 404


def test_metrics_after_a_render_job(
    make_config: Callable[..., AppConfig], fake_engine: None, fixtures_dir: Path
) -> None:
    with TestClient(create_app(make_config(metrics=True))) as client:
        idle = client.get("/metrics")
        assert idle.status_code == 200
        assert idle.headers["content-type"].startswith("text/plain")
        assert _value(idle.text, "a0td_queue_depth") == 0
        assert _value(idle.text, "a0td_documents", status="ready") == 0

        doc = ingest(client, fixtures_dir / "a0_vector_grid.pdf")
        r = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])})
        job = wait_job(client, r.json()["id"])
        assert job["status"] == "succeeded"

        text = client.get("/metrics").text
        assert _value(text, "a0td_documents", status="ready") == 1
        assert _value(text, "a0td_jobs", kind="render", status="succeeded") == 1
        assert _value(text, "a0td_jobs", kind="ingest", status="succeeded") == 1
        assert _value(text, "a0td_jobs_finished_total", kind="render", status="succeeded") == 1
        assert (
            _value(text, "a0td_job_duration_seconds_count", kind="render", status="succeeded") == 1
        )
        assert (
            'a0td_job_duration_seconds_bucket{kind="render",le="+Inf",status="succeeded"}' in text
        )
        assert _value(text, "a0td_queue_depth") == 0


def test_failed_jobs_are_counted(
    make_config: Callable[..., AppConfig],
    fake_engine: None,
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_RENDER_MODE", "fail")
    with TestClient(create_app(make_config(metrics=True))) as client:
        doc = ingest(client, fixtures_dir / "a0_vector_grid.pdf")
        r = client.post("/api/v1/jobs", json={"settings": job_body(doc["id"])})
        assert wait_job(client, r.json()["id"])["status"] == "failed"
        text = client.get("/metrics").text
        assert _value(text, "a0td_jobs_finished_total", kind="render", status="failed") == 1
        assert _value(text, "a0td_jobs", kind="render", status="failed") == 1


def test_metrics_requires_auth_when_auth_is_enabled(make_config: Callable[..., AppConfig]) -> None:
    cfg = make_config(metrics=True, auth_mode="token", token="tok-123")
    with TestClient(create_app(cfg)) as c:
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer no"}).status_code == 401
        ok = c.get("/metrics", headers={"Authorization": "Bearer tok-123"})
        assert ok.status_code == 200
        assert "a0td_queue_depth" in ok.text
        assert c.get("/healthz").status_code == 200
