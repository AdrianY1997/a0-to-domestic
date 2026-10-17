"""ZIP image outputs (SAL-05), reduced copy (SAL-06) and the first-sheet test squares (MAR-04)."""

import io
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from PIL import Image

from a0td.render import RenderResult
from a0td.render.strings import get_strings

Render = Callable[..., tuple[RenderResult, Path]]
SMALL: dict[str, Any] = {"mode": "percent", "percent": 12}


@pytest.mark.parametrize(
    ("fmt", "ext", "pil"),
    [("png_zip", "png", "PNG"), ("jpeg_zip", "jpg", "JPEG"), ("tiff_zip", "tif", "TIFF")],
)
def test_image_zip_one_image_per_sheet_named_by_id(
    render: Render, fmt: str, ext: str, pil: str
) -> None:
    result, pdf = render(
        "a0_vector_grid.pdf", scale=SMALL, output={"formats": ["pdf", fmt], "dpi": 150}
    )
    pdf_out, zip_out = result.outputs
    assert (pdf_out.kind, zip_out.kind) == ("pdf", "zip")
    zpath = pdf.parent / zip_out.name
    assert zip_out.name.endswith(".zip")
    assert zip_out.size_bytes == zpath.stat().st_size
    ids = [s.id for s in result.plans[0].sheets if s.print_index is not None]
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        first = zf.read(names[0])
    assert names == [f"assembly.{ext}", *(f"{i}.{ext}" for i in ids)]
    assert zip_out.pages == len(names) == pdf_out.pages
    img = Image.open(io.BytesIO(first))
    assert img.format == pil
    with pymupdf.open(pdf) as doc:
        w_pt = doc[0].rect.width
    assert img.width == pytest.approx(w_pt / 72 * 150, abs=2)


def test_zip_only_output_has_no_pdf(render: Render) -> None:
    result, zpath = render(
        "a0_vector_grid.pdf", scale=SMALL, output={"formats": ["png_zip"], "dpi": 150}
    )
    assert [o.kind for o in result.outputs] == ["zip"]
    assert zpath.exists()
    assert not list(zpath.parent.glob("*.pdf"))


def test_no_not_implemented_warnings(render: Render) -> None:
    result, _ = render(
        "a0_vector_grid.pdf",
        scale=SMALL,
        output={"formats": ["pdf", "png_zip"], "dpi": 150, "rasterize": True},
        marks={"test_square": "first_sheet"},
        color={"mode": "grayscale"},
    )
    assert result.warnings == []


def test_reduced_copy_is_one_sheet_pdf(render: Render) -> None:
    result, pdf = render("a0_vector_grid.pdf", output={"include_reduced_copy": True})
    main, reduced = result.outputs
    assert main.kind == reduced.kind == "pdf"
    assert reduced.name != main.name
    assert main.pages > 1
    assert reduced.pages == 1
    with pymupdf.open(pdf.parent / reduced.name) as doc:
        assert doc.page_count == 1


def test_test_square_first_sheet_only_on_first_sheet(render: Render) -> None:
    _r, pdf = render(
        "a0_vector_grid.pdf",
        scale={"mode": "percent", "percent": 60},
        marks={"test_square": "first_sheet", "assembly_page": "none"},
    )
    label = get_strings("es")["test_square_50"]
    with pymupdf.open(pdf) as doc:
        assert doc.page_count > 2
        assert doc[0].search_for(label)
        assert not doc[1].search_for(label)


def test_zip_two_pages_unique_sanitized_names_no_partial(render: Render) -> None:
    result, pdf = render(
        "multipage_mixed.pdf",
        scale=SMALL,
        source={"pages": "1-2"},
        marks={"assembly_page": "first"},
        output={"formats": ["pdf", "png_zip"], "dpi": 150},
    )
    zip_out = result.outputs[1]
    with zipfile.ZipFile(pdf.parent / zip_out.name) as zf:
        names = zf.namelist()
    assert len(names) == len(set(names)) == zip_out.pages
    assert all("/" not in n and "\\" not in n and n.endswith(".png") for n in names)
    assert any(n.startswith("P1-") for n in names)
    assert any(n.startswith("P2-") for n in names)
    assert not list(pdf.parent.glob("*.part"))


def test_entry_names_are_deduplicated_and_sanitized() -> None:
    from a0td.render.outputs import _entry_names

    out = _entry_names(["A1", "A1", "../x/y", "a b"], "png")
    assert out == ["A1.png", "A1-2.png", "x_y.png", "a_b.png"]


def test_zip_dpi_is_reduced_with_warning(monkeypatch: pytest.MonkeyPatch, render: Render) -> None:
    from a0td.render import outputs

    monkeypatch.setattr(outputs, "MAX_ZIP_PIXELS", 100_000)
    result, pdf = render(
        "a0_vector_grid.pdf",
        scale=SMALL,
        output={"formats": ["png_zip"], "dpi": 300},
        marks={"assembly_page": "none"},
    )
    assert [w.code for w in result.warnings] == ["ZIP_DPI_REDUCED"]
    with zipfile.ZipFile(pdf) as zf:
        im = Image.open(io.BytesIO(zf.read(zf.namelist()[0])))
    assert im.width * im.height <= 100_000 * 1.2
