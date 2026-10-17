"""App preferences (PRE-05): language, units, default printer and paper, retention."""

import os
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from a0td.api.deps import Services, ServicesDep
from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.papers import PaperId
from a0td.storage import repo

router = APIRouter(prefix="/api/v1", tags=["settings"])

PREFS_KEY = "preferences"
RETENTION_KEY = "retention_hours"
RETENTION_ENV = "A0TD_RETENTION_HOURS"


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: Literal["es", "en"] | None = None
    units: Literal["mm", "cm", "in"] | None = None
    default_printer_id: str | None = Field(default=None, max_length=64)
    default_paper: PaperId | None = None


class AppSettingsIn(Preferences):
    retention_hours: int | None = Field(default=None, ge=1, le=24 * 365)


class AppSettingsOut(Preferences):
    retention_hours: int
    # True when the environment fixes the retention: the API refuses to change it.
    retention_locked: bool
    default_locale: str


def retention_locked() -> bool:
    return bool(os.environ.get(RETENTION_ENV, "").strip())


def apply_stored_retention(svc: Services) -> None:
    """At startup: a retention saved from the UI applies unless the environment sets it."""
    if retention_locked():
        return
    with svc.db.session() as s:
        stored = repo.get_app_setting(s, RETENTION_KEY)
    if isinstance(stored, int) and stored >= 1:
        svc.config.retention_hours = stored


def _read(svc: Services) -> AppSettingsOut:
    with svc.db.session() as s:
        raw = repo.get_app_setting(s, PREFS_KEY, {})
    try:
        prefs = Preferences.model_validate(raw or {})
    except ValueError:
        prefs = Preferences()
    return AppSettingsOut(
        **prefs.model_dump(),
        retention_hours=svc.config.retention_hours,
        retention_locked=retention_locked(),
        default_locale=svc.config.default_locale,
    )


@router.get("/settings", response_model=AppSettingsOut)
def get_settings(svc: ServicesDep) -> AppSettingsOut:
    return _read(svc)


@router.put("/settings", response_model=AppSettingsOut)
def put_settings(body: AppSettingsIn, svc: ServicesDep) -> AppSettingsOut:
    if body.retention_hours is not None and retention_locked():
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            f"retention is fixed by {RETENTION_ENV}",
            errors=[
                {"field": "retention_hours", "message": "read-only (set by env)", "type": "locked"}
            ],
        )
    with svc.db.session() as s:
        if (
            body.default_printer_id is not None
            and repo.get_printer(s, body.default_printer_id) is None
        ):
            raise DomainError(
                ErrorCode.INVALID_SETTINGS,
                "unknown printer profile",
                errors=[{"field": "default_printer_id", "message": "not found", "type": "missing"}],
            )
        repo.set_app_setting(
            s, PREFS_KEY, body.model_dump(mode="json", exclude={"retention_hours"})
        )
        if body.retention_hours is not None:
            repo.set_app_setting(s, RETENTION_KEY, body.retention_hours)
    if body.retention_hours is not None:
        svc.config.retention_hours = body.retention_hours
    return _read(svc)
