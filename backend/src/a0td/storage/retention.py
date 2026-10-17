"""Retention (PRE-04): periodic cleanup of expired documents and jobs, and "delete now"."""

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from a0td.storage import models as m
from a0td.storage.db import Database
from a0td.storage.files import Files, valid_id
from a0td.storage.models import utcnow

log = logging.getLogger(__name__)

ORPHAN_GRACE_S = 3600  # directories without a row younger than this may be an upload in flight


@dataclass(frozen=True, slots=True)
class PurgeResult:
    documents: int = 0
    jobs: int = 0
    orphans: int = 0


def purge(
    db: Database, files: Files, *, now: datetime | None = None, everything: bool = False
) -> PurgeResult:
    """Delete expired rows and their files.

    ``everything`` ignores expiry ("Borrar ahora"). Documents and jobs with a queued or running
    job are never touched: cancel those first. Directories left without a row (older than
    an hour) are removed as well.
    """
    now = now or utcnow()
    n_docs = n_jobs = 0
    with db.session() as s:
        active_docs = set(
            s.scalars(select(m.Job.document_id).where(m.Job.status.in_(m.ACTIVE_JOB_STATUSES)))
        )
        job_stmt = select(m.Job.id).where(m.Job.status.in_(m.FINISHED_JOB_STATUSES))
        if not everything:
            job_stmt = job_stmt.where(m.Job.expires_at <= now)
        for job_id in list(s.scalars(job_stmt)):
            job = s.get(m.Job, job_id)
            if job is None:
                continue
            s.delete(job)
            files.remove_job(job_id)
            n_jobs += 1
        s.flush()

        doc_stmt = select(m.Document.id)
        if not everything:
            doc_stmt = doc_stmt.where(m.Document.expires_at <= now)
        for doc_id in list(s.scalars(doc_stmt)):
            if doc_id in active_docs:
                continue
            for job_id in list(s.scalars(select(m.Job.id).where(m.Job.document_id == doc_id))):
                files.remove_job(job_id)
                n_jobs += 1
            doc = s.get(m.Document, doc_id)
            if doc is not None:
                s.delete(doc)  # jobs go with it (ON DELETE CASCADE / ORM below)
            files.remove_document(doc_id)
            n_docs += 1
        s.flush()
        known_docs = set(s.scalars(select(m.Document.id)))
        known_jobs = set(s.scalars(select(m.Job.id)))
    orphans = _sweep_orphans(files, known_docs, known_jobs)
    return PurgeResult(n_docs, n_jobs, orphans)


def _sweep_orphans(files: Files, known_docs: set[str], known_jobs: set[str]) -> int:
    limit = (
        ORPHAN_GRACE_S  # also for "delete now": a younger row-less dir may be an upload in flight
    )
    removed = 0
    now = time.time()
    for root, known in ((files.documents_dir, known_docs), (files.jobs_dir, known_jobs)):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir() or (valid_id(child.name) or child.name) in known:
                continue
            try:
                age = now - child.stat().st_mtime
            except OSError:
                continue
            if age >= limit:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
    return removed


async def retention_loop(db: Database, files: Files, *, interval_s: float = 600.0) -> None:
    """Run :func:`purge` now and then every ``interval_s`` seconds, forever."""
    while True:
        try:
            result = await asyncio.to_thread(purge, db, files)
            if result.documents or result.jobs or result.orphans:
                log.info(
                    "retention purge",
                    extra={"documents": result.documents, "jobs": result.jobs},
                )
        except Exception:
            log.exception("retention purge failed")
        await asyncio.sleep(interval_s)
