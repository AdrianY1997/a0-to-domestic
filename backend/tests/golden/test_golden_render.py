"""Golden images (SPEC 15.3): output pages rendered at 72 dpi vs stored PNG references.

Regenerate the references with ``pytest --update-golden`` and review the diff by eye.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
import pytest
from PIL import Image

from a0td.render import RenderResult

REFS = Path(__file__).parent / "refs"
DPI = 72
MAX_BAD_FRACTION = 0.004  # pixels differing by more than PIXEL_TOL
PIXEL_TOL = 40

Render = Callable[..., tuple[RenderResult, Path]]

CASES: dict[str, dict[str, Any]] = {
    # name: fixture, settings, page indices to compare
    "ca1_vector": {"fixture": "a0_vector_grid.pdf", "settings": {}, "pages": [0, 1, 6, 21]},
    "ca2_fit_sheet": {
        "fixture": "a0_vector_grid.pdf",
        "settings": {"scale": {"mode": "fit_sheet"}, "marks": {"assembly_page": "first"}},
        "pages": [0, 1],
    },
    "butt_a4": {
        "fixture": "a0_vector_grid.pdf",
        "settings": {
            "tiling": {"join": "butt"},
            "target": {"orientation": "portrait"},
            "scale": {"mode": "percent", "percent": 50},
        },
        "pages": [1, 2],
    },
    "drawing_scale": {
        "fixture": "a0_vector_grid.pdf",
        "settings": {
            "scale": {"mode": "drawing_scale", "from_denominator": 50, "to_denominator": 100}
        },
        "pages": [0, 3],
    },
    "blank_skip": {"fixture": "a0_top_half_only.pdf", "settings": {}, "pages": [0, 1]},
    "raster_scan": {"fixture": "scan_a0_200dpi_g4.tif", "settings": {}, "pages": [0, 8]},
    "rotated_box": {
        "fixture": "boxes.pdf",
        "settings": {"source": {"rotate": 90, "box": "trim"}},
        "pages": [0, 1],
    },
}


def _page_gray(doc: pymupdf.Document, pno: int) -> np.ndarray:
    pix = doc[pno].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()


@pytest.mark.parametrize("case", list(CASES))
def test_golden(case: str, render: Render, update_golden: bool, tmp_path: Path) -> None:
    spec = CASES[case]
    result, pdf = render(spec["fixture"], **spec["settings"])
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == result.outputs[0].pages
        for pno in spec["pages"]:
            if pno >= doc.page_count:
                continue
            got = _page_gray(doc, pno)
            ref = REFS / f"{case}-p{pno}.png"
            if update_golden:
                REFS.mkdir(exist_ok=True)
                Image.fromarray(got).save(ref, optimize=True)
                continue
            assert ref.exists(), f"missing reference {ref.name}; run pytest --update-golden"
            want = np.asarray(Image.open(ref).convert("L"))
            assert got.shape == want.shape, f"{ref.name}: page size changed"
            bad = float((np.abs(got.astype(int) - want.astype(int)) > PIXEL_TOL).mean())
            if bad > MAX_BAD_FRACTION:
                Image.fromarray(got).save(tmp_path / f"actual-{ref.name}")
                pytest.fail(
                    f"{ref.name}: {bad:.4%} of the pixels differ (limit {MAX_BAD_FRACTION:.2%})"
                )


def test_golden_metadata_and_sizes(render: Render) -> None:
    """Exact page sizes, page count and metadata of the reference job (SPEC 15.3)."""
    result, pdf = render("a0_vector_grid.pdf")
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == 22
        assert {(round(p.rect.width, 2), round(p.rect.height, 2)) for p in doc} == {
            (841.89, 595.28)
        }
        assert doc.metadata["creator"].startswith("a0-to-domestic ")
        assert doc.metadata["title"] == "a0_vector_grid_A4-landscape_3x7_100pct"
        assert doc.metadata["creationDate"] == "D:20260115120000Z"
    assert result.plans[0].counts.printed == 21
