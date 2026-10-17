"""Read-only paper catalog (printers and presets have their own routers)."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from a0td.domain.papers import list_papers

router = APIRouter(prefix="/api/v1")


@router.get("/papers", tags=["papers"])
def papers() -> list[dict[str, Any]]:
    return [asdict(p) for p in list_papers()]
