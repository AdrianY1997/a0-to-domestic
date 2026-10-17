"""Vector engine (SPEC 9.7): the canonical PDF page is a shared form XObject, clipped per tile."""

from pathlib import Path

import pymupdf

from a0td.domain.geometry import Rect
from a0td.domain.page import ViewTransform
from a0td.domain.units import mm_to_pt


def open_canonical(path: Path) -> pymupdf.Document:
    return pymupdf.open(path)


def pt_rect(r: Rect) -> pymupdf.Rect:
    return pymupdf.Rect(mm_to_pt(r.x0), mm_to_pt(r.y0), mm_to_pt(r.x1), mm_to_pt(r.y1))


def place_vector(
    page: pymupdf.Page,
    src: pymupdf.Document,
    pno: int,
    tf: ViewTransform,
    visible: Rect,
    dest: Rect,
) -> None:
    """Draw the part ``visible`` (view mm) of canonical page ``pno`` into ``dest`` (sheet mm).

    ``dest`` must be the exact image of ``visible`` under the sheet's view->sheet map, so the scale
    is exactly the plan's. PyMuPDF keeps one form XObject per source page in the output document and
    adds a small clipped reference per call, so the file does not grow with the number of sheets.
    """
    clip = pt_rect(tf.to_canonical(visible))
    before = set(page.get_contents())
    # show_pdf_page rotates counter-clockwise; the view rotation is clockwise.
    page.show_pdf_page(
        pt_rect(dest),
        src,
        pno,
        keep_proportion=False,
        rotate=-tf.rotate,
        clip=clip,
    )
    if tf.mirror:
        _flip_added_content(page, before, pt_rect(dest))


def _flip_added_content(page: pymupdf.Page, before: set[int], dest: pymupdf.Rect) -> None:
    """ESC-06: mirror, inside ``dest``, what ``show_pdf_page`` just drew (x -> x0 + x1 - x)."""
    doc = page.parent
    head = f"q -1 0 0 1 {dest.x0 + dest.x1:.4f} 0 cm\n".encode()
    for xr in page.get_contents():
        if xr not in before:
            doc.update_stream(xr, head + doc.xref_stream(xr) + b"\nQ\n")
