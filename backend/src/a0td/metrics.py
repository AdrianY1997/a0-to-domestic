"""Prometheus metrics (SPEC ADM-04), only when ``A0TD_METRICS=true``.

* ``a0td_jobs{kind,status}``: jobs currently stored (read from the database at scrape time).
* ``a0td_queue_depth`` / ``a0td_jobs_running``: queued and running jobs.
* ``a0td_documents{status}``: stored documents.
* ``a0td_jobs_finished_total{kind,status}`` and ``a0td_job_duration_seconds{kind,status}``
  (histogram): observed by the job manager when a job reaches a final status.

``/metrics`` lives outside ``/api/v1`` but ``AuthMiddleware`` guards it like the API: with
``A0TD_AUTH_MODE`` ``token`` the scraper sends ``Authorization: Bearer <token>``; with
``password`` it needs a session cookie. With ``none`` it is open (counts only): keep it on a
private network, or block the path in the reverse proxy.
"""

from collections.abc import Iterable

from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from sqlalchemy import func, select

from a0td.api.deps import Services
from a0td.storage import models as m

KINDS = (m.KIND_INGEST, m.KIND_RENDER, m.KIND_PRINT)
STATUSES = (m.JOB_QUEUED, m.JOB_RUNNING, m.JOB_SUCCEEDED, m.JOB_FAILED, m.JOB_CANCELLED)
DOC_STATUSES = (m.DOC_PROCESSING, m.DOC_PASSWORD_REQUIRED, m.DOC_READY, m.DOC_FAILED)
DURATION_BUCKETS = (0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1800)


class _DatabaseCollector(Collector):
    """Gauges computed from the database on every scrape."""

    def __init__(self, services: Services) -> None:
        self._svc = services

    def collect(self) -> Iterable[Metric]:
        with self._svc.db.session() as s:
            jobs = {
                (kind, status): n
                for kind, status, n in s.execute(
                    select(m.Job.kind, m.Job.status, func.count()).group_by(
                        m.Job.kind, m.Job.status
                    )
                )
            }
            docs = dict(
                s.execute(select(m.Document.status, func.count()).group_by(m.Document.status)).all()
            )
        by_kind = GaugeMetricFamily(
            "a0td_jobs", "Jobs currently stored, by kind and status", labels=["kind", "status"]
        )
        for kind in KINDS:
            for status in STATUSES:
                by_kind.add_metric([kind, status], jobs.get((kind, status), 0))
        yield by_kind
        queue = GaugeMetricFamily("a0td_queue_depth", "Jobs waiting for a worker")
        queue.add_metric([], sum(n for (_, st), n in jobs.items() if st == m.JOB_QUEUED))
        yield queue
        running = GaugeMetricFamily("a0td_jobs_running", "Jobs being processed now")
        running.add_metric([], sum(n for (_, st), n in jobs.items() if st == m.JOB_RUNNING))
        yield running
        documents = GaugeMetricFamily(
            "a0td_documents", "Documents currently stored", labels=["status"]
        )
        for status in DOC_STATUSES:
            documents.add_metric([status], docs.get(status, 0))
        yield documents


class Metrics:
    """The registry plus the observers fed by the job manager."""

    def __init__(self, services: Services) -> None:
        self.registry = CollectorRegistry()
        self.finished = Counter(
            "a0td_jobs_finished",
            "Jobs that reached a final status since the process started",
            ["kind", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "a0td_job_duration_seconds",
            "Time from start to end of a job",
            ["kind", "status"],
            buckets=DURATION_BUCKETS,
            registry=self.registry,
        )
        self.registry.register(_DatabaseCollector(services))

    def observe_job(self, kind: str, status: str, seconds: float | None) -> None:
        self.finished.labels(kind, status).inc()
        if seconds is not None:
            self.duration.labels(kind, status).observe(seconds)

    def render(self) -> bytes:
        return generate_latest(self.registry)


def install_metrics(app: FastAPI, services: Services) -> Metrics:
    """Create the metrics, hook them to the job manager and add ``GET /metrics``."""
    metrics = Metrics(services)
    services.manager.metrics = metrics

    def scrape() -> Response:
        return Response(metrics.render(), media_type=CONTENT_TYPE_LATEST)

    app.add_api_route("/metrics", scrape, methods=["GET"], include_in_schema=False)
    app.state.metrics = metrics
    return metrics
