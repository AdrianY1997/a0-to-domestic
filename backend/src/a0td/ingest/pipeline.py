"""``ingest_document``: detect -> convert -> canonical -> analyze (+ occupancy) -> preview.

No database and no HTTP: the caller (the ingest job, WP-08) owns the document directory layout
``documents/{id}/`` and passes it as ``workdir``. Files written there (SPEC 11.2):

``canonical.pdf`` | ``page-{n}.tif``, ``pages/{n}/{preview.webp,thumb.webp,occupancy*.png}`` and
``meta.json`` (the serialized :class:`IngestResult`). ``n`` is the 0-based page index.
"""

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from a0td.config import AppConfig, get_config
from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest.analyze import analyze_document, apply_physical_size
from a0td.ingest.converters.base import ConvertContext
from a0td.ingest.converters.registry import ConverterRegistry, default_registry
from a0td.ingest.detect import detect, supported_format_ids
from a0td.ingest.models import (
    CANONICAL_PDF,
    MIME_TYPES,
    CadOptions,
    CanonicalDocument,
    DocFormat,
    IngestResult,
    Page,
    PageKind,
    Progress,
    ProgressCallback,
    page_dir,
)
from a0td.ingest.occupancy import build_occupancy
from a0td.ingest.preview import write_previews
from a0td.ingest.vector_size import apply_vector_size, is_resizable_vector, plan_vector_size

SCRATCH_DIR = ".work"


@dataclass(frozen=True, slots=True)
class SizeOverride:
    """User-fixed physical size for raster pages (exactly one field), applied during ingest."""

    paper: str | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    dpi: float | None = None


def _emit(
    cb: ProgressCallback | None, stage: str, pct: float, key: str | None = None, **p: object
) -> None:
    if cb is not None:
        cb(Progress(stage, pct, key, dict(p)))


def _source_for(result: IngestResult, workdir: Path, index: int) -> Path:
    if result.kind == "pdf":
        assert result.canonical_pdf is not None
        return workdir / result.canonical_pdf
    return workdir / result.page_files[index]


def _finish_page(result: IngestResult, workdir: Path, page: Page, preview_px: int) -> None:
    source = _source_for(result, workdir, page.index)
    out_dir = page_dir(workdir, page.index)
    out_dir.mkdir(parents=True, exist_ok=True)
    build_occupancy(page, source, out_dir)
    write_previews(source, page.index, out_dir, preview_px)


def _override_size(p: Page, workdir: Path, o: SizeOverride, max_page_mm: float) -> Page:
    kwargs = {
        "paper": o.paper,
        "width_mm": o.width_mm,
        "height_mm": o.height_mm,
        "dpi": o.dpi,
        "max_page_mm": max_page_mm,
    }
    if p.kind is PageKind.RASTER:
        return apply_physical_size(p, **kwargs)  # type: ignore[arg-type]
    if is_resizable_vector(workdir, p):
        return apply_vector_size(workdir, p, **kwargs)  # type: ignore[arg-type]
    return p


def ingest_document(
    src_path: Path,
    workdir: Path,
    *,
    password: str | None = None,
    progress: ProgressCallback | None = None,
    config: AppConfig | None = None,
    registry: ConverterRegistry | None = None,
    filename: str | None = None,
    size_override: SizeOverride | None = None,
    cad_options: CadOptions | None = None,
) -> IngestResult:
    """Normalize ``src_path`` into ``workdir`` and describe it. Raises ``DomainError`` with the
    codes of SPEC 10 (UNSUPPORTED_FORMAT, FORMAT_NOT_AVAILABLE_IN_IMAGE, PASSWORD_REQUIRED,
    PASSWORD_INVALID, PRINT_NOT_ALLOWED, CORRUPT_FILE, TOO_MANY_PAGES, PIXEL_LIMIT_EXCEEDED,
    PAGE_TOO_LARGE, CONVERSION_*).

    ``filename`` (default: the name of ``src_path``) is only a hint for weak signatures.
    ``cad_options`` (layout, units, scale, colour policy) only matters for DXF / DWG.
    """
    src_path = src_path.resolve()
    cfg = config or get_config()
    reg = registry or default_registry()
    workdir.mkdir(parents=True, exist_ok=True)
    scratch = workdir / SCRATCH_DIR

    try:
        _emit(progress, "detect", 1, "ingest.detect")
        det = detect(src_path, filename)
        if det.format is None:
            raise DomainError(
                ErrorCode.UNSUPPORTED_FORMAT,
                det.reason or "unrecognized file content",
                params={"supported": supported_format_ids()},
            )
        fmt: DocFormat = det.format
        converter = reg.resolve(fmt)

        ctx = ConvertContext(
            config=cfg,
            password=password,
            format=fmt,
            progress=progress,
            scratch=scratch,
            cad=cad_options,
        )
        canon: CanonicalDocument = converter.convert(src_path, workdir, ctx)

        _emit(progress, "analyze", 50, "ingest.analyze")
        pages = analyze_document(canon, float(cfg.max_page_mm))
        if size_override is not None:
            pages = [
                _override_size(p, workdir, size_override, float(cfg.max_page_mm)) for p in pages
            ]

        result = IngestResult(
            format=fmt,
            mime=MIME_TYPES[fmt],
            kind=canon.kind,
            canonical_pdf=canon.pdf_path.relative_to(workdir).as_posix()
            if canon.pdf_path
            else None,
            page_files=[
                cp.file.relative_to(workdir).as_posix() for cp in canon.pages if cp.file is not None
            ],
            pages=pages,
            warnings=list(canon.warnings),
            cad=canon.cad,
        )
        for i, page in enumerate(pages):
            _emit(
                progress,
                "preview",
                50 + 49 * i / len(pages),
                "ingest.page",
                k=i + 1,
                n=len(pages),
            )
            _finish_page(result, workdir, page, cfg.preview_max_px)
        result.save(workdir)
        _emit(progress, "done", 100, "ingest.done")
        return result
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(
            ErrorCode.CONVERSION_FAILED, f"ingest failed: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def set_physical_size(
    workdir: Path,
    page_index: int,
    *,
    paper: str | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    dpi: float | None = None,
    config: AppConfig | None = None,
) -> IngestResult:
    """Apply the user's physical-size override (ANA-03) to a raster page (or a size-less SVG page).

    Recomputes that page's occupancy maps (they depend on the physical size) and rewrites
    ``meta.json``. The previews do not depend on it.
    """
    cfg = config or get_config()
    result = IngestResult.load(workdir)
    if not 0 <= page_index < len(result.pages):
        raise DomainError(ErrorCode.NOT_FOUND, f"page {page_index} does not exist")
    old = result.pages[page_index]
    size_args = {
        "paper": paper,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "dpi": dpi,
        "max_page_mm": float(cfg.max_page_mm),
    }
    if not is_resizable_vector(workdir, old):
        new = apply_physical_size(old, **size_args)  # type: ignore[arg-type]
        result.pages[page_index] = new
        build_occupancy(
            new, _source_for(result, workdir, page_index), page_dir(workdir, page_index)
        )
        result.save(workdir)
        return result
    return _set_vector_size(workdir, result, page_index, size_args)


def _set_vector_size(
    workdir: Path, result: IngestResult, page_index: int, size_args: dict[str, float | str | None]
) -> IngestResult:
    """Vector page: the rescaled PDF is built aside and ``canonical.pdf`` is replaced last; the
    occupancy maps and ``meta.json`` are rolled back if anything fails first."""
    old = result.pages[page_index]
    new, tmp_pdf = plan_vector_size(workdir, old, **size_args)  # type: ignore[arg-type]
    pdir = page_dir(workdir, page_index)
    backup = workdir / f".pages-backup-{uuid.uuid4().hex}"
    had_dir = pdir.is_dir()
    meta_touched = False
    updated = result.model_copy(update={"pages": list(result.pages)})
    updated.pages[page_index] = new
    try:
        if had_dir:
            shutil.copytree(pdir, backup)
        pdir.mkdir(parents=True, exist_ok=True)
        build_occupancy(new, tmp_pdf, pdir)
        meta_touched = True
        updated.save(workdir)
        os.replace(tmp_pdf, workdir / CANONICAL_PDF)
    except BaseException:
        try:
            if meta_touched:
                result.save(workdir)
        finally:
            shutil.rmtree(pdir, ignore_errors=True)
            if had_dir and backup.is_dir():
                shutil.move(backup, pdir)
        raise
    finally:
        tmp_pdf.unlink(missing_ok=True)
        shutil.rmtree(backup, ignore_errors=True)
    return updated


__all__ = ["SizeOverride", "ingest_document", "set_physical_size"]
