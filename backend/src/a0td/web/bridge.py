"""Web local mode bridge (SPEC 17bis): the functions the browser worker calls through Pyodide.

Everything works on a root directory (``/a0td`` in Pyodide's in-memory FS; a temp dir in CPython
tests) with the same layout as the server (SPEC 11.2)::

    <root>/documents/<doc_id>/    document.json, meta.json, canonical.pdf | page-<n>.tif,
                                  pages/<n>/...
    <root>/jobs/<job_id>/output/  the rendered PDF / ZIP files

No network, no database, no FastAPI, no subprocess. Every function that returns structured data
returns a **JSON string** whose shape is that of the REST API (``DocumentOut``, ``Plan``, ...);
previews and the calibration page return ``bytes`` (PNG / PDF). Failures raise
:class:`BridgeError`, a :class:`~a0td.domain.errors.DomainError` whose ``str()`` is the
problem+json body ``{"type", "title", "status", "detail", "code", "params", "errors"?}``.

Progress callbacks are ``callable(stage, pct, message_key, params)``; a JS function is accepted
(``params`` then arrives as a plain JS object). A callback that returns ``True`` or raises cancels
the job (``JOB_CANCELLED``).
"""

import hashlib
import io
import json
import os
import re
import shutil
import uuid
import zipfile
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from a0td.config import AppConfig, get_config
from a0td.domain.errors import HTTP_STATUS_BY_CODE, DomainError, ErrorCode, WarningCode
from a0td.domain.exact_scale import is_exact_scale
from a0td.domain.papers import list_papers
from a0td.domain.plan import Plan, PlanWarning
from a0td.domain.planner import plan as make_plan
from a0td.domain.settings import (
    BUILTIN_PRESETS,
    BUILTIN_PRINTERS,
    BlankSensitivity,
    JobSettings,
    OutputFormat,
    PrinterProfile,
    get_builtin_printer,
)
from a0td.ingest import (
    CadOptions,
    ConverterRegistry,
    IngestResult,
    SizeOverride,
    default_registry,
    ingest_document,
    load_occupancy,
    page_dir,
)
from a0td.ingest import (
    set_physical_size as _set_physical_size,
)
from a0td.ingest.models import Page, PageKind, Progress
from a0td.ingest.occupancy import OCCUPANCY_FILES, PixelOccupancy
from a0td.io_models import DocumentOut
from a0td.render.color import pixel_ops
from a0td.version import __version__

__all__ = [
    "WEB_LIMITS",
    "BridgeError",
    "cad_info",
    "calibration_pdf",
    "call_json",
    "clear",
    "configure",
    "delete_document",
    "delete_job",
    "export_document",
    "get_document",
    "import_document",
    "info",
    "ingest",
    "output_page_thumb",
    "page_preview",
    "papers",
    "plan",
    "presets",
    "printers",
    "render",
    "set_physical_size",
    "sheet_preview",
]

ProgressFn = Callable[[str, float, str | None, dict[str, Any]], Any]

DEFAULT_ROOT = "/a0td"
DOCUMENT_INFO = "document.json"
_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

#: Limits of the in-browser build (wasm32 memory). They are the defaults of ``A0TD_*``.
WEB_LIMITS: dict[str, int] = {
    "max_upload_mb": 300,
    "max_pages": 50,
    "max_megapixels": 150,
    "max_page_mm": 10000,
    "max_sheets": 300,
    "preview_max_px": 2048,
    "workers": 1,
    "job_timeout_s": 3600,
    "job_memory_mb": 1800,
    "retention_hours": 72,
}
WEB_MAX_DPI = 300  # output.dpi / output.max_dpi are lowered to this (LOCAL_LIMIT)
_LIMIT_CODES = {
    ErrorCode.FILE_TOO_LARGE,
    ErrorCode.TOO_MANY_PAGES,
    ErrorCode.PIXEL_LIMIT_EXCEEDED,
    ErrorCode.PAGE_TOO_LARGE,
}

_state: dict[str, Any] = {"root": None, "config": None, "registry": None}
_occ_cache: OrderedDict[tuple[str, int, int, float, float], PixelOccupancy] = OrderedDict()
_OCC_CACHE_SIZE = 8


# ------------------------------------------------------------------------------- errors


class BridgeError(DomainError):
    """A failed bridge call. ``str(exc)`` is the problem+json body (parse it in JS)."""

    def __init__(
        self,
        code: ErrorCode | str,
        detail: str | None = None,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        known = ErrorCode(code) if code in {c.value for c in ErrorCode} else None
        super().__init__(known or ErrorCode.CONVERSION_FAILED, detail, params, errors)
        self.code_str = str(code.value if isinstance(code, ErrorCode) else code)
        self.status = HTTP_STATUS_BY_CODE.get(known, 500) if known else 500

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"urn:a0td:error:{self.code_str}",
            "title": self.code_str.replace("_", " ").capitalize(),
            "status": self.status,
            "detail": self.detail,
            "code": self.code_str,
            "params": to_jsonable_python(self.params),
        }
        if self.errors is not None:
            body["errors"] = self.errors
        return body

    def __str__(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def _bridge_error(exc: BaseException) -> BridgeError:
    if isinstance(exc, BridgeError):
        return exc
    if isinstance(exc, DomainError):
        return BridgeError(exc.code, exc.detail, exc.params, exc.errors)
    if isinstance(exc, ValidationError):
        return BridgeError(
            ErrorCode.INVALID_SETTINGS,
            "Request validation failed",
            errors=_validation_errors(exc),
        )
    if isinstance(exc, MemoryError):
        return BridgeError(
            ErrorCode.CONVERSION_FAILED, "the browser ran out of memory", {"reason": "memory"}
        )
    return BridgeError("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")


def _validation_errors(exc: ValidationError, prefix: str = "") -> list[dict[str, Any]]:
    return [
        {
            "field": ".".join(str(p) for p in ([prefix] if prefix else []) + list(e["loc"])),
            "message": e["msg"],
            "type": e["type"],
        }
        for e in exc.errors()
    ]


def _api[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Turn any failure of a public function into a :class:`BridgeError`."""

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except BridgeError:
            raise
        except Exception as exc:  # DomainError, ValidationError, MemoryError, anything else
            raise _bridge_error(exc) from exc

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ----------------------------------------------------------------------- configuration


def configure(root: str | os.PathLike[str] | None = None, **limits: int) -> AppConfig:
    """Set the root directory and the limits; returns the effective configuration.

    Limits default to :data:`WEB_LIMITS` unless an ``A0TD_*`` variable (or ``limits``) says
    otherwise. Calling it again with another ``root`` switches storage (tests).
    """
    base = Path(root if root is not None else os.environ.get("A0TD_WEB_ROOT") or DEFAULT_ROOT)
    (base / "documents").mkdir(parents=True, exist_ok=True)
    (base / "jobs").mkdir(parents=True, exist_ok=True)
    (base / "tmp").mkdir(parents=True, exist_ok=True)
    env = {"data_dir": str(base), "tmp_dir": str(base / "tmp"), **WEB_LIMITS}
    env.update(limits)
    for key, value in env.items():
        os.environ.setdefault(f"A0TD_{key.upper()}", str(value))
    for key, value in limits.items():
        os.environ[f"A0TD_{key.upper()}"] = str(value)
    os.environ["A0TD_DATA_DIR"] = str(base)
    os.environ["A0TD_TMP_DIR"] = str(base / "tmp")
    get_config.cache_clear()
    cfg = get_config()
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = int(cfg.max_megapixels) * 1_000_000
    _state.update(root=base, config=cfg, registry=None)
    _occ_cache.clear()
    return cfg


def _cfg() -> AppConfig:
    if _state["config"] is None:
        configure()
    cfg = _state["config"]
    assert isinstance(cfg, AppConfig)
    return cfg


def _root() -> Path:
    _cfg()
    root = _state["root"]
    assert isinstance(root, Path)
    return root


def _registry() -> ConverterRegistry:
    if _state["registry"] is None:
        _state["registry"] = default_registry()
    reg = _state["registry"]
    assert isinstance(reg, ConverterRegistry)
    return reg


def _doc_dir(doc_id: str) -> Path:
    if not _ID_RE.match(str(doc_id)):
        raise DomainError(ErrorCode.NOT_FOUND, "document not found")
    return _root() / "documents" / str(doc_id)


def _job_dir(job_id: str) -> Path:
    if not _ID_RE.match(str(job_id)):
        raise DomainError(ErrorCode.NOT_FOUND, "job not found")
    return _root() / "jobs" / str(job_id)


# ------------------------------------------------------------------------------ helpers


def _json(obj: Any) -> str:
    return json.dumps(to_jsonable_python(obj), ensure_ascii=False, separators=(",", ":"))


def _as_dict(value: Any, what: str) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise DomainError(ErrorCode.INVALID_SETTINGS, f"{what} is not valid JSON") from exc
    if hasattr(value, "to_py"):  # a JS object / PyProxy
        value = value.to_py()
    if not isinstance(value, dict):
        raise DomainError(ErrorCode.INVALID_SETTINGS, f"{what} must be an object")
    return value


def _parse_settings(settings_json: Any) -> JobSettings:
    raw = _as_dict(settings_json, "settings")
    try:
        return JobSettings.model_validate(raw)
    except ValidationError as exc:
        raise BridgeError(
            ErrorCode.INVALID_SETTINGS,
            "Request validation failed",
            errors=_validation_errors(exc, "settings"),
        ) from exc


def _parse_printer(printer_json: Any, settings: JobSettings) -> PrinterProfile | None:
    if printer_json:
        try:
            return PrinterProfile.model_validate(_as_dict(printer_json, "printer"))
        except ValidationError as exc:
            raise BridgeError(
                ErrorCode.INVALID_SETTINGS,
                "Request validation failed",
                errors=_validation_errors(exc, "printer"),
            ) from exc
    return get_builtin_printer(settings.target.printer_id)


def _list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if hasattr(value, "to_py"):
        value = value.to_py()
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _progress(cb: Any) -> Callable[[str, float, str | None, dict[str, Any]], None] | None:
    """Wrap a Python or JS callback; a ``True`` result or an exception cancels the job."""
    if cb is None:
        return None
    convert: Callable[[dict[str, Any]], Any] = lambda p: p  # noqa: E731
    try:
        import js
        from pyodide.ffi import JsProxy, to_js

        if isinstance(cb, JsProxy):
            convert = lambda p: to_js(p, dict_converter=js.Object.fromEntries)  # noqa: E731
    except ImportError:
        pass

    def call(stage: str, pct: float, key: str | None, params: dict[str, Any]) -> None:
        try:
            stop = cb(stage, round(float(pct), 2), key, convert(to_jsonable_python(params or {})))
        except Exception as exc:
            raise DomainError(ErrorCode.JOB_CANCELLED, "cancelled by the caller") from exc
        if stop is True:
            raise DomainError(ErrorCode.JOB_CANCELLED, "cancelled by the caller")

    return call


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _display_name(raw: str | None) -> str:
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    return name[:200] or "document"


def _now() -> datetime:
    return datetime.now(UTC)


def _read_document(doc_id: str) -> DocumentOut:
    path = _doc_dir(doc_id) / DOCUMENT_INFO
    try:
        return DocumentOut.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainError(ErrorCode.NOT_FOUND, "document not found") from exc


def _write_document(doc: DocumentOut) -> None:
    (_doc_dir(doc.id) / DOCUMENT_INFO).write_text(doc.model_dump_json(), encoding="utf-8")


def _ready_document(doc_id: str) -> tuple[Path, DocumentOut]:
    doc = _read_document(doc_id)
    if doc.status != "ready":
        raise DomainError(
            ErrorCode.DOCUMENT_NOT_READY, f"document is {doc.status}", {"status": doc.status}
        )
    return _doc_dir(doc.id), doc


def _settings_doc(settings: JobSettings) -> tuple[Path, DocumentOut]:
    if settings.document_id is None:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            "settings.document_id is required",
            errors=[{"field": "settings.document_id", "message": "required", "type": "missing"}],
        )
    return _ready_document(str(settings.document_id))


def _occupancy(doc_dir: Path, page: Page, settings: JobSettings) -> PixelOccupancy | None:
    sens = settings.tiling.blank_sensitivity
    pdir = page_dir(doc_dir, page.index)
    path = pdir / OCCUPANCY_FILES[sens]
    if not path.exists():
        path = pdir / OCCUPANCY_FILES[BlankSensitivity.NORMAL]
    if not path.exists():
        return None
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size, page.width_mm, page.height_mm)
    hit = _occ_cache.get(key)
    if hit is not None:
        _occ_cache.move_to_end(key)
        return hit
    occ = load_occupancy(pdir, page, sens)
    _occ_cache[key] = occ
    while len(_occ_cache) > _OCC_CACHE_SIZE:
        _occ_cache.popitem(last=False)
    return occ


# ------------------------------------------------------------------------------ catalogs


@_api
def info() -> str:
    """``GET /api/v1/info``: version, limits, converters (subprocess ones are unavailable)."""
    cfg = _cfg()
    limits = {
        "max_upload_mb": cfg.max_upload_mb,
        "max_pages": cfg.max_pages,
        "max_megapixels": cfg.max_megapixels,
        "max_page_mm": cfg.max_page_mm,
        "max_sheets": cfg.max_sheets,
        "preview_max_px": cfg.preview_max_px,
        "workers": cfg.workers,
        "retention_hours": cfg.retention_hours,
        "max_dpi": WEB_MAX_DPI,
    }
    from a0td.imaging import backend_name

    return _json(
        {
            "version": os.environ.get("A0TD_VERSION") or __version__,
            "limits": limits,
            "converters": _registry().info(),
            "auth_mode": "none",
            "default_locale": cfg.default_locale,
            "ipp_enabled": False,
            "local": True,
            "imaging": backend_name(),
        }
    )


@_api
def papers() -> str:
    """``GET /api/v1/papers``."""
    return _json([asdict(p) for p in list_papers()])


@_api
def presets() -> str:
    """Built-in presets (the user's own live in the browser's storage)."""
    return _json(list(BUILTIN_PRESETS))


@_api
def printers() -> str:
    """Built-in printer profiles (the user's own live in the browser's storage)."""
    return _json(list(BUILTIN_PRINTERS))


@_api
def calibration_pdf(paper: str, locale: str = "es", printer_name: str = "") -> bytes:
    """The printer calibration page (``GET /printers/{id}/calibration.pdf``) as PDF bytes."""
    from a0td.render.calibration import render_calibration_pdf

    try:
        return render_calibration_pdf(paper, printer_name=printer_name, locale=locale)
    except ValueError as exc:
        raise DomainError(ErrorCode.INVALID_SETTINGS, str(exc)) from exc


# ------------------------------------------------------------------------------ documents


def _size_override(raw: dict[str, Any]) -> SizeOverride | None:
    if not raw:
        return None
    keys = {"paper", "width_mm", "height_mm", "dpi"}
    given = {k: v for k, v in raw.items() if k in keys and v is not None}
    if len(given) != 1:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS, "exactly one of paper, width_mm, height_mm or dpi"
        )
    return SizeOverride(**given)


@_api
def ingest(
    src_path: str,
    name: str,
    password: str | None = None,
    options: Any = None,
    progress: Any = None,
) -> str:
    """Ingest the file at ``src_path`` (``POST /documents``); returns a ``DocumentOut``.

    The result mirrors the REST document, including ``status``: ``ready``, ``password_required``
    (retry with ``password``) or ``failed`` (with ``error_code`` / ``error_params``). A failed
    document leaves nothing on disk. ``options`` (object or JSON string): ``doc_id`` (reuse an
    id when retrying), ``cad`` (``CadOptions``) and ``size_override`` (one of ``paper``,
    ``width_mm``, ``height_mm``, ``dpi``). The source file is read in place and never copied.
    """
    cfg = _cfg()
    opts = _as_dict(options, "options")
    src = Path(src_path)
    if not src.is_file():
        raise DomainError(ErrorCode.NOT_FOUND, "the source file does not exist")
    doc_id = str(opts.get("doc_id") or uuid.uuid4())
    docdir = _doc_dir(doc_id)
    shutil.rmtree(docdir, ignore_errors=True)
    docdir.mkdir(parents=True)
    now = _now()
    size = src.stat().st_size
    doc = DocumentOut(
        id=doc_id,
        name=_display_name(name),
        format=None,
        mime=None,
        size_bytes=size,
        sha256=_sha256(src),
        status="processing",
        error_code=None,
        error_detail=None,
        error_params={},
        pages=[],
        page_count=0,
        warnings=[],
        job_id=None,
        created_at=now,
        expires_at=now + timedelta(hours=cfg.retention_hours),
    )
    cb = _progress(progress)

    def on_progress(p: Progress) -> None:
        if cb is not None:
            cb(p.stage, p.pct, p.message_key, p.params)

    try:
        if size > cfg.max_upload_mb * 1024 * 1024:
            raise DomainError(
                ErrorCode.FILE_TOO_LARGE,
                f"the file exceeds the {cfg.max_upload_mb} MB limit",
                {"max_mb": cfg.max_upload_mb},
            )
        cad = CadOptions.model_validate(opts["cad"]) if opts.get("cad") else None
        result = ingest_document(
            src,
            docdir,
            password=password or None,
            progress=on_progress,
            config=cfg,
            registry=_registry(),
            filename=doc.name,
            size_override=_size_override(_as_dict(opts.get("size_override"), "size_override")),
            cad_options=cad,
        )
    except ValidationError as exc:
        shutil.rmtree(docdir, ignore_errors=True)
        raise BridgeError(
            ErrorCode.INVALID_SETTINGS, "invalid options", errors=_validation_errors(exc, "options")
        ) from exc
    except (DomainError, MemoryError) as exc:
        shutil.rmtree(docdir, ignore_errors=True)
        err = _bridge_error(exc)
        if err.code is ErrorCode.JOB_CANCELLED:
            raise err from exc
        params = dict(err.params)
        if err.code in _LIMIT_CODES:
            params["local"] = True  # a browser limit, lower than the Docker one
        waiting = err.code in (ErrorCode.PASSWORD_REQUIRED, ErrorCode.PASSWORD_INVALID)
        failed = doc.model_copy(
            update={
                "status": "password_required" if waiting else "failed",
                "error_code": err.code_str,
                "error_detail": err.detail,
                "error_params": to_jsonable_python(params),
            }
        )
        return failed.model_dump_json()
    ready = doc.model_copy(
        update={
            "status": "ready",
            "format": result.format.value,
            "mime": result.mime,
            "pages": result.pages,
            "page_count": len(result.pages),
            "warnings": result.warnings,
        }
    )
    _write_document(ready)
    return ready.model_dump_json()


@_api
def get_document(doc_id: str) -> str:
    """The stored ``DocumentOut`` of a document (``GET /documents/{id}``)."""
    return _read_document(doc_id).model_dump_json()


@_api
def cad_info(doc_id: str) -> str:
    """``GET /documents/{id}/cad``: layouts, units, scale and colour policy of a DXF document."""
    doc_dir, _doc = _ready_document(doc_id)
    try:
        cad = IngestResult.load(doc_dir).cad
    except (OSError, ValueError):
        cad = None
    if cad is None:
        raise DomainError(ErrorCode.NOT_FOUND, "the document is not a CAD drawing")
    return cad.model_dump_json()


@_api
def set_physical_size(doc_id: str, page: int, **size: Any) -> str:
    """Fix the physical size of a raster / size-less SVG page; returns the updated ``Page``.

    ``size`` is exactly one of ``paper``, ``width_mm``, ``height_mm``, ``dpi``.
    """
    doc_dir, doc = _ready_document(doc_id)
    given = {k: v for k, v in size.items() if v is not None}
    if len(given) != 1 or not set(given) <= {"paper", "width_mm", "height_mm", "dpi"}:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            "exactly one of paper, width_mm, height_mm or dpi is required",
        )
    if not 0 <= page < doc.page_count:
        raise DomainError(ErrorCode.NOT_FOUND, f"page {page} does not exist")
    result = _set_physical_size(doc_dir, page, config=_cfg(), **given)
    _write_document(doc.model_copy(update={"pages": result.pages, "page_count": len(result.pages)}))
    _occ_cache.clear()
    return result.pages[page].model_dump_json()


@_api
def page_preview(
    doc_id: str,
    page: int,
    max_px: int | None = None,
    hidden_layers: Any = None,
    visible_layers: Any = None,
) -> bytes:
    """PNG preview of a page (``GET /documents/{id}/pages/{n}/preview``).

    ``hidden_layers`` / ``visible_layers`` (lists, or comma separated names / xrefs) render a PDF
    page with those layers off / forced on, as the output will be (ANA-07). The mirror is the
    UI's business.
    """
    from PIL import Image

    doc_dir, doc = _ready_document(doc_id)
    if not 0 <= page < doc.page_count:
        raise DomainError(ErrorCode.NOT_FOUND, f"page {page} does not exist")
    cfg = _cfg()
    px = min(int(max_px or cfg.preview_max_px), cfg.preview_max_px)
    hidden, visible = _list(hidden_layers), _list(visible_layers)
    canonical = doc_dir / "canonical.pdf"
    if (hidden or visible) and canonical.is_file():
        from a0td.render.layers import layered_preview_png

        return layered_preview_png(canonical, page, doc.pages[page].layers, hidden, visible, px)
    from a0td.ingest.preview import PREVIEW_FILE

    stored = page_dir(doc_dir, page) / PREVIEW_FILE
    if not stored.is_file():
        raise DomainError(ErrorCode.NOT_FOUND, "image not found")
    with Image.open(stored) as img:
        out = img.convert("RGB")
    if max(out.size) > px:
        out.thumbnail((px, px), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", compress_level=3)
    return buf.getvalue()


@_api
def delete_document(doc_id: str) -> None:
    """Remove a document and its files."""
    shutil.rmtree(_doc_dir(doc_id), ignore_errors=True)
    _occ_cache.clear()


@_api
def export_document(doc_id: str) -> bytes:
    """The whole document directory as an (uncompressed) ZIP, to keep it in IndexedDB."""
    doc_dir = _doc_dir(doc_id)
    if not (doc_dir / DOCUMENT_INFO).is_file():
        raise DomainError(ErrorCode.NOT_FOUND, "document not found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for path in sorted(doc_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(doc_dir).as_posix())
    return buf.getvalue()


@_api
def import_document(doc_id: str, data: bytes) -> str:
    """Restore a document exported with :func:`export_document`; returns its ``DocumentOut``."""
    doc_dir = _doc_dir(doc_id)
    shutil.rmtree(doc_dir, ignore_errors=True)
    doc_dir.mkdir(parents=True)
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
            for member in zf.infolist():
                target = (doc_dir / member.filename).resolve()
                if not target.is_relative_to(doc_dir.resolve()):
                    raise DomainError(ErrorCode.CORRUPT_FILE, "unsafe path in the archive")
                if member.is_dir():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
    except (zipfile.BadZipFile, DomainError):
        shutil.rmtree(doc_dir, ignore_errors=True)
        raise
    return _read_document(doc_id).model_dump_json()


# ---------------------------------------------------------------------------- plan, render


@_api
def plan(
    settings_json: Any,
    page_index: int = 0,
    include_alternatives: bool = False,
    preset_id: str | None = None,
    printer_json: Any = None,
) -> str:
    """``POST /plan``: the tiling plan of one page (a ``Plan``)."""
    settings = _parse_settings(settings_json)
    doc_dir, doc = _settings_doc(settings)
    if not 0 <= page_index < doc.page_count:
        raise DomainError(ErrorCode.NOT_FOUND, f"page {page_index} does not exist")
    page = doc.pages[page_index]
    result: Plan = make_plan(
        page.to_geometry(doc.page_count),
        settings,
        printer=_parse_printer(printer_json, settings),
        occupancy=_occupancy(doc_dir, page, settings),
        include_alternatives=include_alternatives,
        exact_scale=is_exact_scale(settings, preset_id),
        max_sheets=_cfg().max_sheets,
    )
    return result.model_dump_json()


@_api
def sheet_preview(
    settings_json: Any,
    page_index: int,
    sheet_id: str,
    max_px: int = 800,
    printer_json: Any = None,
    locale: str = "es",
) -> bytes:
    """``POST /plan/sheet-preview``: PNG of one sheet."""
    from a0td.render.engine import render_sheet_preview

    settings = _parse_settings(settings_json)
    doc_dir, doc = _settings_doc(settings)
    if not 0 <= page_index < doc.page_count:
        raise DomainError(ErrorCode.NOT_FOUND, f"page {page_index} does not exist")
    return render_sheet_preview(
        doc_dir,
        settings,
        page_index,
        sheet_id,
        min(int(max_px), _cfg().preview_max_px),
        printer=_parse_printer(printer_json, settings),
        doc_name=Path(doc.name).stem,
        locale=locale,
    )


def _apply_local_limits(
    settings: JobSettings, doc: DocumentOut
) -> tuple[JobSettings, list[PlanWarning]]:
    """Lower the output resolution to the browser's limit.

    The values are always capped; ``LOCAL_LIMIT`` is reported only when the cap can change the
    result: ``max_dpi`` for documents with pixels, ``dpi`` when something is rasterized.
    """
    out = settings.output
    changes: dict[str, int] = {}
    notes: list[PlanWarning] = []
    has_pixels = any(p.kind is not PageKind.VECTOR for p in doc.pages)
    rasterizes = (
        out.rasterize
        or pixel_ops(settings.color)
        or any(f is not OutputFormat.PDF for f in out.formats)
    )
    for field, matters in (("dpi", rasterizes), ("max_dpi", has_pixels)):
        value = getattr(out, field)
        if value > WEB_MAX_DPI:
            changes[field] = WEB_MAX_DPI
            if matters:
                notes.append(
                    PlanWarning(
                        code=WarningCode.LOCAL_LIMIT,
                        params={"limit": field, "requested": value, "applied": WEB_MAX_DPI},
                    )
                )
    if not changes:
        return settings, notes
    return settings.model_copy(update={"output": out.model_copy(update=changes)}), notes


@_api
def render(
    settings_json: Any,
    preset_id: str | None = None,
    test_sheet_only: bool = False,
    progress: Any = None,
    printer_json: Any = None,
    locale: str = "es",
) -> str:
    """``POST /jobs``: render and store the outputs; returns the finished job.

    The result is ``{"job_id", "outputs": [{name, path, kind, pages, size_bytes}], "plans",
    "warnings": [{code, severity, params}], "ink"}``; each ``path`` is under
    ``<root>/jobs/<job_id>/output/``. Output resolution above 300 dpi is lowered (``LOCAL_LIMIT``).
    """
    from a0td.render.engine import render_job

    settings = _parse_settings(settings_json)
    doc_dir, doc = _settings_doc(settings)
    settings, local_notes = _apply_local_limits(settings, doc)
    job_id = str(uuid.uuid4())
    out_dir = _job_dir(job_id) / "output"
    out_dir.mkdir(parents=True)
    cb = _progress(progress)
    try:
        res = render_job(
            doc_dir,
            settings,
            out_dir,
            printer=_parse_printer(printer_json, settings),
            exact_scale=is_exact_scale(settings, preset_id),
            test_sheet_only=bool(test_sheet_only),
            progress=cb,
            doc_name=Path(doc.name).stem,
            locale=locale,
        )
    except BaseException:
        shutil.rmtree(_job_dir(job_id), ignore_errors=True)
        raise
    warnings = [
        {"code": n.code.value, "severity": n.severity.value, "params": n.params}
        for n in local_notes
    ]
    warnings += [w.model_dump(mode="json") for w in res.warnings]
    outputs = [
        {
            "name": o.name,
            "path": str(out_dir / o.name),
            "kind": o.kind,
            "pages": o.pages,
            "size_bytes": (out_dir / o.name).stat().st_size,
        }
        for o in res.outputs
    ]
    return _json(
        {
            "job_id": job_id,
            "outputs": outputs,
            "plans": res.plans,
            "warnings": warnings,
            "ink": res.ink,
        }
    )


@_api
def output_page_thumb(job_id: str, name: str, page: int = 0, max_px: int = 400) -> bytes:
    """PNG of one page of a rendered PDF output (``<root>/jobs/<job_id>/output/<name>``)."""
    import pymupdf

    out_dir = _job_dir(job_id) / "output"
    path = out_dir / name
    if Path(name).name != name or not path.is_file():
        raise DomainError(ErrorCode.NOT_FOUND, "output not found")
    try:
        with pymupdf.open(path, filetype="pdf") as pdf:
            if not 0 <= page < pdf.page_count:
                raise DomainError(ErrorCode.NOT_FOUND, f"page {page} does not exist")
            sheet = pdf.load_page(page)
            zoom = max(16, min(int(max_px), 4096)) / max(sheet.rect.width, sheet.rect.height)
            pix = sheet.get_pixmap(
                matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False
            )
            return bytes(pix.tobytes("png"))
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(ErrorCode.CORRUPT_FILE, f"cannot read the output: {exc}") from exc


@_api
def delete_job(job_id: str) -> None:
    """Remove the outputs of a render job."""
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)


@_api
def clear() -> None:
    """Remove every document and job."""
    for sub in ("documents", "jobs", "tmp"):
        shutil.rmtree(_root() / sub, ignore_errors=True)
        (_root() / sub).mkdir(parents=True, exist_ok=True)
    _occ_cache.clear()


# ----------------------------------------------------------------------------- dispatcher

_PUBLIC: dict[str, Callable[..., Any]] = {
    "info": info,
    "papers": papers,
    "presets": presets,
    "printers": printers,
    "calibration_pdf": calibration_pdf,
    "ingest": ingest,
    "get_document": get_document,
    "cad_info": cad_info,
    "output_page_thumb": output_page_thumb,
    "set_physical_size": set_physical_size,
    "page_preview": page_preview,
    "delete_document": delete_document,
    "export_document": export_document,
    "import_document": import_document,
    "plan": plan,
    "sheet_preview": sheet_preview,
    "render": render,
    "delete_job": delete_job,
    "clear": clear,
}


def call_json(name: str, args_json: str = "[]", kwargs_json: str = "{}") -> str:
    """Call a bridge function and always answer JSON: ``{"ok": true, "result": ...}`` or
    ``{"ok": false, "error": <problem+json>}``. ``bytes`` results are not representable: use the
    functions directly for previews and PDFs."""
    try:
        fn = _PUBLIC.get(name)
        if fn is None:
            raise DomainError(ErrorCode.NOT_FOUND, f"unknown bridge function {name!r}")
        result = fn(*json.loads(args_json), **json.loads(kwargs_json))
        if isinstance(result, bytes | bytearray):
            raise DomainError(ErrorCode.INVALID_SETTINGS, f"{name} returns binary data")
        return json.dumps({"ok": True, "result": json.loads(result) if result else None})
    except Exception as exc:
        return json.dumps({"ok": False, "error": _bridge_error(exc).to_dict()})
