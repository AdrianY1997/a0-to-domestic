"""Presets: built-ins are read-only, user presets are CRUD plus JSON export/import (PRE-02)."""

import json
import uuid
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from a0td.api.deps import ServicesDep
from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.settings import PartialSettings, Preset, merge_settings
from a0td.storage import repo

router = APIRouter(prefix="/api/v1", tags=["presets"])

EXPORT_FORMAT = "a0td-preset"
EXPORT_VERSION = 1


def _check_partial(value: PartialSettings) -> PartialSettings:
    """A preset is a partial ``JobSettings``: it must merge into valid settings."""
    cleaned = {k: v for k, v in value.items() if k != "document_id"}
    try:
        merge_settings(cleaned)
    except ValidationError as exc:
        raise ValueError(f"invalid preset settings: {exc.errors()[0]['msg']}") from exc
    return cleaned


class PresetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    settings: PartialSettings = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("settings")
    @classmethod
    def _valid_settings(cls, v: PartialSettings) -> PartialSettings:
        return _check_partial(v)


class PresetPatch(BaseModel):
    """PUT body: every field optional, so renaming does not need the settings."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    settings: PartialSettings | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("settings")
    @classmethod
    def _valid_settings(cls, v: PartialSettings | None) -> PartialSettings | None:
        return None if v is None else _check_partial(v)


class PresetImport(PresetIn):
    """The export file; ``format`` and ``version`` are checked."""

    format: str = EXPORT_FORMAT
    version: int = EXPORT_VERSION

    @field_validator("format")
    @classmethod
    def _format(cls, v: str) -> str:
        if v != EXPORT_FORMAT:
            raise ValueError(f"unsupported file format {v!r}")
        return v

    @field_validator("version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != EXPORT_VERSION:
            raise ValueError(f"unsupported version {v}")
        return v


def _get(svc: ServicesDep, preset_id: str) -> Preset:
    with svc.db.session() as s:
        preset = repo.get_preset(s, preset_id)
    if preset is None:
        raise DomainError(ErrorCode.NOT_FOUND, "preset not found")
    return preset


def _editable(svc: ServicesDep, preset_id: str) -> Preset:
    preset = _get(svc, preset_id)
    if preset.builtin:
        raise DomainError(ErrorCode.PRESET_READONLY, "built-in presets cannot be modified")
    return preset


def _new_preset(body: PresetIn, svc: ServicesDep) -> Preset:
    preset = Preset(
        id=f"user-{uuid.uuid4().hex[:12]}",
        name=body.name,
        description=body.description,
        settings=body.settings,
    )
    with svc.db.session() as s:
        return repo.save_preset(s, preset)


@router.get("/presets", response_model=list[Preset])
def list_presets(svc: ServicesDep) -> list[Preset]:
    with svc.db.session() as s:
        return repo.list_presets(s)


@router.post("/presets", response_model=Preset, status_code=201)
def create_preset(body: PresetIn, svc: ServicesDep) -> Preset:
    return _new_preset(body, svc)


@router.post("/presets/import", response_model=Preset, status_code=201)
def import_preset(body: PresetImport, svc: ServicesDep) -> Preset:
    """Create a user preset from an exported file (always a new id; never overwrites)."""
    return _new_preset(PresetIn(**body.model_dump(exclude={"format", "version"})), svc)


@router.get("/presets/{preset_id}", response_model=Preset)
def get_preset(preset_id: str, svc: ServicesDep) -> Preset:
    return _get(svc, preset_id)


@router.get("/presets/{preset_id}/export")
def export_preset(preset_id: str, svc: ServicesDep) -> Response:
    preset = _get(svc, preset_id)
    payload: dict[str, Any] = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "name": preset.name,
        "description": preset.description,
        "settings": preset.settings,
    }
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{preset.id}.a0td-preset.json"'},
    )


@router.put("/presets/{preset_id}", response_model=Preset)
def update_preset(preset_id: str, body: PresetPatch, svc: ServicesDep) -> Preset:
    current = _editable(svc, preset_id)
    updated = current.model_copy(
        update={
            "name": body.name if body.name is not None else current.name,
            "description": (
                body.description if body.description is not None else current.description
            ),
            "settings": body.settings if body.settings is not None else current.settings,
        }
    )
    with svc.db.session() as s:
        return repo.save_preset(s, updated)


@router.delete("/presets/{preset_id}", status_code=204)
def delete_preset(preset_id: str, svc: ServicesDep) -> Response:
    _editable(svc, preset_id)
    with svc.db.session() as s:
        repo.delete_preset(s, preset_id)
    return Response(status_code=204)
