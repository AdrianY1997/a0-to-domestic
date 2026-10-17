"""DjVu converter: ``ddjvu -format=tiff`` (DjVuLibre) and then the regular raster path (SPEC 7.1).

The TIFF that ddjvu writes carries the resolution of each DjVu page (``dpi`` in the page chunk),
so the physical size comes from the file's own metadata. If ddjvu leaves no resolution in the TIFF
it is read from ``djvudump`` and set on the canonical page (``dpi_meta``), so that the page does not
fall back to "assumed".
"""

import re
from pathlib import Path

from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest.canonical import canonicalize_raster
from a0td.ingest.converters.base import ConvertContext, run_tool, tool_available
from a0td.ingest.converters.common import fresh_dir, stage_input
from a0td.ingest.models import CanonicalDocument, DocFormat

_DPI_RE = re.compile(rb"^\s*INFO\b.*?,\s*(\d{2,5}) dpi", re.MULTILINE)
_PAGE_COUNT_RE = re.compile(rb"^\s*(\d+)\s*$")


def _page_count(staged: Path, scratch: Path, timeout_s: float) -> int | None:
    """Pages of the document (``djvused -e n``), or ``None`` if the tool is not there."""
    if not tool_available("djvused"):
        return None
    try:
        res = run_tool(["djvused", "-e", "n", staged], scratch=scratch, timeout_s=timeout_s)
    except DomainError:
        return None
    m = _PAGE_COUNT_RE.match(res.stdout.strip())
    return int(m.group(1)) if m else None


def _dump_dpis(staged: Path, scratch: Path, timeout_s: float) -> list[float]:
    """Resolution of every page, in order, from the INFO chunks printed by ``djvudump``."""
    if not tool_available("djvudump"):
        return []
    try:
        res = run_tool(["djvudump", staged], scratch=scratch, timeout_s=timeout_s)
    except DomainError:
        return []
    return [float(m.group(1)) for m in _DPI_RE.finditer(res.stdout)]


class DjvuConverter:
    id = "djvu"
    handles = frozenset({DocFormat.DJVU})

    def available(self) -> bool:
        return tool_available("ddjvu")

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        scratch = ctx.scratch_dir(work)
        staged = stage_input(src, scratch, "input.djvu")
        pages = _page_count(staged, scratch, 30)
        if pages is not None and pages > ctx.config.max_pages:
            raise DomainError(
                ErrorCode.TOO_MANY_PAGES,
                f"{pages} pages, the limit is {ctx.config.max_pages}",
                params={"pages": pages, "limit": ctx.config.max_pages},
            )
        out_dir = fresh_dir(scratch / "djvu")
        tiff = out_dir / "pages.tif"
        ctx.report("convert", 5, "ingest.convert", tool="ddjvu")
        try:
            run_tool(
                ["ddjvu", "-format=tiff", staged, tiff],
                scratch=scratch,
                timeout_s=ctx.config.job_timeout_s,
                memory_mb=ctx.config.job_memory_mb,
            )
        except DomainError as exc:
            if exc.code is ErrorCode.CONVERSION_FAILED:
                raise DomainError(
                    ErrorCode.CORRUPT_FILE, f"cannot decode the DjVu file: {exc.detail}"
                ) from exc
            raise
        if not tiff.is_file() or tiff.stat().st_size == 0:
            raise DomainError(ErrorCode.CORRUPT_FILE, "ddjvu produced no image")
        canon = canonicalize_raster(tiff, work, ctx, DocFormat.TIFF)
        canon.source_format = DocFormat.DJVU
        if any(p.dpi_meta is None for p in canon.pages):
            dpis = _dump_dpis(staged, scratch, 30)
            if len(dpis) == len(canon.pages):
                for page, dpi in zip(canon.pages, dpis, strict=True):
                    if page.dpi_meta is None:
                        page.dpi_meta = (dpi, dpi)
        return canon
