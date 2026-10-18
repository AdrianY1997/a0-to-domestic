"""Minimal stand-in for ``pydantic_settings`` (the browser build does not ship it, SPEC 17bis).

Supports what :class:`a0td.config.AppConfig` uses: fields filled from ``<env_prefix><FIELD>``
environment variables (case-insensitive names), explicit keyword arguments winning, values
validated by pydantic in the usual lax mode. No ``.env`` files, no secrets directories.
"""

import os
from typing import Any

from pydantic import BaseModel


def SettingsConfigDict(**kwargs: Any) -> Any:
    return kwargs


class BaseSettings(BaseModel):
    def __init__(self, **values: Any) -> None:
        prefix = str(self.model_config.get("env_prefix", ""))  # type: ignore[call-overload]
        env = {k.lower(): v for k, v in os.environ.items()}
        for name in type(self).model_fields:
            key = f"{prefix}{name}".lower()
            if name not in values and key in env:
                values[name] = env[key]
        super().__init__(**values)
