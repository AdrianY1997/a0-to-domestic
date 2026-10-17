"""PDF converter: PyMuPDF direct, MuPDF repair with qpdf as fallback (SPEC 7.1)."""

from pathlib import Path

from a0td.ingest.canonical import canonicalize_pdf
from a0td.ingest.converters.base import ConvertContext
from a0td.ingest.models import CanonicalDocument, DocFormat


class PdfConverter:
    id = "pdf"
    handles = frozenset({DocFormat.PDF})

    def available(self) -> bool:
        try:
            import pymupdf  # noqa: F401
        except ImportError:  # pragma: no cover
            return False
        return True

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        return canonicalize_pdf(src, work, ctx)
