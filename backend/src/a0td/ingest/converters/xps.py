"""XPS / OXPS converter: MuPDF (PyMuPDF) opens it directly and re-saves it as PDF (SPEC 7.1)."""

from pathlib import Path

import pymupdf

from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest.converters.base import ConvertContext
from a0td.ingest.converters.common import pdf_to_canonical
from a0td.ingest.models import CanonicalDocument, DocFormat


class XpsConverter:
    id = "xps"
    handles = frozenset({DocFormat.XPS})

    def available(self) -> bool:
        return True

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        scratch = ctx.scratch_dir(work)
        pymupdf.TOOLS.mupdf_display_errors(False)
        pdf = scratch / "xps.pdf"
        try:
            with pymupdf.open(src, filetype="xps") as doc:
                if doc.page_count < 1:
                    raise DomainError(ErrorCode.CORRUPT_FILE, "the XPS has no pages")
                if doc.page_count > ctx.config.max_pages:
                    raise DomainError(
                        ErrorCode.TOO_MANY_PAGES,
                        f"{doc.page_count} pages, the limit is {ctx.config.max_pages}",
                        params={"pages": doc.page_count, "limit": ctx.config.max_pages},
                    )
                pdf.write_bytes(doc.convert_to_pdf())
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(ErrorCode.CORRUPT_FILE, f"cannot read the XPS: {exc}") from exc
        return pdf_to_canonical(pdf, work, ctx, DocFormat.XPS)
