"""Health, readiness, info and the manual storage purge."""

import asyncio
import os
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from a0td.api.deps import Services, ServicesDep
from a0td.api.schemas import PurgeOut, PurgeRequest
from a0td.config import AppConfig
from a0td.ingest import default_registry
from a0td.storage import repo
from a0td.storage.retention import purge
from a0td.version import __version__

router = APIRouter()


def app_version() -> str:
    """``A0TD_VERSION`` (set at image build time) wins over ``version.py``."""
    return os.environ.get("A0TD_VERSION") or __version__


def _limits(config: AppConfig) -> dict[str, int]:
    return {
        "max_upload_mb": config.max_upload_mb,
        "max_pages": config.max_pages,
        "max_megapixels": config.max_megapixels,
        "max_page_mm": config.max_page_mm,
        "max_sheets": config.max_sheets,
        "preview_max_px": config.preview_max_px,
        "workers": config.workers,
        "retention_hours": config.retention_hours,
    }


@router.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["system"])
def readyz(svc: ServicesDep) -> JSONResponse:
    """Ready when the database answers and the data directory is writable."""
    checks = {"database": svc.db.check(), "storage": svc.files.writable()}
    ok = all(checks.values())
    return JSONResponse(
        {"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503
    )


@router.get("/api/v1/info", tags=["system"])
def info(svc: ServicesDep) -> dict[str, Any]:
    config = svc.config
    return {
        "version": app_version(),
        "limits": _limits(config),
        "converters": default_registry().info(),
        "auth_mode": config.auth_mode,
        "default_locale": config.default_locale,
        "ipp_enabled": config.enable_ipp,
    }


def _active_jobs(svc: Services) -> list[str]:
    with svc.db.session() as s:
        return repo.active_job_ids(s)


@router.post("/api/v1/storage/purge", tags=["system"], response_model=PurgeOut)
async def purge_storage(body: PurgeRequest, svc: ServicesDep) -> PurgeOut:
    """Delete expired documents and jobs now, or everything with ``scope=all`` ("Borrar ahora")."""
    everything = body.scope == "all"
    if everything:
        active = await asyncio.to_thread(_active_jobs, svc)
        for job_id in active:
            await svc.manager.cancel(job_id, wait=True)
    result = await asyncio.to_thread(purge, svc.db, svc.files, everything=everything)
    return PurgeOut(documents=result.documents, jobs=result.jobs, orphans=result.orphans)
