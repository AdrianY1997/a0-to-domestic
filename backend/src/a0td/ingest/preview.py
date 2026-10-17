"""Per-page preview images (SPEC 7.3): ``preview.webp`` and ``thumb.webp``."""

from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

THUMB_PX = 512
PREVIEW_FILE = "preview.webp"
THUMB_FILE = "thumb.webp"
_MAX_ZOOM = 600 / 72  # never render beyond 600 dpi, even for small pages
_PREVIEW_QUALITY = 85
_THUMB_QUALITY = 80


def _render_pdf(pdf_path: Path, index: int, max_px: int) -> Image.Image:
    with pymupdf.open(pdf_path) as doc:
        page = doc.load_page(index)
        longest = max(page.rect.width, page.rect.height)
        zoom = min(max_px / longest, _MAX_ZOOM)
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False
        )
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _render_raster(tif_path: Path, max_px: int) -> Image.Image:
    import pyvips

    im = pyvips.Image.thumbnail(str(tif_path), max_px, height=max_px, size="down")
    if im.bands > 3:
        im = im.flatten(background=[255, 255, 255])
    arr = np.frombuffer(im.write_to_memory(), dtype=np.uint8).reshape(im.height, im.width, im.bands)
    return Image.fromarray(arr[:, :, 0] if im.bands == 1 else arr[:, :, :3]).convert("RGB")


def write_previews(source: Path, index: int, out_dir: Path, max_px: int) -> tuple[int, int]:
    """Write ``preview.webp`` (longest side ``max_px`` at most) and ``thumb.webp`` (512 px).

    ``source`` is the canonical PDF (page ``index``) or the page TIFF. Returns the preview size.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".pdf":
        img = _render_pdf(source, index, max_px)
    else:
        img = _render_raster(source, max_px)
    img.save(out_dir / PREVIEW_FILE, format="WEBP", quality=_PREVIEW_QUALITY, method=4)
    size = img.size
    thumb = img.copy()
    thumb.thumbnail((THUMB_PX, THUMB_PX), Image.Resampling.LANCZOS)
    thumb.save(out_dir / THUMB_FILE, format="WEBP", quality=_THUMB_QUALITY, method=4)
    return size
