"""PostScript / EPS / Illustrator converter: Ghostscript ``pdfwrite`` (SPEC 7.1).

Ghostscript always runs with ``-dSAFER`` (no file reads / writes outside the given input and
output). A PDF-compatible ``.ai`` file takes the PDF path; a PostScript one goes through gs.
"""

from pathlib import Path

from a0td.ingest.converters.base import ConvertContext, run_tool, tool_available
from a0td.ingest.converters.common import fresh_dir, pdf_to_canonical, stage_input
from a0td.ingest.models import CanonicalDocument, DocFormat


def is_pdf_compatible(src: Path) -> bool:
    with src.open("rb") as fh:
        return b"%PDF-" in fh.read(1024)


class PostScriptConverter:
    id = "postscript"
    handles = frozenset({DocFormat.PS, DocFormat.EPS, DocFormat.AI})

    def available(self) -> bool:
        return tool_available("gs")

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        fmt = ctx.format or DocFormat.PS
        if fmt is DocFormat.AI and is_pdf_compatible(src):
            return pdf_to_canonical(src, work, ctx, fmt)
        scratch = ctx.scratch_dir(work)
        staged = stage_input(src, scratch, "input.ps" if fmt is DocFormat.PS else "input.eps")
        folder = fresh_dir(scratch / "gs")
        pdf = folder / "output.pdf"
        argv: list[str | Path] = [
            "gs",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-dNOPROMPT",
            "-dQUIET",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.7",
            f"-dLastPage={ctx.config.max_pages + 1}",
        ]
        if fmt is not DocFormat.PS:
            argv.append("-dEPSCrop")
        argv += [f"-sOutputFile={pdf}", staged]
        ctx.report("convert", 5, "ingest.convert", tool="ghostscript")
        run_tool(
            argv,
            scratch=scratch,
            timeout_s=ctx.config.job_timeout_s,
            memory_mb=ctx.config.job_memory_mb,
        )
        return pdf_to_canonical(pdf, work, ctx, fmt)
