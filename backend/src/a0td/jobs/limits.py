"""Resource limits and environment of job subprocesses (SPEC 9.5, 14)."""

import os
import sys
from pathlib import Path

from a0td.config import AppConfig

# Config fields that must not be handed to a worker.
_SECRET_FIELDS = {"password_hash", "token"}


def apply_limits(memory_mb: int, niceness: int = 10) -> list[str]:
    """Set ``RLIMIT_AS`` and the niceness of the current process. Returns what was applied.

    Both are POSIX-only; elsewhere (Windows dev hosts) they are skipped silently.
    """
    applied: list[str] = []
    try:
        import resource

        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        applied.append("rlimit_as")
    except (ImportError, ValueError, OSError):
        pass
    try:
        os.nice(niceness)
        applied.append("nice")
    except (AttributeError, OSError):
        pass
    return applied


def config_env(config: AppConfig) -> dict[str, str]:
    """``A0TD_*`` variables that recreate ``config`` in a child process."""
    env: dict[str, str] = {}
    for name, value in config.model_dump().items():
        if name in _SECRET_FIELDS:
            continue
        key = f"A0TD_{name.upper()}"
        if value is None:
            continue
        if isinstance(value, bool):
            env[key] = "true" if value else "false"
        elif isinstance(value, Path):
            env[key] = str(value)
        else:
            env[key] = str(value)
    return env


def child_env(config: AppConfig, job_tmp: Path) -> dict[str, str]:
    """Environment of a worker: parent env + config + a private temp dir + import path."""
    env = dict(os.environ)
    for key in ("A0TD_PASSWORD_HASH", "A0TD_TOKEN"):
        env.pop(key, None)
    env.update(config_env(config))
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(job_tmp)
    env["MALLOC_ARENA_MAX"] = "2"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    src_root = str(Path(__file__).resolve().parents[2])  # the directory that contains ``a0td``
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_root + (os.pathsep + existing if existing else "")
    return env


def python_executable() -> str:
    return sys.executable
