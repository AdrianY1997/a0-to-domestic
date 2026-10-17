"""SQLAlchemy 2 models (SPEC 11.1).

JSON columns are stored as text (``*_json``) and handled with :func:`dumps` / :func:`loads`.
Datetimes are timezone-aware UTC in Python and naive UTC in SQLite.
"""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as naive UTC and returns them aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


class Base(DeclarativeBase):
    pass


# Document statuses.
DOC_PROCESSING = "processing"
DOC_PASSWORD_REQUIRED = "password_required"
DOC_READY = "ready"
DOC_FAILED = "failed"

# Job kinds and statuses.
KIND_INGEST = "ingest"
KIND_RENDER = "render"
KIND_PRINT = "print"
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"
ACTIVE_JOB_STATUSES = (JOB_QUEUED, JOB_RUNNING)
FINISHED_JOB_STATUSES = (JOB_SUCCEEDED, JOB_FAILED, JOB_CANCELLED)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(Text)  # display only; never used in paths
    format: Mapped[str | None] = mapped_column(String(32), default=None)
    mime: Mapped[str | None] = mapped_column(String(128), default=None)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    # True once a password was supplied or required: such documents are never reused (ING-10).
    was_encrypted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    status: Mapped[str] = mapped_column(String(24), default=DOC_PROCESSING, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_detail: Mapped[str | None] = mapped_column(Text, default=None)
    error_params_json: Mapped[str | None] = mapped_column(Text, default=None)
    pages_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_document_kind", "document_id", "kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=JOB_QUEUED, index=True)
    stage: Mapped[str | None] = mapped_column(String(64), default=None)
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # percent, 0-100
    settings_json: Mapped[str | None] = mapped_column(Text, default=None)
    options_json: Mapped[str | None] = mapped_column(Text, default=None)  # test_sheet_only, ...
    plans_json: Mapped[str | None] = mapped_column(Text, default=None)
    outputs_json: Mapped[str | None] = mapped_column(Text, default=None)
    warnings_json: Mapped[str | None] = mapped_column(Text, default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_detail: Mapped[str | None] = mapped_column(Text, default=None)
    error_params_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class Printer(Base):
    """User printer profiles (F2). Built-in profiles live in code (``domain.settings``)."""

    __tablename__ = "printers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="generic")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    papers_json: Mapped[str] = mapped_column(Text, default="{}")
    default_margins_json: Mapped[str] = mapped_column(Text, default="{}")
    color: Mapped[bool] = mapped_column(Boolean, default=True)
    scale_x: Mapped[float] = mapped_column(Float, default=1.0)
    scale_y: Mapped[float] = mapped_column(Float, default=1.0)
    ipp_uri: Mapped[str | None] = mapped_column(Text, default=None)
    defaults_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Preset(Base):
    """User presets (F2). Built-in presets live in code (``domain.settings``)."""

    __tablename__ = "presets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="null")
