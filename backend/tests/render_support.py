"""Helpers of the render tests: rasterize output pages, reassemble sheets, compare images."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
import pyvips
from numpy.typing import NDArray

from a0td.domain.geometry import Rect
from a0td.domain.plan import Plan
from a0td.domain.settings import JobSettings, merge_settings
from a0td.domain.units import MM_PER_PT

Gray = NDArray[np.uint8]
FIXED_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
MARGINS_5 = {"top": 5, "right": 5, "bottom": 5, "left": 5}


def make_settings(**partial: Any) -> JobSettings:
    """Defaults with 5 mm margins (the CA-1 setting) and the given sections merged over them."""
    target = {"margins_mm": MARGINS_5, **partial.pop("target", {})}
    return merge_settings({"target": target, **partial})


def render_rect_gray(page: pymupdf.Page, rect_mm: Rect, dpi: float) -> Gray:
    """Gray render of ``rect_mm`` of ``page`` at ``dpi``, cropped to exactly ``round(w * ppm)``."""
    ppm = dpi / 25.4
    k = 1 / MM_PER_PT
    clip = pymupdf.Rect(rect_mm.x0 * k, rect_mm.y0 * k, rect_mm.x1 * k, rect_mm.y1 * k)
    z = dpi / 72.0
    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(z, z), clip=clip, colorspace=pymupdf.csGRAY, alpha=False
    )
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    ox = round(rect_mm.x0 * ppm) - pix.x
    oy = round(rect_mm.y0 * ppm) - pix.y
    w, h = round(rect_mm.width * ppm), round(rect_mm.height * ppm)
    out = np.full((h, w), 255, np.uint8)
    sub = arr[max(oy, 0) : oy + h, max(ox, 0) : ox + w]
    out[: sub.shape[0], : sub.shape[1]] = sub
    return out


def reassemble(
    doc: pymupdf.Document, plan: Plan, first_page: int, dpi: float
) -> tuple[Gray, list[int], list[int]]:
    """Compose the usable areas of a page's sheets on one canvas (later sheets cover earlier).

    Returns the image of the source region and the pixel positions (in that image) of the tile
    edges, i.e. the seams between sheets. Sheets must be in row-major print order.
    """
    ppm = dpi / 25.4
    cw, ch = plan.content_mm
    ox, oy = plan.content_origin_mm
    gx, gy = plan.origin_mm
    dx, dy = plan.step_mm
    kw, kh = plan.coverage_mm
    canvas = np.full((round(kh * ppm) + 2, round(kw * ppm) + 2), 255, np.uint8)
    page_no = first_page
    for sh in plan.sheets:
        if sh.print_index is None:
            continue
        tile = render_rect_gray(doc[page_no], Rect(ox, oy, ox + cw, oy + ch), dpi)
        page_no += 1
        x, y = round(sh.col * dx * ppm), round(sh.row * dy * ppm)
        h, w = tile.shape
        h, w = min(h, canvas.shape[0] - y), min(w, canvas.shape[1] - x)
        canvas[y : y + h, x : x + w] = tile[:h, :w]
    tw, th = plan.assembled_size_mm
    x0, y0 = round(-gx * ppm), round(-gy * ppm)
    out = canvas[y0 : y0 + round(th * ppm), x0 : x0 + round(tw * ppm)]
    xs = [round(c * dx * ppm) - x0 for c in range(plan.grid.cols)]
    ys = [round(r * dy * ppm) - y0 for r in range(plan.grid.rows)]
    return out, xs, ys


def direct_vector(pdf: Path, pno: int, plan: Plan, dpi: float, rotate: int = 0) -> Gray:
    """Direct render of the source region at the plan scale (no tiling), view rotated clockwise."""
    ppm = dpi / 25.4
    with pymupdf.open(pdf) as src:
        page = src[pno]
        page.set_rotation(rotate)  # clockwise, like the view rotation (in memory only)
        z = ppm * plan.scale * 25.4 / 72.0  # px per canonical pt
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csGRAY, alpha=False)
        arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()
    x0, y0, x1, y1 = plan.source_rect_mm
    s = ppm * plan.scale
    return arr[round(y0 * s) : round(y1 * s), round(x0 * s) : round(x1 * s)]


def direct_raster(tif: Path, plan: Plan, width_mm: float, height_mm: float, dpi: float) -> Gray:
    """Direct resample of a canonical raster page to the plan's scale."""
    ppm = dpi / 25.4
    im = pyvips.Image.new_from_file(str(tif))
    if im.bands > 1:
        im = im.colourspace("b-w")
    fx = ppm * plan.scale * width_mm / im.width
    fy = ppm * plan.scale * height_mm / im.height
    im = im.resize(fx, vscale=fy, kernel="lanczos3")
    arr = np.frombuffer(im.write_to_memory(), np.uint8).reshape(im.height, im.width).copy()
    x0, y0, x1, y1 = plan.source_rect_mm
    s = ppm * plan.scale
    return arr[round(y0 * s) : round(y1 * s), round(x0 * s) : round(x1 * s)]


def _dilate(mask: NDArray[np.bool_], r: int = 1) -> NDArray[np.bool_]:
    out = mask.copy()
    h, w = mask.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            src_y = slice(0, h - dy) if dy >= 0 else slice(-dy, h)
            dst_y = slice(dy, h) if dy >= 0 else slice(0, h + dy)
            src_x = slice(0, w - dx) if dx >= 0 else slice(-dx, w)
            dst_x = slice(dx, w) if dx >= 0 else slice(0, w + dx)
            out[dst_y, dst_x] |= mask[src_y, src_x]
    return out


def mismatch_fraction(
    a: Gray,
    b: Gray,
    *,
    seams_x: list[int],
    seams_y: list[int],
    band: int = 3,
    threshold: int = 200,
    tol: int = 1,
) -> float:
    """Fraction of dark pixels (of either image) with no dark pixel of the other within ``tol`` px.

    Pixels within ``band`` px of a seam are ignored. Images are cropped to their common size.
    """
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    da, db = a[:h, :w] < threshold, b[:h, :w] < threshold
    miss = (da & ~_dilate(db, tol)) | (db & ~_dilate(da, tol))
    keep = np.ones((h, w), bool)
    for x in seams_x:
        keep[:, max(0, x - band) : x + band + 1] = False
    for y in seams_y:
        keep[max(0, y - band) : y + band + 1, :] = False
    total = int((da | db)[keep].sum())
    return float(miss[keep].sum()) / max(1, total)


def nonwhite_bbox_mm(page: pymupdf.Page, dpi: int = 100, threshold: int = 250) -> Rect | None:
    """Bounding box (mm) of the non-white pixels of a page."""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    ys, xs = np.where(arr < threshold)
    if xs.size == 0:
        return None
    mm = 25.4 / dpi
    return Rect(xs.min() * mm, ys.min() * mm, (xs.max() + 1) * mm, (ys.max() + 1) * mm)
