"""Runtime configuration, read from ``A0TD_*`` environment variables (SPEC 12.4)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """Process-wide configuration. The only global state allowed by the spec."""

    model_config = SettingsConfigDict(env_prefix="A0TD_", extra="ignore")

    port: int = Field(default=8080, ge=1, le=65535)
    data_dir: Path = Path("/data")
    tmp_dir: Path = Path("/data/tmp")
    max_upload_mb: int = Field(default=1024, ge=1)
    max_pages: int = Field(default=100, ge=1)
    max_megapixels: int = Field(default=1000, ge=1)
    max_page_mm: int = Field(default=10000, ge=1)
    max_sheets: int = Field(default=500, ge=1)
    workers: int = Field(default=2, ge=1)
    job_timeout_s: int = Field(default=600, ge=1)
    job_memory_mb: int = Field(default=3072, ge=64)
    retention_hours: int = Field(default=72, ge=1)
    preview_max_px: int = Field(default=4096, ge=256)
    auth_mode: Literal["none", "password", "token"] = "none"
    password_hash: str | None = None
    token: str | None = None  # bearer token; token mode is for API/CLI clients, not the SPA
    secret_key: str | None = None  # signs session cookies (>= 32 bytes); generated if unset
    # Comma-separated proxy IPs/CIDRs whose X-Forwarded-For/-Proto are trusted. Consumed by
    # docker/entrypoint.sh (uvicorn --forwarded-allow-ips); empty = trust no proxy.
    trusted_proxies: str = ""
    default_locale: str = "es"
    enable_ipp: bool = False
    ipp_timeout_s: float = Field(default=15.0, gt=0, le=300)  # per IPP HTTP request
    # ipps:// / https:// certificates are verified; printers with self-signed ones need this.
    ipp_insecure_tls: bool = False
    metrics: bool = False  # expose Prometheus metrics on /metrics (ADM-04)
    oda_converter: Path | None = None
    hotfolder: bool = False
    hotfolder_interval_s: float = Field(default=5.0, ge=0.05, le=3600)  # polling period
    log_level: str = "info"
    log_format: Literal["json", "text"] = "json"
    # Directory with the built SPA (index.html + assets). Served only if it exists.
    static_dir: Path | None = None

    @field_validator("static_dir", mode="before")
    @classmethod
    def _blank_static_dir_is_none(cls, value: object) -> object:
        """An empty A0TD_STATIC_DIR means "no SPA", not the current directory."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_config() -> AppConfig:
    """Return the cached configuration."""
    return AppConfig()
