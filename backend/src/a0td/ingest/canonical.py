"""Normalization to the canonical document (SPEC 7.3).

* PDF -> ``canonical.pdf``: decrypted, repaired, annotations / form fields baked into the content,
  ``/Rotate`` baked into the content (every page is upright), origin at the CropBox corner.
  The page is rewritten in place (``MediaBox`` / ``CropBox`` and a wrapping ``cm`` matrix) so layers
  (OCG) and every other resource survive. The remaining boxes are returned in canonical mm.
* Raster -> ``page-{n}.tif``: tiled deflate TIFF, sRGB 8-bit / gray 8-bit / 1-bit, alpha flattened
  on white, CMYK and ICC converted to sRGB. ``n`` is the 0-based page index.
"""

import contextlib
import math
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.domain.page import Box
from a0td.domain.plan import PlanWarning
from a0td.domain.settings import SourceBox
from a0td.domain.units import pt_to_mm
from a0td.imaging import backend_name, icc_supported, vips
from a0td.ingest.converters.base import ConvertContext, run_tool, tool_available
from a0td.ingest.models import (
    CANONICAL_PDF,
    CanonicalDocument,
    CanonicalPage,
    DocFormat,
)

_QPDF_OK = (0, 3)  # 3 = finished with warnings (what a repaired file yields)
_SKIP_ANNOT_TYPES = frozenset({"Popup", "Link"})
_TILE = 256

# Pillow's decompression-bomb guard, set once from the config defaults (never per call).
_MAX_PIXELS = int(AppConfig.model_fields["max_megapixels"].default) * 1_000_000
Image.MAX_IMAGE_PIXELS = _MAX_PIXELS


# ----------------------------------------------------------------------------------- PDF


def _r(value: float) -> float:
    return round(value, 4)


def _try_open_pdf(path: Path) -> pymupdf.Document | None:
    try:
        return pymupdf.open(path, filetype="pdf")
    except Exception:
        return None


def _healthy(doc: pymupdf.Document) -> bool:
    try:
        if doc.page_count < 1:
            return False
        doc.load_page(0)
    except Exception:
        return False
    return True


def _repair_with_qpdf(src: Path, scratch: Path, ctx: ConvertContext) -> pymupdf.Document:
    """Rewrite a damaged file with qpdf and open the result, or raise ``CORRUPT_FILE``."""
    if not tool_available("qpdf"):
        raise DomainError(ErrorCode.CORRUPT_FILE, "PDF is damaged and could not be repaired")
    out = scratch / "qpdf-repaired.pdf"
    try:
        run_tool(
            ["qpdf", src.resolve(), out.resolve()],
            scratch=scratch,
            timeout_s=ctx.config.job_timeout_s,
            memory_mb=ctx.config.job_memory_mb,
            ok_codes=_QPDF_OK,
        )
    except DomainError as exc:
        raise DomainError(
            ErrorCode.CORRUPT_FILE, "PDF is damaged and could not be repaired"
        ) from exc
    doc = _try_open_pdf(out)
    if doc is None or (not doc.needs_pass and not _healthy(doc)):
        raise DomainError(ErrorCode.CORRUPT_FILE, "PDF is damaged and could not be repaired")
    return doc


def _open_pdf(src: Path, ctx: ConvertContext, scratch: Path) -> tuple[pymupdf.Document, bool]:
    """Open, repair if needed, and decrypt. Returns ``(doc, repaired)``."""
    repaired = False
    doc = _try_open_pdf(src)
    if doc is None or (not doc.needs_pass and not _healthy(doc)):
        if doc is not None:
            doc.close()
        doc = _repair_with_qpdf(src, scratch, ctx)
        repaired = True
    if doc.needs_pass:
        if not ctx.password:
            doc.close()
            raise DomainError(ErrorCode.PASSWORD_REQUIRED, "the PDF requires a password")
        if not doc.authenticate(ctx.password):
            doc.close()
            raise DomainError(ErrorCode.PASSWORD_INVALID, "the password is not valid")
        if not _healthy(doc):
            doc.close()
            raise DomainError(ErrorCode.CORRUPT_FILE, "the PDF has no readable pages")
    return doc, repaired or bool(doc.is_repaired)


def _has_flattenable(page: pymupdf.Page) -> bool:
    for annot in page.annots() or ():
        if annot.type[1] not in _SKIP_ANNOT_TYPES:
            return True
    return page.first_widget is not None


def _explicit_box(doc: pymupdf.Document, page: pymupdf.Page, key: str) -> bool:
    return doc.xref_get_key(page.xref, key)[0] == "array"


def _mm_box(rect: pymupdf.Rect) -> Box:
    return (
        _r(pt_to_mm(rect.x0)),
        _r(pt_to_mm(rect.y0)),
        _r(pt_to_mm(rect.x1)),
        _r(pt_to_mm(rect.y1)),
    )


def _neutralize(doc: pymupdf.Document) -> None:
    """Drop JavaScript, actions, attachments and links: nothing active survives (SPEC 14)."""
    doc.scrub(
        attached_files=True,
        clean_pages=False,
        embedded_files=True,
        hidden_text=False,
        javascript=True,
        metadata=False,
        redactions=False,
        redact_images=0,
        remove_links=True,
        reset_fields=False,
        reset_responses=False,
        thumbnails=True,
        xml_metadata=False,
    )
    cat = doc.pdf_catalog()
    for key in ("OpenAction", "AA", "AcroForm", "URI", "Names", "AF"):
        doc.xref_set_key(cat, key, "null")


def _canonicalize_page(doc: pymupdf.Document, index: int, max_page_mm: float) -> CanonicalPage:
    page = doc.load_page(index)
    # Unrotated PDF user space -> display space (y down, origin at the rotated crop top-left).
    # ``page.transformation_matrix`` is not usable when /Rotate != 0 (it drops the crop offset),
    # so build the unrotated y-up -> crop-local y-down matrix from the CropBox explicitly.
    # ``page.cropbox`` is y-down relative to the MediaBox top; /UserUnit comes in as the scale.
    uu = abs(page.transformation_matrix.a)
    crop, media_box = page.cropbox, page.mediabox
    unrotated = pymupdf.Matrix(uu, 0, 0, -uu, -uu * crop.x0, uu * (media_box.y1 - crop.y0))
    to_display = unrotated * page.rotation_matrix
    rect = page.rect
    width_pt, height_pt = rect.width, rect.height
    if (
        not (math.isfinite(width_pt) and math.isfinite(height_pt))
        or width_pt <= 0
        or height_pt <= 0
    ):
        raise DomainError(ErrorCode.CORRUPT_FILE, f"page {index + 1} has an invalid size")
    width_mm, height_mm = pt_to_mm(width_pt), pt_to_mm(height_pt)
    if max(width_mm, height_mm) > max_page_mm:
        raise DomainError(
            ErrorCode.PAGE_TOO_LARGE,
            f"page {index + 1} is {max(width_mm, height_mm):.0f} mm, "
            f"the limit is {max_page_mm:.0f} mm",
            params={"page": index + 1, "limit_mm": max_page_mm},
        )

    media = (pymupdf.Rect(page.mediabox) * to_display).normalize()
    boxes: dict[SourceBox, Box] = {
        SourceBox.CROP: (0.0, 0.0, _r(width_mm), _r(height_mm)),
        SourceBox.MEDIA: _mm_box(media),
    }
    for key, which, attr in (
        ("TrimBox", SourceBox.TRIM, page.trimbox),
        ("BleedBox", SourceBox.BLEED, page.bleedbox),
    ):
        if _explicit_box(doc, page, key):
            # trim / bleed (like the crop) are y-down from the MediaBox top: flip to y-up first
            up = pymupdf.Rect(attr.x0, media_box.y1 - attr.y1, attr.x1, media_box.y1 - attr.y0)
            boxes[which] = _mm_box((up * to_display).normalize())

    # Rewrite the page: wrap the content in a matrix that bakes /Rotate, the crop origin and
    # UserUnit, then replace the boxes (canonical user space is y-up with the crop at the origin).
    m = to_display * pymupdf.Matrix(1, 0, 0, -1, 0, height_pt)  # display y-down -> canonical y-up
    page.clean_contents()
    contents = page.get_contents()
    pre = doc.get_new_xref()
    doc.update_object(pre, "<<>>")
    doc.update_stream(
        pre, f"q {m.a:.8f} {m.b:.8f} {m.c:.8f} {m.d:.8f} {m.e:.5f} {m.f:.5f} cm\n".encode()
    )
    post = doc.get_new_xref()
    doc.update_object(post, "<<>>")
    doc.update_stream(post, b"Q\n")
    refs = " ".join(f"{x} 0 R" for x in (pre, *contents, post))
    xref = page.xref
    doc.xref_set_key(xref, "Contents", f"[{refs}]")
    doc.xref_set_key(
        xref,
        "MediaBox",
        f"[{media.x0:.4f} {height_pt - media.y1:.4f} {media.x1:.4f} {height_pt - media.y0:.4f}]",
    )
    doc.xref_set_key(xref, "CropBox", f"[0 0 {width_pt:.4f} {height_pt:.4f}]")
    doc.xref_set_key(xref, "Rotate", "0")
    for key in ("TrimBox", "BleedBox", "ArtBox", "UserUnit", "Annots", "AA"):
        doc.xref_set_key(xref, key, "null")
    return CanonicalPage(
        index=index, boxes_mm=boxes, width_mm=_r(width_mm), height_mm=_r(height_mm)
    )


def canonicalize_pdf(src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
    """PDF -> ``work/canonical.pdf`` (SPEC 7.3). Raises the PDF-specific ``DomainError`` codes."""
    cfg = ctx.config
    pymupdf.TOOLS.mupdf_display_errors(False)
    scratch = ctx.scratch_dir(work)
    ctx.report("convert", 5, "ingest.open")
    doc, repaired = _open_pdf(src, ctx, scratch)
    warnings: list[PlanWarning] = []
    try:
        if doc.page_count > cfg.max_pages:
            raise DomainError(
                ErrorCode.TOO_MANY_PAGES,
                f"{doc.page_count} pages, the limit is {cfg.max_pages}",
                params={"pages": doc.page_count, "limit": cfg.max_pages},
            )
        if not doc.permissions & pymupdf.PDF_PERM_PRINT:
            raise DomainError(ErrorCode.PRINT_NOT_ALLOWED, "the PDF does not allow printing")
        if repaired:
            warnings.append(PlanWarning(code=WarningCode.PDF_REPAIRED))

        flattened = [i + 1 for i in range(doc.page_count) if _has_flattenable(doc.load_page(i))]
        if flattened:
            doc.bake(annots=True, widgets=True)
            warnings.append(
                PlanWarning(code=WarningCode.ANNOTATIONS_FLATTENED, params={"pages": flattened})
            )
        _neutralize(doc)

        pages: list[CanonicalPage] = []
        for i in range(doc.page_count):
            pages.append(_canonicalize_page(doc, i, float(cfg.max_page_mm)))
            ctx.report(
                "convert",
                5 + 40 * (i + 1) / doc.page_count,
                "ingest.page",
                k=i + 1,
                n=doc.page_count,
            )

        out = work / CANONICAL_PDF
        doc.save(out, garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(ErrorCode.CORRUPT_FILE, f"cannot process the PDF: {exc}") from exc
    finally:
        doc.close()
    return CanonicalDocument(
        kind="pdf",
        source_format=DocFormat.PDF,
        pages=pages,
        pdf_path=out,
        warnings=warnings,
    )


# --------------------------------------------------------------------------------- raster

_SIMPLE_INTERPRETATIONS = frozenset(
    {"b-w", "srgb", "rgb", "multiband", "grey16", "rgb16", "cmyk", "histogram"}
)


def _pillow_dpi(src: Path, index: int) -> tuple[float, float] | None:
    """DPI stored in the file's metadata (Pillow reads JFIF / pHYs / EXIF / TIFF tags)."""
    try:
        with Image.open(src) as img:
            if index:
                img.seek(index)
            dpi = img.info.get("dpi")
    except Exception:
        return None
    if not dpi:
        return None
    try:
        x, y = float(dpi[0]), float(dpi[1])
    except (TypeError, ValueError, IndexError):
        return None
    if x <= 0 or y <= 0 or not math.isfinite(x) or not math.isfinite(y):
        return None
    return round(x, 4), round(y, 4)


def _load_vips(src: Path, index: int, fmt: DocFormat, *, random: bool) -> Any:
    kwargs: dict[str, object] = {"access": "random" if random else "sequential"}
    if fmt is DocFormat.TIFF:
        kwargs["page"] = index
    return vips.Image.new_from_file(str(src), **kwargs)


def _load_pillow(src: Path, limit_px: int) -> Any:
    """Fallback loader for formats libvips cannot read here (BMP): through Pillow + numpy."""
    import numpy as np
    from PIL import ImageOps

    with Image.open(src) as img:
        if img.width * img.height > limit_px:
            raise DomainError(ErrorCode.PIXEL_LIMIT_EXCEEDED, "image has too many pixels")
        img = ImageOps.exif_transpose(img)
        bilevel = img.mode == "1"
        if img.mode in ("1", "L"):
            img = img.convert("L")
        elif img.mode in ("LA", "La"):
            img = img.convert("LA")
        elif img.mode == "P":
            img = img.convert("RGBA" if "transparency" in img.info else "RGB")
        elif img.mode != "RGB" and img.mode != "RGBA":
            img = img.convert("RGB")
        arr = np.ascontiguousarray(np.asarray(img))
    bands = 1 if arr.ndim == 2 else arr.shape[2]
    out = vips.Image.new_from_memory(arr.tobytes(), arr.shape[1], arr.shape[0], bands, "uchar")
    interp = "b-w" if bands <= 2 else "srgb"
    out = out.copy(interpretation=interp)
    if bilevel:
        out = out.copy()
        out.set_type(vips.GValue.gint_type, "bits-per-sample", 1)
    return out


def _is_bilevel(im: Any) -> bool:
    return (
        im.bands == 1
        and bool(im.get_typeof("bits-per-sample"))
        and int(im.get("bits-per-sample")) == 1
    )


def _normalize(im: Any, notes: set[str] | None = None) -> Any:
    """Convert to 8-bit sRGB or 8-bit gray, alpha flattened on white (SPEC 7.3).

    ``notes`` collects ``"icc"`` when a colour profile (or CMYK) could only be approximated
    because the imaging backend has no colour engine (``ICC_IGNORED``).
    """
    interp = str(im.interpretation)
    if interp not in _SIMPLE_INTERPRETATIONS:
        im = im.colourspace("srgb")
        interp = "srgb"
    if im.format != "uchar":
        im = (im / 257.0).rint().cast("uchar") if im.format == "ushort" else im.cast("uchar")
    if interp == "cmyk":
        if notes is not None and not icc_supported():
            notes.add("icc")
        alpha = im.extract_band(4) if im.bands > 4 else None
        rgb = im.extract_band(0, n=4).icc_transform(
            "srgb", input_profile="cmyk", embedded=True, black_point_compensation=True
        )
        if alpha is not None:
            rgb = rgb.bandjoin(alpha).flatten(background=[255, 255, 255])
        return rgb.copy(interpretation="srgb")
    if im.bands <= 2:
        if im.bands == 2:
            im = im.flatten(background=[255])
        return im.copy(interpretation="b-w")
    if im.bands > 4:
        im = im.extract_band(0, n=3)
    if im.bands == 4:
        im = im.flatten(background=[255, 255, 255])
    im = im.copy(interpretation="srgb")
    if im.get_typeof("icc-profile-data"):
        profile = im.get("icc-profile-data")
        if isinstance(profile, bytes) and b"sRGB" not in profile:
            if notes is not None and not icc_supported():
                notes.add("icc")
            with contextlib.suppress(Exception):  # unusable profile: keep the pixel values
                im = im.icc_transform("srgb", embedded=True)
    return im


def _page_count(src: Path, fmt: DocFormat) -> int:
    if fmt is not DocFormat.TIFF:
        return 1
    try:
        head = vips.Image.new_from_file(str(src), access="sequential")
        if head.get_typeof("n-pages"):
            return max(1, int(head.get("n-pages")))
    except vips.Error as exc:
        raise DomainError(ErrorCode.CORRUPT_FILE, "the TIFF cannot be read") from exc
    return 1


def _convert_raster_page(
    src: Path, index: int, out: Path, fmt: DocFormat, ctx: ConvertContext, notes: set[str]
) -> CanonicalPage:
    limit_px = ctx.config.max_megapixels * 1_000_000
    try:
        try:
            im = _load_vips(src, index, fmt, random=False)
        except vips.Error:
            if fmt is not DocFormat.BMP or backend_name() != "pyvips":
                raise
            im = _load_pillow(src, limit_px)
        if im.width < 1 or im.height < 1 or im.width * im.height > limit_px:
            raise DomainError(
                ErrorCode.PIXEL_LIMIT_EXCEEDED,
                f"image is {im.width}x{im.height} px, the limit is {ctx.config.max_megapixels} Mpx",
                params={"limit_mpx": ctx.config.max_megapixels},
            )
        if im.get_typeof("orientation") and int(im.get("orientation")) > 1:
            im = _load_vips(src, index, fmt, random=True).autorot()
        bilevel = _is_bilevel(im)
        im = _normalize(im, notes)
        width, height = im.width, im.height
        save_kwargs: dict[str, object] = {
            "tile": True,
            "tile_width": _TILE,
            "tile_height": _TILE,
            "compression": "deflate",
            "bigtiff": width * height * im.bands > 2_000_000_000,
            "strip": True,
        }
        if bilevel:
            save_kwargs["bitdepth"] = 1
            save_kwargs["predictor"] = "none"
        else:
            save_kwargs["predictor"] = "horizontal"
        im.tiffsave(str(out), **save_kwargs)
    except DomainError:
        raise
    except vips.Error as exc:
        if type(exc).__name__ == "PixelLimitError":  # Pillow backend: decompression-bomb guard
            raise DomainError(
                ErrorCode.PIXEL_LIMIT_EXCEEDED,
                f"image has too many pixels, the limit is {ctx.config.max_megapixels} Mpx",
                params={"limit_mpx": ctx.config.max_megapixels},
            ) from exc
        raise DomainError(ErrorCode.CORRUPT_FILE, f"cannot decode the image: {exc}") from exc
    dpi = _pillow_dpi(src, index)
    return CanonicalPage(index=index, px=(width, height), dpi_meta=dpi, file=out)


def canonicalize_raster(
    src: Path, work: Path, ctx: ConvertContext, fmt: DocFormat
) -> CanonicalDocument:
    """Image -> ``work/page-{n}.tif`` per page (SPEC 7.3). Sizes are resolved later by analysis."""
    vips.cache_set_max(0)
    cfg = ctx.config
    n_pages = _page_count(src, fmt)
    if n_pages > cfg.max_pages:
        raise DomainError(
            ErrorCode.TOO_MANY_PAGES,
            f"{n_pages} pages, the limit is {cfg.max_pages}",
            params={"pages": n_pages, "limit": cfg.max_pages},
        )
    pages: list[CanonicalPage] = []
    notes: set[str] = set()
    for i in range(n_pages):
        out = work / f"page-{i}.tif"
        pages.append(_convert_raster_page(src, i, out, fmt, ctx, notes))
        ctx.report("convert", 5 + 40 * (i + 1) / n_pages, "ingest.page", k=i + 1, n=n_pages)
    warnings = [PlanWarning(code=WarningCode.ICC_IGNORED)] if "icc" in notes else []
    return CanonicalDocument(kind="raster", source_format=fmt, pages=pages, warnings=warnings)
