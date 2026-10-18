"""PDF output: viewer prefs, metadata, job attachment, file names, printer correction."""

import json
import os
import re
import string
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymupdf

from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.plan import Plan
from a0td.domain.planner import printer_correction_matrix
from a0td.domain.settings import JobSettings
from a0td.imaging import vips
from a0td.render.color import effective_dpi
from a0td.runtime import is_web
from a0td.version import __version__

JOB_ATTACHMENT = "a0td-job.json"
NAME_MAX = 60
MAX_ZIP_PIXELS = 50_000_000 if is_web() else 150_000_000  # per page image in the ZIP outputs
_VIEWER_PREFS = "<</PrintScaling/None/Duplex/Simplex/PickTrayByPDFSize true>>"
_TEMPLATE_KEYS = ("name", "paper", "orientation", "cols", "rows", "scale")


# ----------------------------------------------------------------------------- file names


def sanitize_name(name: str, max_len: int = NAME_MAX) -> str:
    """ASCII ``[A-Za-z0-9_-]`` only, at most ``max_len`` characters (SAL-03)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_name).strip("_-")
    return (cleaned[:max_len].strip("_-")) or "document"


def scale_text(scale: float) -> str:
    """``0.2378`` -> ``23.78``; ``1.0`` -> ``100``."""
    return f"{scale * 100:.2f}".rstrip("0").rstrip(".")


def build_filename(
    template: str, *, name: str, plan: Plan, suffix: str = "", ext: str = "pdf"
) -> str:
    """Apply the filename template of SAL-03 and sanitize the result (adds ``.pdf``)."""
    values = {
        "name": sanitize_name(name),
        "paper": plan.paper.id,
        "orientation": plan.orientation.value,
        "cols": plan.grid.cols,
        "rows": plan.grid.rows,
        "scale": scale_text(plan.scale),
    }
    try:
        fields = {f for _, f, _, _ in string.Formatter().parse(template) if f}
    except ValueError as exc:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            f"invalid filename template: {exc}",
            errors=[{"loc": "output.filename_template", "message": str(exc)}],
        ) from exc
    unknown = fields - set(_TEMPLATE_KEYS)
    if unknown:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            f"unknown filename template field(s): {', '.join(sorted(unknown))}",
            errors=[{"loc": "output.filename_template", "message": "unknown field"}],
        )
    try:
        stem = template.format(**values)
    except (ValueError, IndexError, KeyError) as exc:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            f"invalid filename template: {exc}",
            errors=[{"loc": "output.filename_template", "message": str(exc)}],
        ) from exc
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-") or "output"
    return f"{stem}{suffix}.{ext}"


def unique_name(name: str, taken: set[str]) -> str:
    """``name`` or ``name-2.pdf``... so two outputs never share a file name."""
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    i = 2
    while f"{stem}-{i}{dot}{ext}" in taken:
        i += 1
    return f"{stem}-{i}{dot}{ext}"


# --------------------------------------------------------------------- printer correction


def apply_correction(page: pymupdf.Page, kx: float, ky: float) -> None:
    """Scale everything on the page by ``1/kx, 1/ky`` around its centre (SPEC 5.5).

    The page content is wrapped in one ``cm``; the correction therefore covers tile content and
    marks alike. A no-op for an uncalibrated printer.
    """
    if kx == 1.0 and ky == 1.0:
        return
    a, b, c, d, e, f = printer_correction_matrix(page.rect.width, page.rect.height, kx, ky)
    doc = page.parent
    data = page.read_contents()
    head = f"q {a:.8f} {b:.8f} {c:.8f} {d:.8f} {e:.5f} {f:.5f} cm\n".encode()
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, head + data + b"\nQ\n")
    page.set_contents(xref)


# ------------------------------------------------------------------------------ images

IMAGE_EXT = {"png_zip": "png", "jpeg_zip": "jpg", "tiff_zip": "tif"}


def encode_pixmap(pix: pymupdf.Pixmap, fmt: str, jpeg_quality: int, dpi: float) -> bytes:
    """PNG / JPEG / TIFF bytes of a pixmap, carrying the resolution (``pix`` stays alive)."""
    im: Any = vips.Image.new_from_memory(pix.samples_mv, pix.width, pix.height, pix.n, "uchar")
    ppm = dpi / 25.4
    im = im.copy(xres=ppm, yres=ppm)
    if fmt == "png":
        return bytes(im.write_to_buffer(".png", compression=6))
    if fmt == "jpg":
        return bytes(im.write_to_buffer(".jpg", Q=jpeg_quality, optimize_coding=True))
    return bytes(im.tiffsave_buffer(compression="deflate", resunit="inch"))


def _entry_names(names: list[str], ext: str) -> list[str]:
    """Unique, separator-free ZIP entry names (``stem.ext``)."""
    taken: set[str] = set()
    out: list[str] = []
    for stem in names:
        clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-") or "page"
        name = unique_name(f"{clean}.{ext}", taken)
        taken.add(name)
        out.append(name)
    return out


@dataclass(slots=True)
class ZipResult:
    pages: int
    reduced_dpi: float | None = None  # lowest dpi used when a page exceeded the pixel cap


def write_image_zip(
    pdf: Path, names: list[str], zip_path: Path, *, fmt: str, dpi: int, jpeg_quality: int
) -> ZipResult:
    """One image per page of ``pdf`` (``names[i]`` is the file stem of page ``i``) in a ZIP.

    Pages that would exceed ``MAX_ZIP_PIXELS`` are drawn at a lower resolution (reported in the
    result and tagged in the image). The ZIP is written to ``.part`` and renamed when complete.
    """
    ext = IMAGE_EXT[fmt]
    entries = _entry_names(names, ext)
    part = zip_path.with_name(zip_path.name + ".part")
    reduced: float | None = None
    try:
        with (
            pymupdf.open(pdf) as doc,
            zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as zf,
        ):
            for page, entry in zip(doc, entries, strict=True):
                eff = effective_dpi(page.rect.width, page.rect.height, dpi, MAX_ZIP_PIXELS)
                if eff < dpi:
                    reduced = eff if reduced is None else min(reduced, eff)
                pix = page.get_pixmap(dpi=max(1, int(eff)), alpha=False)
                eff_real = pix.width / (page.rect.width / 72.0)
                zf.writestr(entry, encode_pixmap(pix, ext, jpeg_quality, eff_real))
                del pix
        os.replace(part, zip_path)
    finally:
        part.unlink(missing_ok=True)
    return ZipResult(len(entries), reduced)


# ---------------------------------------------------------------------------- finishing


def job_attachment(settings: JobSettings, plans: list[Plan], now: datetime) -> bytes:
    """The ``a0td-job.json`` payload: settings + a summary of every plan."""
    payload: dict[str, Any] = {
        "app": "a0-to-domestic",
        "version": __version__,
        "created": now.astimezone(UTC).isoformat(timespec="seconds"),
        "settings": settings.model_dump(mode="json"),
        "plans": [
            {
                "page_index": p.page_index,
                "paper": p.paper.model_dump(mode="json"),
                "orientation": p.orientation.value,
                "scale": p.scale,
                "scale_label": p.scale_label,
                "grid": p.grid.model_dump(mode="json"),
                "counts": p.counts.model_dump(mode="json"),
            }
            for p in plans
        ],
    }
    return json.dumps(payload, indent=1, ensure_ascii=False).encode("utf-8")


def pdf_date(now: datetime) -> str:
    return "D:" + now.astimezone(UTC).strftime("%Y%m%d%H%M%S") + "Z"


def finalize_pdf(
    doc: pymupdf.Document,
    path: Path,
    *,
    title: str,
    settings: JobSettings,
    plans: list[Plan],
    now: datetime,
) -> None:
    """Viewer preferences (SAL-02), metadata, the job attachment; then save compactly."""
    doc.xref_set_key(doc.pdf_catalog(), "ViewerPreferences", _VIEWER_PREFS)
    stamp = pdf_date(now)
    doc.set_metadata(
        {
            "title": title,
            "author": "",
            "subject": "Tiled print generated by a0-to-domestic",
            "keywords": "a0-to-domestic",
            "creator": f"a0-to-domestic {__version__}",
            "producer": f"a0-to-domestic {__version__} (PyMuPDF {pymupdf.__version__})",
            "creationDate": stamp,
            "modDate": stamp,
        }
    )
    doc.embfile_add(
        JOB_ATTACHMENT,
        job_attachment(settings, plans, now),
        filename=JOB_ATTACHMENT,
        ufilename=JOB_ATTACHMENT,
        desc="Job settings (a0-to-domestic)",
    )
    doc.save(path, garbage=4, deflate=True, use_objstms=1)
