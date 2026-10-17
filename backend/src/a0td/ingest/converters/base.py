"""Converter protocol (SPEC 7.2) and the subprocess helper used by external-tool converters."""

import contextlib
import logging
import os
import shutil
import signal
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from a0td.config import AppConfig, get_config
from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest.models import (
    CadOptions,
    CanonicalDocument,
    DocFormat,
    Progress,
    ProgressCallback,
)

log = logging.getLogger(__name__)

_OUTPUT_TAIL = 64 * 1024  # bytes of stdout / stderr read back from the capture files
_STDERR_TAIL = 400


@dataclass(slots=True)
class ConvertContext:
    """Everything a converter needs besides the file itself."""

    config: AppConfig = field(default_factory=get_config)
    password: str | None = None
    format: DocFormat | None = None  # detected format of the file being converted
    progress: ProgressCallback | None = None
    # Scratch directory for external tools (HOME / TMPDIR live here). Created on demand.
    scratch: Path | None = None
    # DXF / DWG choices (layout, units, scale, colour policy); ``None`` = automatic.
    cad: CadOptions | None = None

    def report(
        self, stage: str, pct: float, message_key: str | None = None, **params: object
    ) -> None:
        if self.progress is not None:
            self.progress(Progress(stage, pct, message_key, dict(params)))

    def scratch_dir(self, work: Path) -> Path:
        path = self.scratch or (work / ".work")
        path.mkdir(parents=True, exist_ok=True)
        return path


class Converter(Protocol):
    """Turns a detected file into the canonical document (SPEC 7.3)."""

    id: str
    handles: frozenset[DocFormat]

    def available(self) -> bool:
        """Whether the binaries / libraries this converter needs exist in this image."""
        ...

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        """Write the canonical files into ``work`` and describe them."""
        ...


@dataclass(frozen=True, slots=True)
class ToolResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def _tail(path: Path, limit: int = _OUTPUT_TAIL) -> bytes:
    """The last ``limit`` bytes of ``path`` (empty when it does not exist)."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - limit))
            return fh.read(limit)
    except OSError:
        return b""


def _with_memory_limit(args: list[str], memory_mb: int | None) -> list[str]:
    """Prefix ``prlimit --as=<bytes> --`` (util-linux); no limit, with a warning, if absent."""
    if not memory_mb or os.name != "posix":
        return args
    if shutil.which("prlimit") is None:
        log.warning("prlimit not found: running %s without a memory limit", Path(args[0]).name)
        return args
    return ["prlimit", f"--as={memory_mb * 1024 * 1024}", "--", *args]


def run_tool(
    argv: Sequence[str | os.PathLike[str]],
    *,
    scratch: Path,
    cwd: Path | None = None,
    timeout_s: float | None = None,
    memory_mb: int | None = None,
    env: dict[str, str] | None = None,
    ok_codes: Sequence[int] = (0,),
) -> ToolResult:
    """Run an external converter (SPEC 7.2 / 14): argv list (no shell), timeout, ``prlimit --as``,
    ``HOME`` / ``TMPDIR`` inside ``scratch``. The whole process group is killed on timeout.

    Raises ``CONVERSION_TIMEOUT`` or ``CONVERSION_FAILED`` (return code not in ``ok_codes``).
    """
    home = scratch / "home"
    tmp = scratch / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    child_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    child_env.update(env or {})
    args = [os.fspath(a) for a in argv]
    posix = os.name == "posix"
    out_path, err_path = scratch / "stdout.log", scratch / "stderr.log"
    try:
        with out_path.open("wb") as out_fh, err_path.open("wb") as err_fh:
            proc = subprocess.Popen(
                _with_memory_limit(args, memory_mb),
                stdin=subprocess.DEVNULL,
                stdout=out_fh,
                stderr=err_fh,
                cwd=cwd or scratch,
                env=child_env,
                start_new_session=posix,
            )
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                _kill_group(proc)
                proc.wait()
                raise DomainError(
                    ErrorCode.CONVERSION_TIMEOUT,
                    f"{Path(args[0]).name} timed out after {timeout_s}s",
                ) from exc
            finally:
                if proc.poll() is None:  # pragma: no cover - defensive
                    _kill_group(proc)
    except OSError as exc:
        raise DomainError(
            ErrorCode.CONVERSION_FAILED, f"cannot start {args[0]}: {exc.strerror or exc}"
        ) from exc
    out, err = _tail(out_path), _tail(err_path)
    if proc.returncode not in ok_codes:
        tail = err.decode("utf-8", "replace").strip()[-_STDERR_TAIL:]
        raise DomainError(
            ErrorCode.CONVERSION_FAILED,
            f"{Path(args[0]).name} exited with {proc.returncode}: {tail}",
        )
    return ToolResult(proc.returncode, out, err)


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover
            proc.kill()
