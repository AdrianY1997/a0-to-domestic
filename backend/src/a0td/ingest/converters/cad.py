"""CAD converters: DXF (ezdxf ``drawing`` add-on, PyMuPDF backend) and DWG (LibreDWG) (SPEC 7.1).

DXF -> ``canonical.pdf``:

* a **paper space layout** keeps the page size the layout declares (``Layout1`` 1189 x 841 mm...),
  and its viewports are drawn as in the CAD program;
* **model space** is drawn at 1:N with the drawing units of ``$INSUNITS`` (or the user's choice).
  Without a scale, the smallest standard N that makes the extents fit an A0 sheet is used and
  reported (``CadInfo.scale``, ``scale_auto``).

Line weights are always on (absolute, in mm on paper). The colour policy is ``color`` (as in the
drawing, on white paper), ``black`` (everything black) or ``gray``.

DWG -> DXF with LibreDWG's ``dwg2dxf`` (or the ODA File Converter when ``A0TD_ODA_CONVERTER`` is
set, under ``xvfb-run`` if that exists) -> the DXF path.
"""

import contextlib
import logging
import math
import os
import re
import shutil
import signal
import threading
from collections.abc import Iterator
from pathlib import Path
from types import FrameType
from typing import Any

import pymupdf

from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.domain.plan import PlanWarning
from a0td.ingest.converters.base import ConvertContext, run_tool, tool_available
from a0td.ingest.converters.common import fresh_dir, pdf_to_canonical, stage_input
from a0td.ingest.models import (
    CadColor,
    CadInfo,
    CadLayout,
    CadOptions,
    CanonicalDocument,
    DocFormat,
)

log = logging.getLogger(__name__)
# ezdxf reports every salvaged oddity of a DWG-derived DXF as a warning: keep only the errors.
logging.getLogger("ezdxf").setLevel(logging.ERROR)

MODEL = "Model"
MARGIN_MM = 5.0  # blank border around a model-space drawing
A0_SHORT_MM, A0_LONG_MM = 841.0, 1189.0
# Standard drawing scales tried (in order) when the user gives none.
STANDARD_SCALES: tuple[float, ...] = (
    1, 2, 5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 500, 1000, 2000, 5000, 10000,
)  # fmt: skip

# Drawing unit -> millimetres.
UNIT_MM: dict[str, float] = {
    "um": 0.001,
    "mm": 1.0,
    "cm": 10.0,
    "dm": 100.0,
    "m": 1000.0,
    "km": 1_000_000.0,
    "in": 25.4,
    "ft": 304.8,
    "yd": 914.4,
    "mi": 1_609_344.0,
}
_UNIT_ALIASES = {
    "µm": "um", "micron": "um", "microns": "um", "millimeter": "mm", "millimeters": "mm",
    "centimeter": "cm", "centimeters": "cm", "meter": "m", "meters": "m", "metre": "m",
    "inch": "in", "inches": "in", "foot": "ft", "feet": "ft", "yard": "yd", "yards": "yd",
}  # fmt: skip
# $INSUNITS code -> unit name (anything else is treated as unitless).
INSUNITS: dict[int, str] = {
    1: "in", 2: "ft", 3: "mi", 4: "mm", 5: "cm", 6: "m", 7: "km", 10: "yd", 13: "um", 14: "dm",
}  # fmt: skip
DEFAULT_UNIT = "mm"
MAX_ENTITIES_ENV = "A0TD_CAD_MAX_ENTITIES"
DEFAULT_MAX_ENTITIES = 2_000_000  # drawn entities, INSERT expansions included
MAX_INSERT_DEPTH = 16  # nested block references; a deeper (or circular) drawing is refused

DWG_VERSIONS = {
    "AC1006": "R10", "AC1009": "R11/R12", "AC1012": "R13", "AC1014": "R14", "AC1015": "2000",
    "AC1018": "2004", "AC1021": "2007", "AC1024": "2010", "AC1027": "2013", "AC1032": "2018",
}  # fmt: skip
_DWG_SUPPORTED_FROM = "AC1012"  # LibreDWG decodes R13 and later; earlier ones are rejected


def normalize_unit(name: str) -> str:
    key = name.strip().lower()
    key = _UNIT_ALIASES.get(key, key)
    if key not in UNIT_MM:
        raise DomainError(
            ErrorCode.INVALID_SETTINGS,
            f"unknown drawing unit '{name}'",
            params={"units": name, "supported": sorted(UNIT_MM)},
        )
    return key


# ------------------------------------------------------------------------------ deadline


@contextlib.contextmanager
def deadline(seconds: float) -> Iterator[None]:
    """Raise ``CONVERSION_TIMEOUT`` if the block runs longer than ``seconds``.

    ezdxf renders in-process, so the limit uses ``SIGALRM`` (POSIX, main thread only). In the job
    worker the manager's own timeout and ``RLIMIT_AS`` back this up; elsewhere there is no guard.
    """
    usable = hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread()
    if not usable:
        yield
        return

    fired: list[bool] = []

    def _expired(signum: int, frame: FrameType | None) -> None:
        fired.append(True)
        raise _timeout(seconds)

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    if fired:  # ezdxf catches broad exceptions around some entities and may have eaten ours
        raise _timeout(seconds)


def _timeout(seconds: float) -> DomainError:
    return DomainError(
        ErrorCode.CONVERSION_TIMEOUT, f"the drawing took more than {seconds:g}s to render"
    )


# ----------------------------------------------------------------------------- DXF reading


def _load_dxf(path: Path) -> Any:
    import ezdxf
    from ezdxf import recover
    from ezdxf.lldxf.const import DXFVersionError

    try:
        return ezdxf.readfile(path)
    except DXFVersionError as exc:
        raise DomainError(
            ErrorCode.CONVERSION_FAILED,
            f"this DXF version is not supported: {exc}",
            params={"reason": "dxf_version_unsupported"},
        ) from exc
    except (OSError, ezdxf.DXFError, UnicodeDecodeError, ValueError):
        pass
    try:  # damaged file: let ezdxf salvage what it can
        doc, _auditor = recover.readfile(str(path))
        return doc
    except Exception as exc:
        raise DomainError(ErrorCode.CORRUPT_FILE, f"cannot read the DXF file: {exc}") from exc


def _paper_scale(dxf_layout: Any) -> float:
    """Millimetres per paper-space unit (``plot_paper_units``: 0 inches, 1 mm, 2 pixels)."""
    return 25.4 if getattr(dxf_layout.dxf, "plot_paper_units", 1) == 0 else 1.0


def list_layouts(doc: Any) -> list[CadLayout]:
    """Model space first, then the paper space layouts in tab order."""
    out = [CadLayout(name=MODEL, kind="model")]
    try:
        names = list(doc.layout_names_in_taborder())
    except Exception:  # LAYOUT objects of some DWG conversions are damaged: model space only
        log.debug("cannot order the layouts", exc_info=True)
        names = []
    for name in names:
        if name == MODEL:
            continue
        w = h = None
        try:
            dxf = doc.layouts.get(name).dxf_layout.dxf
            if dxf.paper_width > 0 and dxf.paper_height > 0:
                w, h = round(float(dxf.paper_width), 1), round(float(dxf.paper_height), 1)
                if dxf.plot_rotation in (1, 3):
                    w, h = h, w
        except Exception:  # a broken LAYOUT object only loses the declared size
            log.debug("layout %s has no usable page setup", name, exc_info=True)
        out.append(CadLayout(name=name, kind="paper", width_mm=w, height_mm=h))
    return out


def _has_content(layout: Any) -> bool:
    for entity in layout:
        if entity.dxftype() != "VIEWPORT":
            return True
        if entity.dxf.hasattr("id") and entity.dxf.id > 1 and entity.dxf.get("status", 1) != 0:
            return True
    return False


def default_layout(doc: Any, layouts: list[CadLayout]) -> str:
    """First paper space layout with something in it, else model space."""
    for item in layouts:
        if item.kind == "paper" and _has_content(doc.layouts.get(item.name)):
            return item.name
    return MODEL


def _pick_layout(doc: Any, layouts: list[CadLayout], wanted: str | None) -> CadLayout:
    if wanted is None:
        name = default_layout(doc, layouts)
    else:
        by_lower = {item.name.lower(): item.name for item in layouts}
        name = by_lower.get(wanted.strip().lower(), "")
        if not name:
            raise DomainError(
                ErrorCode.INVALID_SETTINGS,
                f"the drawing has no layout '{wanted}'",
                params={"layout": wanted, "layouts": [item.name for item in layouts]},
            )
    return next(item for item in layouts if item.name == name)


def _units_of(doc: Any, wanted: str | None) -> tuple[str, str]:
    """Model-space units and where they come from (``user``, ``insunits`` or ``assumed``)."""
    if wanted:
        return normalize_unit(wanted), "user"
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    if code in INSUNITS:
        return INSUNITS[code], "insunits"
    return DEFAULT_UNIT, "assumed"


def choose_scale(width_mm: float, height_mm: float, max_page_mm: float) -> float:
    """Smallest standard N for which a ``width x height`` drawing (at 1:1) fits an A0 sheet."""
    for n in STANDARD_SCALES:
        w, h = width_mm / n + 2 * MARGIN_MM, height_mm / n + 2 * MARGIN_MM
        short, long_ = sorted((w, h))
        if short <= A0_SHORT_MM and long_ <= A0_LONG_MM and long_ <= max_page_mm:
            return n
    return STANDARD_SCALES[-1]


# ------------------------------------------------------------------------ complexity


def max_entities() -> int:
    raw = os.environ.get(MAX_ENTITIES_ENV, "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_ENTITIES
    except ValueError:
        value = DEFAULT_MAX_ENTITIES
    return value if value > 0 else DEFAULT_MAX_ENTITIES


def _too_complex(limit: int, reason: str, detail: str) -> DomainError:
    return DomainError(
        ErrorCode.PIXEL_LIMIT_EXCEEDED,
        detail,
        params={"reason": reason, "limit_entities": limit},
    )


def _insert_copies(insert: Any) -> int:
    try:
        return max(1, int(insert.dxf.get("row_count", 1))) * max(
            1, int(insert.dxf.get("column_count", 1))
        )
    except (TypeError, ValueError):
        return 1


def count_entities(doc: Any, space: Any, limit: int) -> int:
    """Entities that drawing ``space`` would visit, INSERTs expanded (arrays included).

    Raises a ``PIXEL_LIMIT_EXCEEDED`` error as soon as the count passes ``limit``, when blocks are
    nested deeper than ``MAX_INSERT_DEPTH`` or when a block contains itself.
    """
    block_cache: dict[str, int] = {}

    def block_size(name: str, chain: tuple[str, ...]) -> int:
        key = name.lower()
        if key in chain:
            raise _too_complex(limit, "insert_cycle", f"block '{name}' contains itself")
        if len(chain) >= MAX_INSERT_DEPTH:
            raise _too_complex(
                limit, "insert_depth", f"blocks are nested more than {MAX_INSERT_DEPTH} levels"
            )
        if key in block_cache:
            return block_cache[key]
        block = doc.blocks.get(name)
        n = walk(block, (*chain, key)) if block is not None else 0
        block_cache[key] = n
        return n

    def walk(entities: Any, chain: tuple[str, ...]) -> int:
        n = 0
        for entity in entities:
            n += 1
            if entity.dxftype() == "INSERT":
                n += _insert_copies(entity) * block_size(entity.dxf.get("name", ""), chain)
            if n > limit:
                raise _too_complex(limit, "entities", f"the drawing has more than {limit} entities")
        return n

    return walk(space, ())


# --------------------------------------------------------------------------- rendering


def _configuration(color: CadColor) -> Any:
    from ezdxf.addons.drawing.config import (
        BackgroundPolicy,
        ColorPolicy,
        Configuration,
        ImagePolicy,
    )

    policy = {
        CadColor.COLOR: ColorPolicy.COLOR,
        CadColor.BLACK: ColorPolicy.BLACK,
        CadColor.GRAY: ColorPolicy.MONOCHROME,
    }[color]
    # White paper; absolute line weights (the CAD drawing's own, in mm on paper) stay on.
    return Configuration(
        color_policy=policy,
        background_policy=BackgroundPolicy.WHITE,
        lineweight_scaling=1.0,
        min_lineweight=0.1,
        # A drawing must not make the server open files named in it (IMAGEDEF paths): images
        # are drawn as rectangles.
        image_policy=ImagePolicy.RECT,
    )


PT_PER_MM = 72.0 / 25.4


def make_exact_backend() -> Any:
    """ezdxf's PyMuPDF backend that remembers the final page it was asked to draw.

    ezdxf truncates the page size and the output coordinate space to whole points, which
    shrinks the drawing by up to 1 pt in 3000 (0.3 mm on an A0 sheet); ``fix_page_size`` undoes it.
    """
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend

    class ExactBackend(PyMuPdfBackend):
        final: tuple[float, float, float] | None = None

        def make_backend(self, page: Any, settings: Any) -> Any:  # type: ignore[override]
            self.final = (
                float(page.width_in_mm),
                float(page.height_in_mm),
                float(settings.output_coordinate_space),
            )
            return PyMuPdfBackend.make_backend(page, settings)

    return ExactBackend()


def fix_page_size(data: bytes, width_mm: float, height_mm: float, space: float) -> bytes:
    """Give the page its exact size (float points) and the content its exact scale.

    The content was drawn at ``space / max(width, height)`` points per mm instead of 72 / 25.4 and
    on a page truncated to whole points; scale it about the top-left corner and size the boxes.
    """
    if space <= 0 or width_mm <= 0 or height_mm <= 0:
        return data
    k = PT_PER_MM * max(width_mm, height_mm) / space
    new_w, new_h = width_mm * PT_PER_MM, height_mm * PT_PER_MM
    with pymupdf.open("pdf", data) as doc:
        page = doc[0]
        old_h = page.mediabox.height
        page.wrap_contents()
        first = page.get_contents()[0]
        head = doc.xref_stream(first) or b""
        cm = f"{k:.9f} 0 0 {k:.9f} 0 {new_h - k * old_h:.6f} cm"
        doc.update_stream(first, b" ".join((head, cm.encode(), b"")))
        box = f"[0 0 {new_w:.6f} {new_h:.6f}]"
        doc.xref_set_key(page.xref, "MediaBox", box)
        doc.xref_set_key(page.xref, "CropBox", box)
        return bytes(doc.tobytes())


def _render(
    doc: Any,
    layout_name: str,
    page: Any,
    scale: float,
    color: CadColor,
    fit_page: bool,
) -> bytes:
    from ezdxf.addons.drawing import Frontend, RenderContext, layout

    ctx = RenderContext(doc)
    backend = make_exact_backend()
    Frontend(ctx, backend, config=_configuration(color)).draw_layout(doc.layouts.get(layout_name))
    settings = layout.Settings(scale=scale, fit_page=fit_page)
    data = bytes(backend.get_pdf_bytes(page, settings=settings))
    if backend.final is None:
        return data
    return fix_page_size(data, *backend.final)


def _ensure_layers(doc: Any, layout_name: str) -> None:
    """Give every entity of the layout a layer that exists.

    DXF files written without a LAYER table (LibreDWG's entities-only output) otherwise draw in an
    undefined, white-on-white colour.
    """
    known = {layer.dxf.name.lower() for layer in doc.layers}
    for entity in doc.layouts.get(layout_name):
        name = entity.dxf.get("layer", "")
        if not name:
            entity.dxf.layer = name = "0"
        if name.lower() not in known:
            doc.layers.add(name)
            known.add(name.lower())


def _visible_entities(doc: Any, layout_obj: Any) -> list[Any]:
    """Entities of the layout on layers that are neither off nor frozen (those are not drawn)."""
    hidden: set[str] = set()
    for layer in doc.layers:
        try:
            if layer.is_off() or layer.is_frozen():
                hidden.add(layer.dxf.name.lower())
        except Exception:  # a damaged LAYER entry counts as visible
            log.debug("cannot read the state of a layer", exc_info=True)
    return [e for e in layout_obj if str(e.dxf.get("layer", "0")).lower() not in hidden]


def _extents_mm(doc: Any, layout_obj: Any, factor: float) -> tuple[float, float]:
    from ezdxf import bbox

    box = bbox.extents(_visible_entities(doc, layout_obj), fast=True)
    size = box.size if box.has_data else None
    if size is None or not (math.isfinite(size.x) and math.isfinite(size.y)):
        raise DomainError(ErrorCode.CORRUPT_FILE, "the drawing has no geometry")
    return float(size.x) * factor, float(size.y) * factor


def render_dxf(dxf_path: Path, pdf_path: Path, options: CadOptions, max_page_mm: float) -> CadInfo:
    """Draw one layout of ``dxf_path`` into ``pdf_path`` and describe the choices made."""
    from ezdxf.addons.drawing import layout as dlayout

    doc = _load_dxf(dxf_path)
    layouts = list_layouts(doc)
    chosen = _pick_layout(doc, layouts, options.layout)
    _ensure_layers(doc, chosen.name if chosen.kind == "paper" else MODEL)
    limit = max_entities()
    count_entities(doc, doc.layouts.get(chosen.name), limit)
    if chosen.kind == "paper":  # its viewports draw model space as well
        count_entities(doc, doc.modelspace(), limit)
    info = CadInfo(
        layouts=layouts,
        layout=chosen.name,
        units=DEFAULT_UNIT,
        units_source="assumed",
        color=options.color,
    )
    units, source = _units_of(doc, options.units)
    info.units, info.units_source = units, source  # type: ignore[assignment]
    if chosen.kind == "paper":
        paper = doc.layouts.get(chosen.name)
        dxf_layout = paper.dxf_layout
        if chosen.width_mm and chosen.height_mm:
            page = dlayout.Page.from_dxf_layout(dxf_layout)
            page.margins = dlayout.Margins.all(0)
        else:
            page = dlayout.Page(0, 0, dlayout.Units.mm, margins=dlayout.Margins.all(MARGIN_MM))
        scale = _paper_scale(dxf_layout)
        data = _render(doc, chosen.name, page, scale, options.color, fit_page=False)
    else:
        factor = UNIT_MM[units]
        width, height = _extents_mm(doc, doc.modelspace(), factor)
        if options.scale is None:
            n = choose_scale(width, height, max_page_mm)
            info.scale_auto = True
        else:
            n = options.scale
        info.scale = n
        if max(width, height) / n + 2 * MARGIN_MM > max_page_mm:
            raise DomainError(
                ErrorCode.PAGE_TOO_LARGE,
                f"the drawing is {width / n:.0f} x {height / n:.0f} mm at 1:{n:g}, "
                f"the limit is {max_page_mm:.0f} mm",
                params={"limit_mm": max_page_mm, "scale": n},
            )
        page = dlayout.Page(0, 0, dlayout.Units.mm, margins=dlayout.Margins.all(MARGIN_MM))
        data = _render(doc, MODEL, page, factor / n, options.color, fit_page=False)
    pdf_path.write_bytes(data)
    return info


def _page_has_ink(pdf_path: Path) -> bool:
    with pymupdf.open(pdf_path) as pdf:
        return pdf.page_count > 0 and bool(pdf[0].get_drawings() or pdf[0].get_text().strip())


class DxfConverter:
    id = "dxf"
    handles = frozenset({DocFormat.DXF})

    def available(self) -> bool:
        try:
            import ezdxf  # noqa: F401
        except ImportError:
            return False
        return True

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        return self.convert_dxf(src, work, ctx, DocFormat.DXF)

    @staticmethod
    def convert_dxf(
        dxf: Path, work: Path, ctx: ConvertContext, source_format: DocFormat
    ) -> CanonicalDocument:
        scratch = ctx.scratch_dir(work)
        out_dir = fresh_dir(scratch / "cad-out")
        pdf = out_dir / "drawing.pdf"
        options = ctx.cad or CadOptions()
        ctx.report("convert", 10, "ingest.convert", tool="ezdxf")
        pymupdf.TOOLS.mupdf_display_errors(False)
        try:
            with deadline(float(ctx.config.job_timeout_s)):
                info = render_dxf(dxf, pdf, options, float(ctx.config.max_page_mm))
        except DomainError:
            raise
        except MemoryError:
            raise
        except Exception as exc:
            raise DomainError(
                ErrorCode.CONVERSION_FAILED, f"cannot draw the DXF: {type(exc).__name__}: {exc}"
            ) from exc
        if not _page_has_ink(pdf):
            raise DomainError(ErrorCode.CORRUPT_FILE, "the layout has nothing to draw")
        canon = pdf_to_canonical(pdf, work, ctx, source_format)
        # Unitless drawings (``units_source == "assumed"``) are read as millimetres; the CAD options
        # dialog shows that and lets the user pick the real units, so the generic raster/SVG
        # "assumed size" flow (which sets a size on the page itself) does not apply here.
        canon.cad = info
        if info.units_source == "assumed" and info.scale is not None:
            canon.warnings.append(
                PlanWarning(code=WarningCode.PHYSICAL_SIZE_ASSUMED, params={"reason": "cad_units"})
            )
        return canon


# ----------------------------------------------------------------------------------- DWG


def dwg_version(path: Path) -> str | None:
    with path.open("rb") as fh:
        head = fh.read(6)
    return head.decode("ascii", "replace") if re.fullmatch(rb"AC10\d\d", head) else None


def _version_name(code: str | None) -> str:
    return DWG_VERSIONS.get(code or "", code or "unknown")


class DwgConverter:
    id = "dwg"
    handles = frozenset({DocFormat.DWG})

    def available(self) -> bool:
        return DxfConverter().available() and (
            tool_available("dwg2dxf") or self._oda(None) is not None
        )

    @staticmethod
    def _oda(ctx: ConvertContext | None) -> Path | None:
        if ctx is not None:
            path = ctx.config.oda_converter
        else:
            from a0td.config import get_config

            path = get_config().oda_converter
        if path is not None and path.is_file() and os.access(path, os.X_OK):
            return path
        return None

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        scratch = ctx.scratch_dir(work)
        version = dwg_version(src)
        if version is not None and version < _DWG_SUPPORTED_FROM:
            raise DomainError(
                ErrorCode.CONVERSION_FAILED,
                f"DWG version {_version_name(version)} ({version}) is not supported; "
                "export the drawing as DXF or PDF from the CAD program",
                params={"reason": "dwg_version_unsupported", "version": version},
            )
        out_dir = fresh_dir(scratch / "dwg-out")
        dxf = out_dir / "drawing.dxf"
        ctx.report("convert", 5, "ingest.convert", tool="libredwg")
        errors: list[DomainError] = []
        oda = self._oda(ctx)
        by_oda = oda is not None and self._via_oda(oda, src, scratch, out_dir, dxf, ctx, errors)
        if not by_oda and tool_available("dwg2dxf"):
            self._via_libredwg(src, dxf, scratch, ctx, version, errors)
        if not dxf.is_file() or dxf.stat().st_size == 0:
            raise (
                errors[-1]
                if errors
                else DomainError(ErrorCode.CONVERSION_FAILED, "no DWG converter is available")
            )
        try:
            return DxfConverter.convert_dxf(dxf, work, ctx, DocFormat.DWG)
        except DomainError as first:
            if by_oda or first.code not in (ErrorCode.CORRUPT_FILE, ErrorCode.CONVERSION_FAILED):
                raise
            # LibreDWG sometimes writes a DXF whose tables or layouts ezdxf cannot use: retry
            # with the entities only (no tables, blocks or layouts).
            minimal = out_dir / "minimal.dxf"
            self._via_libredwg(src, minimal, scratch, ctx, version, errors, minimal=True)
            if not minimal.is_file() or minimal.stat().st_size == 0:
                raise first from None
            try:
                canon = DxfConverter.convert_dxf(minimal, work, ctx, DocFormat.DWG)
            except DomainError:
                raise first from None
        canon.warnings.append(
            PlanWarning(
                code=WarningCode.LOW_FIDELITY_FORMAT,
                params={"format": "dwg", "reason": "entities_only"},
            )
        )
        return canon

    @staticmethod
    def _via_libredwg(
        src: Path,
        dxf: Path,
        scratch: Path,
        ctx: ConvertContext,
        version: str | None,
        errors: list[DomainError],
        *,
        minimal: bool = False,
    ) -> None:
        staged = stage_input(src, scratch, "input.dwg")
        argv: list[str | os.PathLike[str]] = ["dwg2dxf", "-y"]
        if minimal:
            argv.append("-m")
        try:
            run_tool(
                [*argv, "-o", dxf, staged],
                scratch=scratch,
                timeout_s=ctx.config.job_timeout_s,
                memory_mb=ctx.config.job_memory_mb,
                ok_codes=range(0, 128),  # >= 128: critical errors; lower ones are recoverable
            )
        except DomainError as exc:
            if exc.code is ErrorCode.CONVERSION_TIMEOUT:
                raise
            errors.append(_dwg_error(exc, version))
            dxf.unlink(missing_ok=True)
            return
        if not dxf.is_file() or dxf.stat().st_size == 0:
            errors.append(
                DomainError(
                    ErrorCode.CONVERSION_FAILED,
                    f"LibreDWG could not read this DWG (version {_version_name(version)})",
                    params={"reason": "dwg_unreadable", "version": version},
                )
            )

    @staticmethod
    def _via_oda(
        oda: Path,
        src: Path,
        scratch: Path,
        out_dir: Path,
        dxf: Path,
        ctx: ConvertContext,
        errors: list[DomainError],
    ) -> bool:
        """ODA File Converter (user supplied): folder in, folder out, ACAD2018 DXF."""
        in_dir = fresh_dir(scratch / "oda-in")
        shutil.copyfile(src, in_dir / "drawing.dwg")
        oda_out = fresh_dir(out_dir / "oda")
        argv: list[str | os.PathLike[str]] = [oda, in_dir, oda_out, "ACAD2018", "DXF", "0", "1"]
        if tool_available("xvfb-run"):
            argv = ["xvfb-run", "-a", *argv]
        try:
            run_tool(
                argv,
                scratch=scratch,
                timeout_s=ctx.config.job_timeout_s,
                memory_mb=None,  # Qt + Xvfb do not live within an address-space limit
            )
        except DomainError as exc:
            if exc.code is ErrorCode.CONVERSION_TIMEOUT:
                raise
            errors.append(_dwg_error(exc, dwg_version(src)))
            return False
        produced = oda_out / "drawing.dxf"
        if not produced.is_file() or produced.stat().st_size == 0:
            return False
        shutil.move(produced, dxf)
        return True


def _dwg_error(exc: DomainError, version: str | None) -> DomainError:
    return DomainError(
        ErrorCode.CONVERSION_FAILED,
        f"cannot convert this DWG (version {_version_name(version)}): {exc.detail}",
        params={"reason": "dwg_unreadable", "version": version},
    )
