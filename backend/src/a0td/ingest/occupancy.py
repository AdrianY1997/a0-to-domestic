"""Occupancy maps (SPEC 9.6, ANA-05).

Each page is rendered to gray at 2 px/mm; the background is the 99th percentile of the luminance
(scans yellow with age) and a pixel is content when it is darker than ``background - delta``.
``delta`` depends on the blank-sheet sensitivity (strict 3, normal 12, tolerant 30).

The maps are stored as 1-bit PNGs (content = black): ``occupancy.png`` is the normal sensitivity and
``occupancy-strict.png`` / ``occupancy-tolerant.png`` sit next to it so the planner can honour
``JobSettings.blank_sensitivity`` without re-rendering. :class:`PixelOccupancy` implements
:class:`a0td.domain.occupancy.OccupancyMap`; coordinates are canonical-page mm.
"""

import math
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

from a0td.domain.geometry import Rect
from a0td.domain.settings import BlankSensitivity
from a0td.domain.units import EPS
from a0td.imaging import vips
from a0td.ingest.models import Page, PageKind

PX_PER_MM = 2.0
MAX_CELLS = 16_000_000  # cap for huge pages; the grid then has fewer than 2 px/mm
DELTAS: dict[BlankSensitivity, int] = {
    BlankSensitivity.STRICT: 3,
    BlankSensitivity.NORMAL: 12,
    BlankSensitivity.TOLERANT: 30,
}
OCCUPANCY_FILES: dict[BlankSensitivity, str] = {
    BlankSensitivity.NORMAL: "occupancy.png",
    BlankSensitivity.STRICT: "occupancy-strict.png",
    BlankSensitivity.TOLERANT: "occupancy-tolerant.png",
}


class PixelOccupancy:
    """Boolean grid over the whole canonical page (``mask[row, col]`` = has content)."""

    def __init__(self, mask: np.ndarray, width_mm: float, height_mm: float) -> None:
        if mask.ndim != 2 or mask.size == 0:
            raise ValueError("mask must be a non-empty 2-D array")
        self._rows, self._cols = mask.shape
        self._w, self._h = width_mm, height_mm
        self._ppm_x = self._cols / width_mm
        self._ppm_y = self._rows / height_mm
        sat = np.zeros((self._rows + 1, self._cols + 1), dtype=np.int32)
        np.cumsum(np.cumsum(mask, axis=0, dtype=np.int32), axis=1, out=sat[1:, 1:])
        self._sat = sat
        self._mask = mask
        self._bbox = self._compute_bbox(mask)

    def _compute_bbox(self, mask: np.ndarray) -> Rect | None:
        rows = np.flatnonzero(mask.any(axis=1))
        if rows.size == 0:
            return None
        cols = np.flatnonzero(mask.any(axis=0))
        return Rect(
            float(cols[0]) / self._ppm_x,
            float(rows[0]) / self._ppm_y,
            min(self._w, float(cols[-1] + 1) / self._ppm_x),
            min(self._h, float(rows[-1] + 1) / self._ppm_y),
        )

    @property
    def mask(self) -> np.ndarray:
        return self._mask

    @property
    def px_per_mm(self) -> tuple[float, float]:
        return (self._ppm_x, self._ppm_y)

    def any_in(self, rect: Rect) -> bool:
        """Whether any content lies inside ``rect`` (touching only the edge is not inside)."""
        c0 = max(0, math.floor(rect.x0 * self._ppm_x + EPS))
        c1 = min(self._cols, math.ceil(rect.x1 * self._ppm_x - EPS))
        r0 = max(0, math.floor(rect.y0 * self._ppm_y + EPS))
        r1 = min(self._rows, math.ceil(rect.y1 * self._ppm_y - EPS))
        if c1 <= c0 or r1 <= r0:
            return False
        s = self._sat
        return bool(s[r1, c1] - s[r0, c1] - s[r1, c0] + s[r0, c0] > 0)

    def content_bbox(self) -> Rect | None:
        return self._bbox

    def save_png(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(~self._mask).save(path, format="PNG", optimize=True)

    @classmethod
    def from_png(cls, path: Path, width_mm: float, height_mm: float) -> "PixelOccupancy":
        with Image.open(path) as img:
            mask = np.asarray(img.convert("L")) < 128
        return cls(mask, width_mm, height_mm)


# ---------------------------------------------------------------------------- building


def grid_shape(width_mm: float, height_mm: float) -> tuple[int, int]:
    """``(rows, cols)`` of the occupancy grid of a page."""
    ppm = PX_PER_MM
    if width_mm * height_mm * ppm * ppm > MAX_CELLS:
        ppm = math.sqrt(MAX_CELLS / (width_mm * height_mm))
    return max(1, math.ceil(height_mm * ppm)), max(1, math.ceil(width_mm * ppm))


def render_pdf_gray(pdf_path: Path, index: int, rows: int, cols: int) -> np.ndarray:
    """Gray render (uint8, white background) of a canonical PDF page into ``rows x cols``."""
    with pymupdf.open(pdf_path) as doc:
        page = doc.load_page(index)
        matrix = pymupdf.Matrix(cols / page.rect.width, rows / page.rect.height)
        pix = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def render_raster_gray(tif_path: Path, rows: int, cols: int) -> np.ndarray:
    """Gray, area-averaged version of a canonical raster page at ``rows x cols``."""
    im = vips.Image.new_from_file(str(tif_path), access="sequential")
    if im.bands > 1:
        im = im.colourspace("b-w")
    im = im.resize(cols / im.width, vscale=rows / im.height, kernel="linear")
    return np.frombuffer(im.write_to_memory(), dtype=np.uint8).reshape(im.height, im.width).copy()


def background_level(gray: np.ndarray) -> int:
    """99th percentile of the luminance (exact, histogram based)."""
    hist = np.bincount(gray.ravel(), minlength=256)
    cum = np.cumsum(hist)
    return int(np.searchsorted(cum, 0.99 * gray.size, side="left"))


def remove_isolated(mask: np.ndarray) -> np.ndarray:
    """Drop single-pixel specks (no content in the 8-neighbourhood); lines and blobs stay."""
    padded = np.pad(mask, 1)
    rows, cols = mask.shape
    neighbours = np.zeros_like(mask)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            neighbours |= padded[dy : dy + rows, dx : dx + cols]
    return mask & neighbours


def masks_from_gray(gray: np.ndarray, *, despeckle: bool) -> dict[BlankSensitivity, np.ndarray]:
    bg = background_level(gray)
    out: dict[BlankSensitivity, np.ndarray] = {}
    for sens, delta in DELTAS.items():
        mask = gray.astype(np.int16) < (bg - delta)
        out[sens] = remove_isolated(mask) if despeckle else mask
    return out


def build_occupancy(
    page: Page, source: Path, page_dir: Path
) -> dict[BlankSensitivity, PixelOccupancy]:
    """Render ``page`` from ``source`` (canonical PDF or page TIFF) and write the PNG maps."""
    rows, cols = grid_shape(page.width_mm, page.height_mm)
    if source.suffix.lower() == ".pdf":
        gray = render_pdf_gray(source, page.index, rows, cols)
    else:
        gray = render_raster_gray(source, rows, cols)
    masks = masks_from_gray(gray, despeckle=page.kind is PageKind.RASTER)
    maps: dict[BlankSensitivity, PixelOccupancy] = {}
    for sens, mask in masks.items():
        occ = PixelOccupancy(mask, page.width_mm, page.height_mm)
        occ.save_png(page_dir / OCCUPANCY_FILES[sens])
        maps[sens] = occ
    return maps


def load_occupancy(
    page_dir: Path, page: Page, sensitivity: BlankSensitivity = BlankSensitivity.NORMAL
) -> PixelOccupancy:
    """Load a stored map (falls back to the normal one if the sensitivity file is missing)."""
    path = page_dir / OCCUPANCY_FILES[sensitivity]
    if not path.exists():
        path = page_dir / OCCUPANCY_FILES[BlankSensitivity.NORMAL]
    return PixelOccupancy.from_png(path, page.width_mm, page.height_mm)
