"""Physical-size override for vector pages whose size was assumed (SVG without physical units).

Rasters are resized by metadata only (:func:`a0td.ingest.analyze.apply_physical_size`). A vector
page has real geometry, so the canonical PDF itself is scaled (a wrapping ``cm`` and the boxes).
"""

import json
import math
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import pymupdf

from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.domain.units import MM_PER_INCH
from a0td.ingest.analyze import (
    _check_page_mm,
    _fit_mm_per_px,
    _size_warnings,
    _source_size,
    _standard_match,
)
from a0td.ingest.models import CANONICAL_PDF, Page

NATIVE_SIZE_FILE = "native-size.json"

_BOXES = ("MediaBox", "CropBox", "TrimBox", "BleedBox", "ArtBox")
_MAX_PARENT_DEPTH = 64
_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def is_resizable_vector(workdir: Path, page: Page) -> bool:
    """An SVG page whose size came from the user or was assumed (it has a native size on disk)."""
    return page.px is None and (workdir / NATIVE_SIZE_FILE).is_file()


def _scaled_box(raw: str, factor: float) -> str:
    parts = raw.strip("[] \t\r\n").split()
    if len(parts) != 4 or not all(_NUMBER.match(x) for x in parts):
        raise DomainError(ErrorCode.CORRUPT_FILE, "a page box of the canonical PDF is malformed")
    return "[" + " ".join(f"{float(x) * factor:.4f}" for x in parts) + "]"


def _inherited_box(doc: pymupdf.Document, xref: int, key: str) -> str | None:
    """Raw array of ``key`` on the page or the nearest ancestor that defines it (else ``None``)."""
    current = xref
    for _ in range(_MAX_PARENT_DEPTH):
        kind, raw = doc.xref_get_key(current, key)
        if kind == "array":
            return raw
        if kind == "xref":  # indirect array
            return doc.xref_object(int(raw.split()[0]), compressed=True)
        pkind, praw = doc.xref_get_key(current, "Parent")
        if pkind != "xref":
            return None
        current = int(praw.split()[0])
    return None


def rescale_pdf_pages(pdf: Path, factor: float) -> None:
    """Scale every page of ``pdf`` by ``factor`` about the origin, in place.

    All five page boxes are scaled, including the ones inherited from the page tree (they are
    written explicitly on the page; the shared parent is left alone).
    """
    if not (math.isfinite(factor) and factor > 0):
        raise DomainError(ErrorCode.INVALID_SETTINGS, "the scale factor must be greater than 0")
    tmp = pdf.with_name(f"{pdf.name}.{uuid.uuid4().hex}.tmp")
    try:
        with pymupdf.open(pdf) as doc:
            for index in range(doc.page_count):
                page = doc.load_page(index)
                page.clean_contents()
                contents = page.get_contents()
                pre, post = doc.get_new_xref(), doc.get_new_xref()
                doc.update_object(pre, "<<>>")
                doc.update_stream(pre, f"q {factor:.8f} 0 0 {factor:.8f} 0 0 cm\n".encode())
                doc.update_object(post, "<<>>")
                doc.update_stream(post, b"Q\n")
                refs = " ".join(f"{x} 0 R" for x in (pre, *contents, post))
                xref = page.xref
                for key in _BOXES:
                    raw = _inherited_box(doc, xref, key)
                    if raw is not None:
                        doc.xref_set_key(xref, key, _scaled_box(raw, factor))
                doc.xref_set_key(xref, "Contents", f"[{refs}]")
            doc.save(tmp, garbage=3, deflate=True)
        os.replace(tmp, pdf)
    finally:
        tmp.unlink(missing_ok=True)


def _pdf_page_mm(pdf: Path, index: int) -> tuple[float, float]:
    with pymupdf.open(pdf) as doc:
        rect = doc.load_page(index).rect
    return rect.width * MM_PER_INCH / 72, rect.height * MM_PER_INCH / 72


def plan_vector_size(
    workdir: Path,
    page: Page,
    *,
    paper: str | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    dpi: float | None = None,
    max_page_mm: float = 10000.0,
) -> tuple[Page, Path]:
    """Compute the user override of the size of an SVG page flagged ``PHYSICAL_SIZE_ASSUMED``.

    Nothing canonical is touched: the rescaled PDF is a temporary file inside ``workdir`` and the
    caller commits it with ``os.replace`` (or deletes it). The factor is relative to the size of
    the PDF on disk (not to ``meta.json``), so retrying after a partial failure never scales twice.
    """
    given = [v for v in (paper, width_mm, height_mm, dpi) if v is not None]
    if len(given) != 1:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            "exactly one of paper, width_mm, height_mm or dpi is required",
        )
    canonical = workdir / CANONICAL_PDF
    w, h = _pdf_page_mm(canonical, page.index)
    if paper is not None:
        factor = _fit_mm_per_px(round(w * 1000), round(h * 1000), _source_size(paper)) * 1000
    else:
        value = float(next(v for v in (width_mm, height_mm, dpi) if v is not None))
        if not math.isfinite(value) or value <= 0:
            raise DomainError(ErrorCode.INVALID_SETTINGS, "the value must be greater than 0")
        if width_mm is not None:
            factor = value / w
        elif height_mm is not None:
            factor = value / h
        else:
            native = _native_px(workdir)
            factor = native[0] * MM_PER_INCH / value / w
    _check_page_mm(w * factor, h * factor, max_page_mm, page.index)

    fd, name = tempfile.mkstemp(prefix="canonical-", suffix=".tmp.pdf", dir=workdir)
    os.close(fd)
    tmp = Path(name)
    try:
        shutil.copyfile(canonical, tmp)
        rescale_pdf_pages(tmp, factor)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    nw, nh = round(w * factor, 3), round(h * factor, 3)
    meta_factor = (w * factor) / page.width_mm if page.width_mm > 0 else factor
    boxes = {
        k: tuple(round(c * meta_factor, 4) for c in box)  # type: ignore[misc]
        for k, box in page.boxes_mm.items()
    }
    match = _standard_match(nw, nh)
    kept = [
        wn
        for wn in page.warnings
        if wn.code not in (WarningCode.PHYSICAL_SIZE_ASSUMED, WarningCode.NONSTANDARD_SIZE)
    ]
    new = page.model_copy(
        update={
            "width_mm": nw,
            "height_mm": nh,
            "boxes_mm": boxes,
            "standard_match": match,
            "font_min_mm": _scaled(page.font_min_mm, meta_factor),
            "font_p10_mm": _scaled(page.font_p10_mm, meta_factor),
            "line_min_mm": _scaled(page.line_min_mm, meta_factor),
            "warnings": [*_size_warnings(match, assumed=False), *kept],
        }
    )
    return new, tmp


def _scaled(value: float | None, factor: float) -> float | None:
    return None if value is None else value * factor


def apply_vector_size(workdir: Path, page: Page, **kwargs: float | str | None) -> Page:
    """:func:`plan_vector_size` committed straight away (ingest-time override, nothing to undo)."""
    new, tmp = plan_vector_size(workdir, page, **kwargs)  # type: ignore[arg-type]
    try:
        os.replace(tmp, workdir / CANONICAL_PDF)
    finally:
        tmp.unlink(missing_ok=True)
    return new


def _native_px(workdir: Path) -> tuple[float, float]:
    try:
        data = json.loads((workdir / NATIVE_SIZE_FILE).read_text(encoding="utf-8"))
        return float(data["px"][0]), float(data["px"][1])
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS, "the native size of this vector page is unknown"
        ) from exc
