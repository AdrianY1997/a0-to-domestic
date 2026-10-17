"""COL-07: ink coverage per sheet and in total, from low-resolution renders of the output PDF."""

from pathlib import Path

import numpy as np
import pymupdf

from a0td.render.models import InkEstimate, InkSheet

INK_DPI = 24.0
MAX_PAGES = 400  # beyond this the estimate is skipped: it is a convenience, not a requirement
_PT_PER_CM2 = (72.0 / 2.54) ** 2


def _is_assembly(name: str) -> bool:
    return name == "assembly" or name.endswith("-assembly")


def estimate_ink(pdf: Path, names: list[str]) -> InkEstimate | None:
    """Estimate from ``pdf``; ``names[i]`` is the sheet id of page ``i`` (assembly pages skipped).

    Never raises: ``None`` when the output cannot be rendered or has too many pages.
    """
    sheets: list[InkSheet] = []
    total_area = 0.0
    try:
        with pymupdf.open(pdf) as doc:
            if doc.page_count > MAX_PAGES or len(names) != doc.page_count:
                return None
            zoom = INK_DPI / 72.0
            for i, name in enumerate(names):
                area_cm2 = doc[i].rect.width * doc[i].rect.height / _PT_PER_CM2
                if _is_assembly(name) or area_cm2 <= 0:
                    continue
                pix = doc[i].get_pixmap(
                    matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False
                )
                gray = np.frombuffer(pix.samples, dtype=np.uint8)
                darkness = 1.0 - float(gray.mean()) / 255.0 if gray.size else 0.0
                sheets.append(
                    InkSheet(
                        id=name,
                        coverage=round(darkness, 4),
                        equiv_cm2=round(darkness * area_cm2, 2),
                    )
                )
                total_area += area_cm2
    except Exception:
        return None
    if not sheets or total_area <= 0:
        return None
    equiv = sum(s.equiv_cm2 for s in sheets)
    return InkEstimate(
        sheets=sheets, total_coverage=round(equiv / total_area, 4), total_equiv_cm2=round(equiv, 2)
    )
