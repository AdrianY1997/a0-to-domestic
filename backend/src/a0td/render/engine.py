"""Render F1 (SPEC 5.5, 5.10, 9.7): from an ingested document and settings to the tiled PDF.

:func:`render_job` loads the ingested document, plans every selected page (with its occupancy map,
so blank sheets are skipped), draws one PDF page per printed sheet (plus the assembly plan page)
with the vector or the raster engine, applies the printer correction and writes the output.

Colour modes and rasterized output are prepared once per job by :mod:`a0td.render.color`; ZIP image
outputs are extra :class:`OutputFile` entries rendered from the finished PDF; the reduced copy is a
second pass with ``fit_sheet``. The marks layer (F3) wraps :func:`draw_marks`.
"""

import json
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pymupdf

from a0td.config import get_config
from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.geometry import Rect
from a0td.domain.occupancy import OccupancyMap
from a0td.domain.papers import Orientation
from a0td.domain.plan import Plan, SheetPlan
from a0td.domain.planner import plan as make_plan
from a0td.domain.settings import (
    AssemblyPage,
    FitSheetScale,
    InfoBandPosition,
    JobSettings,
    OutputFormat,
    PrinterProfile,
    TestSquare,
    get_builtin_printer,
    parse_page_ranges,
)
from a0td.domain.units import mm_to_pt
from a0td.ingest import IngestResult, Page, load_occupancy, page_dir
from a0td.render.assembly import AssemblySource, draw_assembly_page, draw_test_block
from a0td.render.canvas import FONT, FONT_BOLD
from a0td.render.color import PreparedSources, needs_prepare, prepare_sources
from a0td.render.geometry import PageView, ViewMap, page_view_of, visible_rect
from a0td.render.ink import estimate_ink
from a0td.render.layers import effective_off_names, finish_layers, new_marks_ocg
from a0td.render.marks import MarksContext, draw_marks
from a0td.render.models import InkEstimate, OutputFile, RenderResult, RenderWarning
from a0td.render.outputs import (
    IMAGE_EXT,
    apply_correction,
    build_filename,
    finalize_pdf,
    unique_name,
    write_image_zip,
)
from a0td.render.raster import RasterSource, place_raster
from a0td.render.vector import open_canonical, place_vector

_PAGE_PREFIX = re.compile(r"^P\d+-")
ProgressCallback = Callable[[str, float, str, dict[str, Any]], None]
ROTATE_TO_PORTRAIT = 90  # show_pdf_page angle that turns a landscape sheet into a portrait page

__all__ = ["OutputFile", "RenderResult", "RenderWarning", "render_job", "render_sheet_preview"]


class _Progress:
    def __init__(self, cb: ProgressCallback | None, total: int) -> None:
        self._cb = cb
        self._total = max(1, total)
        self._done = 0

    def emit(self, stage: str, key: str, **params: Any) -> None:
        if self._cb is not None:
            self._cb(stage, min(99.0, 100.0 * self._done / self._total), key, params)

    def step(self, stage: str, key: str, **params: Any) -> None:
        self._done += 1
        self.emit(stage, key, **params)

    def final(self, stage: str, pct: float, key: str) -> None:
        if self._cb is not None:
            self._cb(stage, pct, key, {})


# ------------------------------------------------------------------------------ helpers


def _invalid(loc: str, message: str) -> DomainError:
    return DomainError(
        ErrorCode.INVALID_SETTINGS, message, errors=[{"loc": loc, "message": message}]
    )


def _select_pages(ingest: IngestResult, settings: JobSettings) -> list[int]:
    try:
        numbers = parse_page_ranges(settings.source.pages, len(ingest.pages))
    except ValueError as exc:
        raise _invalid("source.pages", str(exc)) from exc
    if not numbers:
        raise _invalid("source.pages", "no pages selected")
    return [n - 1 for n in numbers]


def _resolve_printer(settings: JobSettings, printer: PrinterProfile | None) -> PrinterProfile:
    if printer is not None:
        return printer
    found = get_builtin_printer(settings.target.printer_id) or get_builtin_printer(
        "builtin-generic"
    )
    assert found is not None  # the generic printer is always built in
    return found


def _occupancy(doc_dir: Path, page: Page, settings: JobSettings) -> OccupancyMap | None:
    if not (settings.tiling.skip_blank or settings.source.auto_trim):
        return None
    try:
        return load_occupancy(
            page_dir(doc_dir, page.index), page, settings.tiling.blank_sensitivity
        )
    except (FileNotFoundError, OSError):
        return None


def _document_name(doc_dir: Path) -> str:
    """Display name of the document: ``name`` of ``document.json`` without its extension."""
    try:
        info = json.loads((doc_dir / "document.json").read_text(encoding="utf-8"))
        name = str(info.get("name") or "")
    except (OSError, ValueError, AttributeError):
        name = ""
    return Path(name).stem if name else "document"


def _hidden_names(pages: list[Page], settings: JobSettings) -> set[str]:
    """ANA-07: names of the source layers to switch off, over the given pages."""
    names: set[str] = set()
    for pg in pages:
        names |= effective_off_names(
            pg.layers, settings.source.hidden_layers, settings.source.visible_layers
        )
    return names


def _band_rect(plan: Plan, settings: JobSettings) -> Rect | None:
    band = settings.target.info_band
    if band.position is InfoBandPosition.NONE or band.height_mm <= 0:
        return None
    m = plan.margins_mm
    cw, ch = plan.content_mm
    top = m.top if band.position is InfoBandPosition.TOP else m.top + ch
    return Rect(m.left, top, m.left + cw, top + band.height_mm)


def _new_page(out: pymupdf.Document, plan: Plan) -> pymupdf.Page:
    page: pymupdf.Page = out.new_page(
        -1, width=mm_to_pt(plan.paper.width_mm), height=mm_to_pt(plan.paper.height_mm)
    )
    # Register the label fonts on the page first: PyMuPDF skips a font that it finds anywhere in
    # the page's resources, including inside the source form XObject, and the text would then be
    # drawn with a fallback font.
    for font in (FONT, FONT_BOLD):
        page.insert_font(fontname=font)
    return page


# ------------------------------------------------------------------------------- engine


class _PageRenderer:
    """Draws the sheets and the assembly page of one source page into the output document."""

    def __init__(
        self,
        out: pymupdf.Document,
        ingest: IngestResult,
        doc_dir: Path,
        page: Page,
        plan: Plan,
        settings: JobSettings,
        printer: PrinterProfile,
        *,
        doc_name: str,
        page_count: int,
        locale: str,
        now: datetime,
        vector_src: pymupdf.Document | None,
        tif: Path | None = None,
        names: list[str] | None = None,
        test_block: bool = False,
        marks_oc: int = 0,
    ) -> None:
        self.out, self.page, self.plan = out, page, plan
        self.settings, self.printer = settings, printer
        self.doc_name, self.page_count, self.locale, self.now = doc_name, page_count, locale, now
        self.pv: PageView = page_view_of(
            page, settings.source.box, settings.source.rotate, settings.source.mirror
        )
        self.marks_oc = marks_oc
        self.vector_src = vector_src
        self.raster: RasterSource | None = None
        self._tif: Path | None = None
        self.names = names
        self._test_block = test_block
        if vector_src is None:
            self._tif = tif or doc_dir / ingest.page_files[page.index]
            self.raster = RasterSource(self._tif, page.width_mm, page.height_mm)
        self.band = _band_rect(plan, settings)
        # landscape_as_rotated_portrait (SPEC 5.3): sheets are drawn landscape in a scratch
        # document and turned into portrait pages (content rotated 90 degrees) when finished.
        self._scratch: pymupdf.Document | None = None
        if (
            settings.target.landscape_as_rotated_portrait
            and plan.orientation is Orientation.LANDSCAPE
        ):
            self._scratch = pymupdf.open()

    def close(self) -> None:
        if self._scratch is not None:
            self._scratch.close()
            self._scratch = None

    def _new(self) -> pymupdf.Page:
        return _new_page(self._scratch if self._scratch is not None else self.out, self.plan)

    def _correct(self, pg: pymupdf.Page) -> None:
        kx, ky = self.printer.scale_kx, self.printer.scale_ky
        if self._scratch is not None:
            kx, ky = ky, kx  # the landscape view x axis is the paper's y axis
        apply_correction(pg, kx, ky)

    def _finish(self, pg: pymupdf.Page) -> None:
        """Apply the printer correction and, in rotated mode, move the sheet into ``out``."""
        self._correct(pg)
        scratch = self._scratch
        if scratch is None:
            return
        w, h = pg.rect.width, pg.rect.height
        portrait = self.out.new_page(-1, width=h, height=w)
        # The landscape view is the portrait page turned 90 degrees clockwise (margins follow
        # Margins.rotated_cw), so the portrait content is the view turned counter-clockwise.
        portrait.show_pdf_page(portrait.rect, scratch, pg.number, rotate=ROTATE_TO_PORTRAIT)
        scratch.delete_page(pg.number)

    def sheet(self, sh: SheetPlan) -> None:
        plan, settings = self.plan, self.settings
        pg = self._new()
        ox, oy = plan.content_origin_mm
        src = Rect(*sh.src_rect_mm)
        vis = visible_rect(src, Rect(*plan.source_rect_mm), self.pv.page_view)
        if vis is not None:
            vmap = ViewMap(ox, oy, src.x0, src.y0, plan.scale)
            if self.vector_src is not None:
                place_vector(
                    pg, self.vector_src, self.page.index, self.pv.transform, vis, vmap.rect(vis)
                )
            else:
                assert self.raster is not None
                place_raster(
                    pg,
                    self.raster,
                    self.pv.transform,
                    vis,
                    vmap,
                    max_dpi=settings.output.max_dpi,
                    jpeg_quality=settings.output.jpeg_quality,
                )
        if self._test_block:  # MAR-04: test squares on the first printed sheet
            self._test_block = False
            content = Rect(ox, oy, ox + plan.content_mm[0], oy + plan.content_mm[1])
            draw_test_block(pg, content, self.locale)
        draw_marks(
            pg,
            MarksContext(
                plan, sh, settings.marks, doc_name=self.doc_name, locale=self.locale, band=self.band
            ),
            self.marks_oc,
        )
        self._finish(pg)
        if self.names is not None:
            self.names.append(sh.id)

    def assembly(self) -> None:
        pg = self._new()
        source = AssemblySource()
        if self.vector_src is not None:
            source.vector, source.pno = self.vector_src, self.page.index
        else:
            assert self._tif is not None
            source.raster = RasterSource(
                self._tif, self.page.width_mm, self.page.height_mm, sequential=True
            )
        draw_assembly_page(
            pg,
            plan=self.plan,
            settings=self.settings,
            printer=self.printer,
            pv=self.pv,
            source=source,
            doc_name=self.doc_name,
            page_count=self.page_count,
            locale=self.locale,
            now=self.now,
        )
        self._finish(pg)
        if self.names is not None:
            n = self.plan.page_index + 1
            self.names.append("assembly" if self.page_count <= 1 else f"P{n}-assembly")


@dataclass(slots=True)
class _Drawn:
    path: Path
    pages: int
    names: list[str]


def _open_vector(
    doc_dir: Path, ingest: IngestResult, prepared: PreparedSources
) -> pymupdf.Document | None:
    if ingest.kind != "pdf" or prepared.tifs:
        return None
    assert ingest.canonical_pdf is not None
    return open_canonical(prepared.pdf or doc_dir / ingest.canonical_pdf)


def _draw_pdf(
    doc_dir: Path,
    ingest: IngestResult,
    selected: list[int],
    plans: list[Plan],
    settings: JobSettings,
    prof: PrinterProfile,
    prepared: PreparedSources,
    target: Path,
    *,
    todo: list[int],
    asm_pos: AssemblyPage,
    only_assembly: bool,
    doc_name: str,
    locale: str,
    now: datetime,
    prog: _Progress | None,
) -> _Drawn:
    """Draw the sheets (and assembly pages) of ``plans[i] for i in todo`` and save the PDF."""
    out = pymupdf.open()
    names: list[str] = []
    vector_src = _open_vector(doc_dir, ingest, prepared)
    marks_oc = new_marks_ocg(out, locale) if settings.marks.marks_layer else 0
    try:
        for n, i in enumerate(todo):
            plan = plans[i]
            idx = selected[i]
            renderer = _PageRenderer(
                out,
                ingest,
                doc_dir,
                ingest.pages[idx],
                plan,
                settings,
                prof,
                doc_name=doc_name,
                page_count=len(selected),
                locale=locale,
                now=now,
                vector_src=vector_src,
                tif=prepared.tifs.get(idx),
                names=names,
                test_block=settings.marks.test_square is TestSquare.FIRST_SHEET and n == 0,
                marks_oc=marks_oc,
            )
            if asm_pos is AssemblyPage.FIRST:
                renderer.assembly()
                if prog:
                    prog.step("render", "render.assembly", page=plan.page_index + 1)
            if not only_assembly:
                for sh in plan.sheets:
                    if sh.print_index is None:
                        continue
                    renderer.sheet(sh)
                    if prog:
                        prog.step(
                            "render",
                            "render.sheet",
                            page=plan.page_index + 1,
                            sheet=sh.id,
                            k=sh.print_index,
                            n=plan.counts.printed,
                        )
            if asm_pos is AssemblyPage.LAST:
                renderer.assembly()
                if prog:
                    prog.step("render", "render.assembly", page=plan.page_index + 1)
            renderer.close()
        if prog:
            prog.final("save", 99.0, "render.save")
        pages = out.page_count
        finish_layers(
            out,
            _hidden_names([ingest.pages[selected[i]] for i in todo], settings),
            marks_oc=marks_oc,
        )
        finalize_pdf(out, target, title=target.stem, settings=settings, plans=plans, now=now)
    finally:
        out.close()
        if vector_src is not None:
            vector_src.close()
    return _Drawn(target, pages, names)


def _reduced_settings(settings: JobSettings) -> JobSettings:
    """SAL-06: the same document fitted on one sheet, without extras."""
    return settings.model_copy(
        update={
            "scale": FitSheetScale(),
            "tiling": settings.tiling.model_copy(update={"only_sheets": None}),
            "marks": settings.marks.model_copy(update={"assembly_page": AssemblyPage.NONE}),
            "output": settings.output.model_copy(
                update={"include_reduced_copy": False, "formats": [OutputFormat.PDF]}
            ),
        }
    )


def _make_plans(
    doc_dir: Path,
    ingest: IngestResult,
    selected: list[int],
    settings: JobSettings,
    prof: PrinterProfile,
    *,
    exact_scale: bool,
) -> list[Plan]:
    max_sheets = get_config().max_sheets
    return [
        make_plan(
            ingest.pages[idx].to_geometry(page_count=len(selected)),
            settings,
            printer=prof,
            occupancy=_occupancy(doc_dir, ingest.pages[idx], settings),
            exact_scale=exact_scale,
            max_sheets=max_sheets,
        )
        for idx in selected
    ]


def _pdf_output(drawn: _Drawn, name: str) -> OutputFile:
    return OutputFile(
        name=name, kind="pdf", size_bytes=drawn.path.stat().st_size, pages=drawn.pages
    )


def render_job(
    doc_dir: Path,
    settings: JobSettings,
    out_dir: Path,
    *,
    printer: PrinterProfile | None = None,
    exact_scale: bool = False,
    test_sheet_only: bool = False,
    progress: ProgressCallback | None = None,
    doc_name: str | None = None,
    locale: str = "es",
    now: datetime | None = None,
) -> RenderResult:
    """Render the tiled output of an ingested document into ``out_dir``.

    ``doc_dir`` is the ingest directory (``meta.json`` + ``canonical.pdf`` / ``page-{n}.tif``).
    With ``test_sheet_only`` only the assembly plan (with its test squares) of the first selected
    page is produced. ``progress(stage, pct, message_key, params)`` is called as sheets are drawn;
    an exception raised by it (e.g. a cancellation) aborts the job and propagates. ``doc_name``
    (default: ``name`` of ``<doc_dir>/document.json``) feeds the labels and the file name;
    ``locale`` (``es``/``en``) the printed texts; ``now`` fixes the timestamp (tests).
    Outputs: the PDF (when requested), one ZIP of images per image format (SAL-05) and the reduced
    copy (SAL-06). Raises :class:`DomainError` for invalid settings.
    """
    try:
        ingest = IngestResult.load(doc_dir)
    except FileNotFoundError as exc:
        raise DomainError(ErrorCode.DOCUMENT_NOT_READY, "document has not been ingested") from exc
    now = now or datetime.now().astimezone()
    doc_name = doc_name or _document_name(doc_dir)
    selected = _select_pages(ingest, settings)
    prof = _resolve_printer(settings, printer)
    plans = _make_plans(doc_dir, ingest, selected, settings, prof, exact_scale=exact_scale)

    asm_pos = AssemblyPage.FIRST if test_sheet_only else settings.marks.assembly_page
    if test_sheet_only:
        todo = [0]
        n_pages = 1
    else:
        todo = list(range(len(plans)))
        n_pages = sum(p.counts.printed + (asm_pos is not AssemblyPage.NONE) for p in plans)
    if n_pages == 0:
        raise _invalid("tiling", "no sheets to print (all blank or deselected)")

    out_dir.mkdir(parents=True, exist_ok=True)
    prog = _Progress(progress, n_pages + 1)
    prog.emit("load", "render.start", pages=n_pages)

    warnings: list[RenderWarning] = []
    outputs: list[OutputFile] = []
    formats = settings.output.formats
    want_pdf = OutputFormat.PDF in formats
    zip_formats = [f for f in formats if f is not OutputFormat.PDF]
    test_suffix = "_test" if test_sheet_only else ""
    with tempfile.TemporaryDirectory(prefix="a0td-render-") as tmp:
        work = Path(tmp)
        prepared = PreparedSources()
        if needs_prepare(ingest, settings):
            prog.emit("load", "render.prepare")
            prepared = prepare_sources(
                doc_dir, ingest, [selected[i] for i in todo], settings, work / "src"
            )
            if prepared.layers_rasterized:
                warnings.append(RenderWarning(code="LAYERS_RASTERIZED", severity="info"))
            if prepared.gray_rasterized:
                warnings.append(
                    RenderWarning(
                        code="LOCAL_LIMIT", severity="info", params={"limit": "gray_vector"}
                    )
                )
        template = settings.output.filename_template
        pdf_name = build_filename(template, name=doc_name, plan=plans[0], suffix=test_suffix)
        drawn = _draw_pdf(
            doc_dir, ingest, selected, plans, settings, prof, prepared,
            (out_dir if want_pdf else work) / pdf_name,
            todo=todo, asm_pos=asm_pos, only_assembly=test_sheet_only, doc_name=doc_name,
            locale=locale, now=now, prog=prog,
        )  # fmt: skip
        taken = {pdf_name}
        ink: InkEstimate | None = estimate_ink(drawn.path, drawn.names)
        if want_pdf:
            outputs.append(_pdf_output(drawn, pdf_name))
        for fmt in zip_formats:
            prog.final("zip", 99.0, "render.zip")
            zname = unique_name(
                build_filename(
                    template, name=doc_name, plan=plans[0],
                    suffix=f"{test_suffix}_{IMAGE_EXT[fmt.value]}", ext="zip",
                ),
                taken,
            )  # fmt: skip
            taken.add(zname)
            zres = write_image_zip(
                drawn.path, drawn.names, out_dir / zname, fmt=fmt.value,
                dpi=settings.output.dpi, jpeg_quality=settings.output.jpeg_quality,
            )  # fmt: skip
            size = (out_dir / zname).stat().st_size
            outputs.append(OutputFile(name=zname, kind="zip", size_bytes=size, pages=zres.pages))
            if zres.reduced_dpi is not None:
                warnings.append(
                    RenderWarning(
                        code="ZIP_DPI_REDUCED",
                        params={
                            "format": fmt.value,
                            "dpi": settings.output.dpi,
                            "effective_dpi": round(zres.reduced_dpi, 1),
                        },
                    )
                )
        if settings.output.include_reduced_copy and not test_sheet_only:
            reduced = _reduced_settings(settings)
            rplans = _make_plans(doc_dir, ingest, selected, reduced, prof, exact_scale=False)
            if any(p.counts.printed for p in rplans):
                rname = unique_name(
                    build_filename(template, name=doc_name, plan=rplans[0], suffix="_reduced"),
                    taken,
                )
                rdrawn = _draw_pdf(
                    doc_dir, ingest, selected, rplans, reduced, prof, prepared, out_dir / rname,
                    todo=list(range(len(rplans))), asm_pos=AssemblyPage.NONE,
                    only_assembly=False, doc_name=doc_name, locale=locale, now=now, prog=None,
                )  # fmt: skip
                outputs.append(_pdf_output(rdrawn, rname))

    prog.final("done", 100.0, "render.done")
    return RenderResult(outputs=outputs, plans=plans, warnings=warnings, ink=ink)


def render_sheet_preview(
    doc_dir: Path,
    settings: JobSettings,
    page_index: int,
    sheet_id: str,
    max_px: int,
    *,
    printer: PrinterProfile | None = None,
    doc_name: str | None = None,
    locale: str = "es",
) -> bytes:
    """PNG of one sheet (content and marks) whose longest side is at most ``max_px`` pixels.

    ``page_index`` is 0-based; ``sheet_id`` is matched case-insensitively and a missing ``P{n}-``
    prefix is tolerated. Raises ``NOT_FOUND`` for an unknown page or sheet.
    """
    ingest = IngestResult.load(doc_dir)
    if not 0 <= page_index < len(ingest.pages):
        raise DomainError(ErrorCode.NOT_FOUND, f"page {page_index} does not exist")
    try:
        count = len(_select_pages(ingest, settings))
    except DomainError:
        count = len(ingest.pages)
    page = ingest.pages[page_index]
    prof = _resolve_printer(settings, printer)
    plan = make_plan(
        page.to_geometry(page_count=count),
        settings,
        printer=prof,
        occupancy=_occupancy(doc_dir, page, settings),
        max_sheets=get_config().max_sheets,
    )
    wanted = sheet_id.strip().upper()
    sheet = next(
        (s for s in plan.sheets if wanted in (s.id.upper(), _PAGE_PREFIX.sub("", s.id.upper()))),
        None,
    )
    if sheet is None:
        raise DomainError(ErrorCode.NOT_FOUND, f"sheet {sheet_id} does not exist")

    out = pymupdf.open()
    vector_src: pymupdf.Document | None = None
    with tempfile.TemporaryDirectory(prefix="a0td-preview-") as tmp:
        prepared = PreparedSources()
        if needs_prepare(ingest, settings):
            prepared = prepare_sources(doc_dir, ingest, [page_index], settings, Path(tmp))
        try:
            vector_src = _open_vector(doc_dir, ingest, prepared)
            renderer = _PageRenderer(
                out,
                ingest,
                doc_dir,
                page,
                plan,
                settings,
                prof,
                doc_name=doc_name or _document_name(doc_dir),
                page_count=count,
                locale=locale,
                now=datetime.now().astimezone(),
                vector_src=vector_src,
                tif=prepared.tifs.get(page_index),
                marks_oc=new_marks_ocg(out, locale) if settings.marks.marks_layer else 0,
            )
            renderer.sheet(sheet)
            renderer.close()
            finish_layers(out, _hidden_names([page], settings), marks_oc=renderer.marks_oc)
            # MuPDF reads the layer configuration on load: reopen so the render honours it
            data = out.tobytes()
            out.close()
            out = pymupdf.open("pdf", data)
            pg = out[0]
            zoom = max(1, max_px) / max(pg.rect.width, pg.rect.height)
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            png: bytes = pix.tobytes("png")
        finally:
            out.close()
            if vector_src is not None:
                vector_src.close()
    return png
