"""Job worker: ``python -m a0td.jobs.worker --job <id>`` (SPEC 9.5).

The manager starts one worker per job and sends the job spec as a single JSON line on stdin
(so a PDF password never touches the disk or the process list). The worker writes JSON lines on
stdout, one per message::

    {"type":"progress","stage":"render","pct":42,"message_key":"render.sheet","params":{"k":9}}
    {"type":"result", ...}                       # exactly one, on success (exit code 0)
    {"type":"error","code":"CORRUPT_FILE","detail":"...","params":{}}   # on failure (exit code 1)

Anything else a library prints to stdout is redirected to stderr. The worker never touches the
database (the manager is the only SQLite writer).
"""

import argparse
import json
import logging
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from pydantic_core import to_jsonable_python

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode
from a0td.jobs.limits import apply_limits
from a0td.jobs.render_loader import RenderUnavailableError, engine_function
from a0td.storage.files import STAGING_DIR, safe_file_name

log = logging.getLogger("a0td.worker")


class Emitter:
    """Writes protocol lines to the real stdout."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def __call__(self, message: dict[str, Any]) -> None:
        self._stream.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._stream.flush()

    def progress(
        self, stage: str, pct: float, message_key: str | None, params: dict[str, Any] | None
    ) -> None:
        self(
            {
                "type": "progress",
                "stage": stage,
                "pct": round(float(pct), 2),
                "message_key": message_key,
                "params": to_jsonable_python(params or {}),
            }
        )


def _get(obj: object, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ------------------------------------------------------------------------------ ingest


def _artifact_names(d: Path) -> list[str]:
    """What ingest leaves in a document directory (never the original or ``document.json``)."""
    names = ["pages", "canonical.pdf", "meta.json", "cache"]
    return names + [p.name for p in d.glob("page-*.tif")]


def _swap_in(staging: Path, doc_dir: Path) -> None:
    """Replace the document's ingest files by the ones rendered in ``staging``.

    The old files are moved aside first and put back if anything fails, so the document is never
    left half way between two renderings.
    """
    old = doc_dir / (STAGING_DIR + "-prev")
    shutil.rmtree(old, ignore_errors=True)
    old.mkdir()
    moved_old: list[str] = []
    moved_new: list[str] = []
    try:
        for name in _artifact_names(doc_dir):
            if (doc_dir / name).exists():
                (doc_dir / name).replace(old / name)
                moved_old.append(name)
        for name in _artifact_names(staging):
            if (staging / name).exists():
                (staging / name).replace(doc_dir / name)
                moved_new.append(name)
    except BaseException:
        for name in moved_new:
            target = doc_dir / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        for name in moved_old:
            (old / name).replace(doc_dir / name)
        raise
    finally:
        shutil.rmtree(old, ignore_errors=True)


def run_ingest(spec: dict[str, Any], cfg: AppConfig, emit: Emitter) -> dict[str, Any]:
    from a0td.ingest import CadOptions, ingest_document

    def progress(p: Any) -> None:
        emit.progress(p.stage, p.pct, p.message_key, p.params)

    doc_dir = Path(spec["doc_dir"])
    reingest = bool(spec.get("reingest"))
    workdir = doc_dir / STAGING_DIR if reingest else doc_dir
    if reingest:
        shutil.rmtree(workdir, ignore_errors=True)
    try:
        result = ingest_document(
            Path(spec["source"]),
            workdir,
            password=spec.get("password") or None,
            progress=progress,
            config=cfg,
            filename=spec.get("filename"),
            cad_options=CadOptions.model_validate(spec["cad"]) if spec.get("cad") else None,
        )
        if reingest:
            _swap_in(workdir, doc_dir)
    finally:
        if reingest:
            shutil.rmtree(workdir, ignore_errors=True)
    return {
        "format": result.format.value,
        "mime": result.mime,
        "pages": [p.model_dump(mode="json") for p in result.pages],
        "warnings": [w.model_dump(mode="json") for w in result.warnings],
    }


# ------------------------------------------------------------------------------ render


def _normalize_outputs(raw_outputs: list[Any], out_dir: Path) -> list[dict[str, Any]]:
    """Make every output a plain-named file inside ``out_dir`` and fill in its size."""
    outputs: list[dict[str, Any]] = []
    used: set[str] = set()
    for i, raw in enumerate(raw_outputs):
        name = str(_get(raw, "name", ""))
        kind = str(_get(raw, "kind", "") or "")
        safe = safe_file_name(name, default=f"output-{i + 1}")
        src = out_dir / name if name == safe else None
        if src is None:
            # The engine reported a name with separators or reserved characters: find the file
            # it meant (its basename) and give it the safe name.
            candidate = out_dir / name.replace("\\", "/").rsplit("/", 1)[-1]
            if candidate.is_file():
                src = candidate
        if src is None or not src.is_file():
            raise DomainError(ErrorCode.CONVERSION_FAILED, f"output '{safe}' was not written")
        final, n = safe, 1
        while final in used:
            n += 1
            final = f"{Path(safe).stem} ({n}){Path(safe).suffix}"
        if src.name != final:
            src.replace(out_dir / final)
        used.add(final)
        size = (out_dir / final).stat().st_size
        pages = _get(raw, "pages")
        outputs.append(
            {
                "name": final,
                "kind": kind or (Path(final).suffix.lstrip(".").lower() or "file"),
                "size_bytes": size,
                "pages": int(pages) if pages is not None else None,
            }
        )
    return outputs


def run_render(spec: dict[str, Any], cfg: AppConfig, emit: Emitter) -> dict[str, Any]:
    from a0td.domain.settings import JobSettings, PrinterProfile

    render_job: Callable[..., Any] = engine_function("render_job")  # type: ignore[assignment]
    settings = JobSettings.model_validate(spec["settings"])
    printer = PrinterProfile.model_validate(spec["printer"]) if spec.get("printer") else None
    out_dir = Path(spec["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    def progress(
        stage: str, pct: float, message_key: str = "", params: dict[str, Any] | None = None
    ) -> None:
        emit.progress(stage, pct, message_key or None, params)

    res = render_job(
        Path(spec["doc_dir"]),
        settings,
        out_dir,
        printer=printer,
        exact_scale=bool(spec.get("exact_scale")),
        test_sheet_only=bool(spec.get("test_sheet_only")),
        progress=progress,
    )
    return {
        "outputs": _normalize_outputs(list(_get(res, "outputs", []) or []), out_dir),
        "plans": to_jsonable_python(list(_get(res, "plans", []) or [])),
        "warnings": to_jsonable_python(list(_get(res, "warnings", []) or [])),
        "ink": to_jsonable_python(_get(res, "ink", None)),
    }


# ------------------------------------------------------------------------------- print


def run_print(spec: dict[str, Any], cfg: AppConfig, emit: Emitter) -> dict[str, Any]:
    """IPP direct printing (IMP-03) of a finished render job's PDF."""
    import tempfile

    from a0td.printing.ipp import IppClient
    from a0td.printing.submit import submit_pdf

    pdf = Path(spec["pdf"])
    if not pdf.is_file():
        raise DomainError(ErrorCode.NOT_FOUND, "the rendered PDF is gone")
    client = IppClient(
        str(spec["ipp_uri"]), timeout=cfg.ipp_timeout_s, verify_tls=not cfg.ipp_insecure_tls
    )

    def progress(stage: str, pct: float, key: str) -> None:
        emit.progress(stage, pct, key, None)

    with tempfile.TemporaryDirectory(prefix="a0td-print-") as tmp:
        outcome = submit_pdf(
            client,
            pdf,
            scratch=Path(tmp),
            media=str(spec["media"]),
            copies=int(spec.get("copies") or 1),
            monochrome=bool(spec.get("monochrome")),
            pages=list(spec["pages"]) if spec.get("pages") else None,
            job_name=str(spec.get("job_name") or "a0-to-domestic")[:100],
            progress=progress,
        )
    return {
        "outputs": [],
        "plans": [],
        "warnings": outcome.warnings,
        "printer_job_id": outcome.job_id,
        "printer_job_state": outcome.state,
    }


# --------------------------------------------------------------------------------- main


def _error_message(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, DomainError):
        return {
            "type": "error",
            "code": exc.code.value,
            "detail": exc.detail,
            "params": to_jsonable_python(exc.params),
        }
    if isinstance(exc, MemoryError):
        return {
            "type": "error",
            "code": ErrorCode.CONVERSION_FAILED.value,
            "detail": "the job ran out of memory",
            "params": {"reason": "memory"},
        }
    if isinstance(exc, RenderUnavailableError):
        return {
            "type": "error",
            "code": ErrorCode.CONVERSION_FAILED.value,
            "detail": "the render engine is not available",
            "params": {"reason": "render_unavailable"},
        }
    return {
        "type": "error",
        "code": ErrorCode.CONVERSION_FAILED.value,
        "detail": "internal error while processing the job",  # full text goes to the logs
        "params": {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="a0td.jobs.worker")
    parser.add_argument("--job", required=True, help="job id (for logs)")
    args = parser.parse_args(argv)

    # Keep fd 1 for the protocol; whatever else prints to stdout goes to stderr.
    emit = Emitter(os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1))
    os.dup2(2, 1)
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    try:
        spec = json.loads(sys.stdin.readline())
        cfg = AppConfig()
        apply_limits(cfg.job_memory_mb)
        kind = spec["kind"]
        if kind == "ingest":
            result = run_ingest(spec, cfg, emit)
        elif kind == "render":
            result = run_render(spec, cfg, emit)
        elif kind == "print":
            result = run_print(spec, cfg, emit)
        else:
            raise DomainError(ErrorCode.INVALID_SETTINGS, f"unsupported job kind: {kind}")
        emit({"type": "result", **result})
        return 0
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt | SystemExit):
            raise
        log.error("job %s failed: %s: %s", args.job, type(exc).__name__, exc, exc_info=True)
        emit(_error_message(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
