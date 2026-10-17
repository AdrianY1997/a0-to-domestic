"""Small data-access helpers shared by the API, the job manager and retention."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from a0td.config import AppConfig
from a0td.domain.settings import (
    BUILTIN_PRESETS,
    BUILTIN_PRINTERS,
    Preset,
    PrinterProfile,
    get_builtin_preset,
    get_builtin_printer,
)
from a0td.storage import models as m
from a0td.storage.models import dumps, loads, utcnow


def create_document(
    session: Session, config: AppConfig, *, doc_id: str, name: str, size_bytes: int, sha256: str
) -> m.Document:
    now = utcnow()
    doc = m.Document(
        id=doc_id,
        name=name,
        size_bytes=size_bytes,
        sha256=sha256,
        status=m.DOC_PROCESSING,
        created_at=now,
        expires_at=now + timedelta(hours=config.retention_hours),
    )
    session.add(doc)
    return doc


def create_job(
    session: Session,
    config: AppConfig,
    *,
    job_id: str,
    document_id: str,
    kind: str,
    settings: dict[str, object] | None = None,
    options: dict[str, object] | None = None,
) -> m.Job:
    """Insert a queued job and keep its document alive at least as long as the job."""
    now = utcnow()
    job = m.Job(
        id=job_id,
        document_id=document_id,
        kind=kind,
        status=m.JOB_QUEUED,
        settings_json=dumps(settings) if settings is not None else None,
        options_json=dumps(options) if options is not None else None,
        created_at=now,
        expires_at=now + timedelta(hours=config.retention_hours),
    )
    session.add(job)
    doc = session.get(m.Document, document_id)
    if doc is not None and doc.expires_at < job.expires_at:
        doc.expires_at = job.expires_at
    return job


def latest_ingest_job(session: Session, doc_id: str) -> m.Job | None:
    stmt = (
        select(m.Job)
        .where(m.Job.document_id == doc_id, m.Job.kind == m.KIND_INGEST)
        .order_by(m.Job.created_at.desc(), m.Job.id)
        .limit(1)
    )
    return session.scalars(stmt).first()


def active_job_ids(session: Session, doc_id: str | None = None) -> list[str]:
    stmt = select(m.Job.id).where(m.Job.status.in_(m.ACTIVE_JOB_STATUSES))
    if doc_id is not None:
        stmt = stmt.where(m.Job.document_id == doc_id)
    return list(session.scalars(stmt))


# ------------------------------------------------------------------ printers & presets


_BORDERLESS_KEY = "__borderless"  # extra profile fields kept inside ``defaults_json``
_SUPPORTED_KEY = "__supported"


def _printer_from_row(row: m.Printer) -> PrinterProfile:
    papers = loads(row.papers_json, {}) or {}
    defaults = dict(loads(row.defaults_json, {}) or {})
    borderless = bool(defaults.pop(_BORDERLESS_KEY, False))
    supported = defaults.pop(_SUPPORTED_KEY, None) or list(papers)
    data: dict[str, object] = {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "builtin": row.builtin,
        "paper_margins": papers,
        "default_margins": loads(row.default_margins_json, {}) or {},
        "borderless": borderless,
        "color": row.color,
        "scale_kx": row.scale_x,
        "scale_ky": row.scale_y,
        "ipp_uri": row.ipp_uri,
        "defaults": defaults,
    }
    if supported:
        data["supported_papers"] = supported
    return PrinterProfile.model_validate(data)


def _fill_printer_row(row: m.Printer, p: PrinterProfile) -> None:
    row.name = p.name
    row.kind = p.kind.value
    row.builtin = False
    row.papers_json = dumps({k.value: v.model_dump() for k, v in p.paper_margins.items()})
    row.default_margins_json = dumps(p.default_margins.model_dump())
    row.color = p.color
    row.scale_x = p.scale_kx
    row.scale_y = p.scale_ky
    row.ipp_uri = p.ipp_uri
    row.defaults_json = dumps(
        {
            **p.defaults,
            _BORDERLESS_KEY: p.borderless,
            _SUPPORTED_KEY: [x.value for x in p.supported_papers],
        }
    )


def save_printer(session: Session, profile: PrinterProfile) -> PrinterProfile:
    """Insert or update a user profile (built-in ids are the caller's business)."""
    row = session.get(m.Printer, profile.id)
    if row is None:
        row = m.Printer(id=profile.id, name=profile.name)
        session.add(row)
    _fill_printer_row(row, profile)
    session.flush()
    return _printer_from_row(row)


def delete_printer(session: Session, printer_id: str) -> bool:
    row = session.get(m.Printer, printer_id)
    if row is None:
        return False
    session.delete(row)
    return True


def get_printer(session: Session, printer_id: str) -> PrinterProfile | None:
    """Built-in profile first, then a user profile from the database."""
    builtin = get_builtin_printer(printer_id)
    if builtin is not None:
        return builtin
    row = session.get(m.Printer, printer_id)
    if row is None:
        return None
    try:
        return _printer_from_row(row)
    except ValueError:
        return None


def list_printers(session: Session) -> list[PrinterProfile]:
    out = list(BUILTIN_PRINTERS)
    for row in session.scalars(select(m.Printer).order_by(m.Printer.name)):
        try:
            out.append(_printer_from_row(row))
        except ValueError:
            continue
    return out


_DESCRIPTION_KEY = "__description"  # the presets table has no description column


def _preset_from_row(row: m.Preset) -> Preset:
    settings = dict(loads(row.settings_json, {}) or {})
    description = settings.pop(_DESCRIPTION_KEY, "")
    return Preset(
        id=row.id,
        name=row.name,
        builtin=row.builtin,
        description=description if isinstance(description, str) else "",
        settings=settings,
    )


def save_preset(session: Session, preset: Preset) -> Preset:
    """Insert or update a user preset."""
    row = session.get(m.Preset, preset.id)
    if row is None:
        row = m.Preset(id=preset.id, name=preset.name)
        session.add(row)
    row.name = preset.name
    row.builtin = False
    row.settings_json = dumps({**preset.settings, _DESCRIPTION_KEY: preset.description})
    session.flush()
    return _preset_from_row(row)


def delete_preset(session: Session, preset_id: str) -> bool:
    row = session.get(m.Preset, preset_id)
    if row is None:
        return False
    session.delete(row)
    return True


# ----------------------------------------------------------------------- app settings


def get_app_setting(session: Session, key: str, default: object = None) -> object:
    row = session.get(m.AppSetting, key)
    return loads(row.value_json, default) if row is not None else default


def set_app_setting(session: Session, key: str, value: object) -> None:
    row = session.get(m.AppSetting, key)
    if row is None:
        session.add(m.AppSetting(key=key, value_json=dumps(value)))
    else:
        row.value_json = dumps(value)


def get_preset(session: Session, preset_id: str) -> Preset | None:
    builtin = get_builtin_preset(preset_id)
    if builtin is not None:
        return builtin
    row = session.get(m.Preset, preset_id)
    return _preset_from_row(row) if row is not None else None


def list_presets(session: Session) -> list[Preset]:
    out = list(BUILTIN_PRESETS)
    out.extend(
        _preset_from_row(r) for r in session.scalars(select(m.Preset).order_by(m.Preset.name))
    )
    return out
