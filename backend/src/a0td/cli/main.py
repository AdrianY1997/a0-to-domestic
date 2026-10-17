"""``a0td`` command line: info, plan, tile, fit, formats, calibration-page (SPEC CLI-01).

No database and no HTTP: documents are ingested into a temporary work directory.
"""

import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from a0td.domain.errors import DomainError
from a0td.domain.papers import Orientation, PaperId
from a0td.domain.planner import plan as make_plan
from a0td.domain.settings import (
    JobSettings,
    PrinterProfile,
    get_builtin_preset,
    get_builtin_printer,
    merge_settings,
    parse_page_ranges,
)
from a0td.ingest import (
    CadColor,
    CadOptions,
    IngestResult,
    default_registry,
    ingest_document,
    load_occupancy,
    page_dir,
)

app = typer.Typer(help="A0 to domestic printer: tile large documents into printable sheets.")

FileArg = Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)]


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def _domain_exit(exc: DomainError) -> typer.Exit:
    return _fail(f"error [{exc.code.value}]: {exc.detail}")


def _cad_options(
    layout: str | None, units: str | None, scale: float | None, color: CadColor | None
) -> CadOptions | None:
    """The ``--cad-*`` flags as ingest options (``None`` when none was given)."""
    if layout is None and units is None and scale is None and color is None:
        return None
    try:
        return CadOptions(layout=layout, units=units, scale=scale, color=color or CadColor.COLOR)
    except ValidationError as exc:
        details = "; ".join(f"cad {e['loc'][0]}: {e['msg']}" for e in exc.errors())
        raise _fail(f"error [INVALID_SETTINGS]: {details}") from None


def _parse_margins(raw: str) -> dict[str, float]:
    try:
        parts = [float(p) for p in raw.split(",")]
    except ValueError:
        parts = []
    if len(parts) == 1:
        parts *= 4
    if len(parts) != 4:
        raise _fail(f"invalid --margins {raw!r}: use N or top,right,bottom,left (mm)")
    return dict(zip(("top", "right", "bottom", "left"), parts, strict=True))


def _parse_grid(raw: str) -> tuple[int, int]:
    try:
        c, r = raw.lower().split("x")
        return int(c), int(r)
    except ValueError:
        raise _fail(f"invalid --fit-grid {raw!r}: use CxR, e.g. 3x2") from None


def _parse_final_size(raw: str) -> dict[str, float]:
    try:
        if raw.lower().startswith("x"):
            return {"height_mm": float(raw[1:])}
        return {"width_mm": float(raw)}
    except ValueError:
        raise _fail(f"invalid --final-size {raw!r}: use W (width mm) or xH (height mm)") from None


def _parse_drawing_scale(raw: str) -> tuple[float, float]:
    try:
        a, b = raw.split(":")
        return float(a), float(b)
    except ValueError:
        raise _fail(f"invalid --drawing-scale {raw!r}: use A:B, e.g. 50:100") from None


def _parse_align(raw: str) -> dict[str, str]:
    parts = [p.strip().lower() for p in raw.split(",")]
    if len(parts) == 1:
        parts *= 2
    if len(parts) != 2 or any(p not in ("start", "center", "end") for p in parts):
        raise _fail(f"invalid --align {raw!r}: use x,y with start|center|end")
    return {"x": parts[0], "y": parts[1]}


def build_settings(
    *,
    settings_file: Path | None = None,
    preset: str | None = None,
    paper: PaperId | None = None,
    orientation: Orientation | None = None,
    scale: float | None = None,
    fit_grid: str | None = None,
    fit_count: int | None = None,
    fit_sheet: bool = False,
    overlap: float | None = None,
    join: str | None = None,
    margins: str | None = None,
    printer: str | None = None,
    no_skip_blank: bool = False,
    pages: str | None = None,
    rotate: int | None = None,
    final_size: str | None = None,
    drawing_scale: str | None = None,
    align: str | None = None,
    order: str | None = None,
    naming: str | None = None,
) -> JobSettings:
    """defaults < preset < settings file < flags."""
    try:
        base = JobSettings()
        if preset:
            p = get_builtin_preset(preset)
            if p is None:
                raise _fail(f"unknown preset {preset!r}")
            base = p.apply(base)
        if settings_file:
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise _fail(f"error [INVALID_SETTINGS]: cannot read settings file: {exc}") from None
            if not isinstance(data, dict):
                raise _fail("error [INVALID_SETTINGS]: settings file must contain a JSON object")
            base = merge_settings(data, base)
        target: dict[str, Any] = {}
        tiling: dict[str, Any] = {}
        source: dict[str, Any] = {}
        flags: dict[str, Any] = {}
        if paper:
            target["paper"] = paper.value
        if orientation:
            target["orientation"] = orientation.value
        if printer:
            if get_builtin_printer(printer) is None:
                raise _fail(f"unknown printer {printer!r}")
            target["printer_id"] = printer
        if margins:
            target["margins_mm"] = _parse_margins(margins)
        if fit_grid:
            c, r = _parse_grid(fit_grid)
            flags["scale"] = {"mode": "fit_grid", "cols": c, "rows": r}
        elif fit_count:
            flags["scale"] = {"mode": "fit_count", "max_sheets": fit_count}
        elif fit_sheet:
            flags["scale"] = {"mode": "fit_sheet"}
        elif final_size:
            flags["scale"] = {"mode": "final_size", **_parse_final_size(final_size)}
        elif drawing_scale:
            a, b = _parse_drawing_scale(drawing_scale)
            flags["scale"] = {"mode": "drawing_scale", "from_denominator": a, "to_denominator": b}
        elif scale is not None:
            flags["scale"] = {"mode": "percent", "percent": scale}
        if join:
            tiling["join"] = join
        if overlap is not None:
            tiling["overlap_mm"] = overlap
        if align:
            tiling["align"] = _parse_align(align)
        if order:
            tiling["order"] = order
        if naming:
            tiling["naming"] = naming
        if no_skip_blank:
            tiling["skip_blank"] = False
        if pages:
            source["pages"] = pages
        if rotate is not None:
            source["rotate"] = rotate
        for key, value in (("target", target), ("tiling", tiling), ("source", source)):
            if value:
                flags[key] = value
        return merge_settings(flags, base)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise _fail(f"error [INVALID_SETTINGS]: {details}") from None


def _printer_for(js: JobSettings) -> PrinterProfile | None:
    return get_builtin_printer(js.target.printer_id) or get_builtin_printer("builtin-generic")


@contextmanager
def _ingested(
    src: Path, password: str | None, cad: CadOptions | None = None
) -> Iterator[tuple[Path, IngestResult]]:
    with tempfile.TemporaryDirectory(prefix="a0td-cli-") as tmp:
        workdir = Path(tmp) / "doc"
        workdir.mkdir()
        try:
            result = ingest_document(
                src, workdir, password=password, filename=src.name, cad_options=cad
            )
        except DomainError as exc:
            raise _domain_exit(exc) from None
        (workdir / "document.json").write_text(
            json.dumps({"id": "cli", "name": src.name}), encoding="utf-8"
        )
        yield workdir, result


Paper = Annotated[PaperId | None, typer.Option("--paper", help="Paper id, e.g. A4, LETTER")]
Orient = Annotated[Orientation | None, typer.Option("--orientation")]
Scale = Annotated[float | None, typer.Option("--scale", help="Scale in percent")]
FitGrid = Annotated[str | None, typer.Option("--fit-grid", help="Fit to CxR sheets, e.g. 3x2")]
FitCount = Annotated[int | None, typer.Option("--fit-count", help="Fit to at most N sheets")]
Overlap = Annotated[float | None, typer.Option("--overlap", help="Overlap in mm")]
Join = Annotated[str | None, typer.Option("--join", help="overlap | butt")]
MarginsOpt = Annotated[str | None, typer.Option("--margins", help="mm: N or t,r,b,l")]
PrinterOpt = Annotated[str | None, typer.Option("--printer", help="Printer profile id")]
PresetOpt = Annotated[str | None, typer.Option("--preset", help="Built-in preset id")]
NoSkip = Annotated[bool, typer.Option("--no-skip-blank", help="Print blank sheets too")]
Pages = Annotated[str | None, typer.Option("--pages", help='"all" or "1,3-5"')]
Rotate = Annotated[int | None, typer.Option("--rotate", help="0, 90, 180 or 270")]
Password = Annotated[str | None, typer.Option("--password", help="Document password")]
CadLayoutOpt = Annotated[
    str | None, typer.Option("--cad-layout", help="DXF/DWG layout name (default: automatic)")
]
CadUnitsOpt = Annotated[
    str | None,
    typer.Option(
        "--cad-units", help="DXF/DWG model-space units: mm, cm, m, in, ft... (default: $INSUNITS)"
    ),
]
CadScaleOpt = Annotated[
    float | None,
    typer.Option(
        "--cad-scale", min=0.000001, help="DXF/DWG model-space scale 1:N (default: automatic)"
    ),
]
CadColorOpt = Annotated[
    CadColor | None, typer.Option("--cad-color", help="DXF/DWG colour policy: color | black | gray")
]
SettingsFile = Annotated[
    Path | None,
    typer.Option(
        "--settings",
        exists=True,
        dir_okay=False,
        help="JobSettings JSON object; options with no flag (marks, color...) only here",
    ),
]
AsJson = Annotated[bool, typer.Option("--json", help="Machine-readable output")]
OutDir = Annotated[
    Path | None,
    typer.Option("-o", "--output", help="Output directory (default: the input file's directory)"),
]
FitSheet = Annotated[bool, typer.Option("--fit-sheet", help="Scale each page to fit one sheet")]
FinalSize = Annotated[
    str | None, typer.Option("--final-size", help="Final size in mm: W (width) or xH (height)")
]
DrawingScaleOpt = Annotated[
    str | None, typer.Option("--drawing-scale", help="Drawing scale A:B, e.g. 50:100")
]
AlignOpt = Annotated[
    str | None, typer.Option("--align", help="x,y each start|center|end, e.g. start,center")
]
OrderOpt = Annotated[str | None, typer.Option("--order", help="rows | columns")]
NamingOpt = Annotated[
    str | None, typer.Option("--naming", help="letter_number | number_number | sequential")
]


@app.command()
def formats(as_json: AsJson = False) -> None:
    """List detected format ids and converter availability."""
    infos = default_registry().info()
    if as_json:
        typer.echo(json.dumps(infos, indent=2))
        return
    for item in infos:
        state = "available" if item["available"] else "NOT available"
        typer.echo(f"{item['id']}: {state}")
        typer.echo(f"  {', '.join(item['formats'])}")


@app.command()
def info(
    file: FileArg,
    password: Password = None,
    cad_layout: CadLayoutOpt = None,
    cad_units: CadUnitsOpt = None,
    cad_scale: CadScaleOpt = None,
    cad_color: CadColorOpt = None,
    as_json: AsJson = False,
) -> None:
    """Ingest FILE and describe its pages (DXF/DWG: see the --cad-* options)."""
    cad = _cad_options(cad_layout, cad_units, cad_scale, cad_color)
    with _ingested(file, password, cad) as (_, result):
        if as_json:
            typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))
            return
        typer.echo(f"{file.name}: {result.format.value}, {len(result.pages)} page(s)")
        typer.echo("page  size_mm           standard  kind    dpi    warnings")
        for p in result.pages:
            std = p.standard_match.id if p.standard_match else "-"
            dpi = f"{p.dpi:.0f}" if p.dpi else "-"
            warns = ",".join(w.code.value for w in p.warnings) or "-"
            size = f"{p.width_mm:.1f} x {p.height_mm:.1f}"
            typer.echo(f"{p.index + 1:<5} {size:<17} {std:<9} {p.kind.value:<7} {dpi:<6} {warns}")
        if result.cad is not None:
            c = result.cad
            scale = f", scale 1:{c.scale:g}" if c.scale else ""
            names = ", ".join(item.name for item in c.layouts)
            typer.echo(f"cad: layout {c.layout!r}{scale}, units {c.units}, color {c.color.value}")
            typer.echo(f"cad layouts: {names}")
        for w in result.warnings:
            typer.echo(f"warning: {w.code.value}")


@app.command(name="plan")
def plan_cmd(
    file: FileArg,
    paper: Paper = None,
    orientation: Orient = None,
    scale: Scale = None,
    fit_grid: FitGrid = None,
    fit_count: FitCount = None,
    fit_sheet: FitSheet = False,
    final_size: FinalSize = None,
    drawing_scale: DrawingScaleOpt = None,
    align: AlignOpt = None,
    order: OrderOpt = None,
    naming: NamingOpt = None,
    overlap: Overlap = None,
    join: Join = None,
    margins: MarginsOpt = None,
    printer: PrinterOpt = None,
    preset: PresetOpt = None,
    no_skip_blank: NoSkip = False,
    pages: Pages = None,
    rotate: Rotate = None,
    password: Password = None,
    cad_layout: CadLayoutOpt = None,
    cad_units: CadUnitsOpt = None,
    cad_scale: CadScaleOpt = None,
    cad_color: CadColorOpt = None,
    settings: SettingsFile = None,
    page: Annotated[int | None, typer.Option("--page", help="1-based page")] = None,
    as_json: AsJson = False,
) -> None:
    """Show the tiling plan (grid, sheets, scale, warnings) without rendering.

    Settings without a flag (marks, color, output, ...) go via --settings.
    """
    js = build_settings(
        settings_file=settings,
        preset=preset,
        paper=paper,
        orientation=orientation,
        scale=scale,
        fit_grid=fit_grid,
        fit_count=fit_count,
        fit_sheet=fit_sheet,
        final_size=final_size,
        drawing_scale=drawing_scale,
        align=align,
        order=order,
        naming=naming,
        overlap=overlap,
        join=join,
        margins=margins,
        printer=printer,
        no_skip_blank=no_skip_blank,
        pages=pages,
        rotate=rotate,
    )
    prof = _printer_for(js)
    cad = _cad_options(cad_layout, cad_units, cad_scale, cad_color)
    with _ingested(file, password, cad) as (workdir, result):
        count = len(result.pages)
        if page is not None:
            if not 1 <= page <= count:
                raise _fail(f"--page {page} out of range (document has {count} pages)")
            wanted = [page]
        else:
            try:
                wanted = parse_page_ranges(js.source.pages, count)
            except ValueError as exc:
                raise _fail(f"error [INVALID_SETTINGS]: {exc}") from None
        plans = []
        for n in wanted:
            pg = result.pages[n - 1]
            occ = None
            if js.tiling.skip_blank:
                try:
                    occ = load_occupancy(
                        page_dir(workdir, pg.index), pg, js.tiling.blank_sensitivity
                    )
                except (FileNotFoundError, OSError):
                    occ = None
            try:
                plans.append(make_plan(pg.to_geometry(count), js, printer=prof, occupancy=occ))
            except DomainError as exc:
                raise _domain_exit(exc) from None
    if as_json:
        typer.echo(json.dumps([p.model_dump(mode="json") for p in plans], indent=2))
        return
    for p in plans:
        typer.echo(
            f"page {p.page_index + 1}: {p.grid.cols}x{p.grid.rows} sheets of "
            f"{p.paper.id} {p.orientation.value} "
            f"({p.paper.width_mm:g}x{p.paper.height_mm:g} mm), scale {p.scale_label}, "
            f"overlap {p.overlap_mm:g} mm, printed {p.counts.printed}/{p.counts.total} "
            f"(blank {p.counts.blank})"
        )
        for w in p.warnings:
            typer.echo(f"  warning: {w.code.value} {w.params or ''}".rstrip())


def _render(
    file: Path,
    out: Path | None,
    js: JobSettings,
    password: str | None,
    as_json: bool,
    cad: CadOptions | None = None,
) -> None:
    from a0td.render.engine import render_job

    out = out if out is not None else file.resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    with _ingested(file, password, cad) as (workdir, _):
        try:
            res = render_job(workdir, js, out, printer=_printer_for(js))
        except DomainError as exc:
            raise _domain_exit(exc) from None
    paths = [str(out / o.name) for o in res.outputs]
    if as_json:
        warnings = [w.model_dump(mode="json") for w in res.warnings]
        typer.echo(json.dumps({"outputs": paths, "warnings": warnings}))
        return
    for path in paths:
        typer.echo(path)
    for w in res.warnings:
        typer.echo(f"warning: {w.code}", err=True)


@app.command()
def tile(
    file: FileArg,
    output: OutDir = None,
    paper: Paper = None,
    orientation: Orient = None,
    scale: Scale = None,
    fit_grid: FitGrid = None,
    fit_count: FitCount = None,
    fit_sheet: FitSheet = False,
    final_size: FinalSize = None,
    drawing_scale: DrawingScaleOpt = None,
    align: AlignOpt = None,
    order: OrderOpt = None,
    naming: NamingOpt = None,
    overlap: Overlap = None,
    join: Join = None,
    margins: MarginsOpt = None,
    printer: PrinterOpt = None,
    preset: PresetOpt = None,
    no_skip_blank: NoSkip = False,
    pages: Pages = None,
    rotate: Rotate = None,
    password: Password = None,
    cad_layout: CadLayoutOpt = None,
    cad_units: CadUnitsOpt = None,
    cad_scale: CadScaleOpt = None,
    cad_color: CadColorOpt = None,
    settings: SettingsFile = None,
    as_json: AsJson = False,
) -> None:
    """Render FILE tiled into printable sheets.

    Settings without a flag (marks, color, output, ...) go via --settings.
    """
    js = build_settings(
        settings_file=settings,
        preset=preset,
        paper=paper,
        orientation=orientation,
        scale=scale,
        fit_grid=fit_grid,
        fit_count=fit_count,
        fit_sheet=fit_sheet,
        final_size=final_size,
        drawing_scale=drawing_scale,
        align=align,
        order=order,
        naming=naming,
        overlap=overlap,
        join=join,
        margins=margins,
        printer=printer,
        no_skip_blank=no_skip_blank,
        pages=pages,
        rotate=rotate,
    )
    cad = _cad_options(cad_layout, cad_units, cad_scale, cad_color)
    _render(file, output, js, password, as_json, cad)


@app.command()
def fit(
    file: FileArg,
    output: OutDir = None,
    paper: Paper = None,
    orientation: Orient = None,
    margins: MarginsOpt = None,
    printer: PrinterOpt = None,
    preset: PresetOpt = None,
    pages: Pages = None,
    rotate: Rotate = None,
    password: Password = None,
    cad_layout: CadLayoutOpt = None,
    cad_units: CadUnitsOpt = None,
    cad_scale: CadScaleOpt = None,
    cad_color: CadColorOpt = None,
    settings: SettingsFile = None,
    as_json: AsJson = False,
) -> None:
    """Scale FILE to fit one sheet (scale mode fit_sheet).

    Settings without a flag (marks, color, output, ...) go via --settings.
    """
    js = build_settings(
        settings_file=settings,
        preset=preset,
        paper=paper,
        orientation=orientation,
        fit_sheet=True,
        margins=margins,
        printer=printer,
        pages=pages,
        rotate=rotate,
    )
    cad = _cad_options(cad_layout, cad_units, cad_scale, cad_color)
    _render(file, output, js, password, as_json, cad)


@app.command("calibration-page")
def calibration_page(
    paper: Annotated[str, typer.Option("--paper", help="Paper id (A4, LETTER, A3, ...).")] = "A4",
    output: Annotated[Path | None, typer.Option("-o", "--output", help="Output folder.")] = None,
    locale: Annotated[str, typer.Option("--locale", help="Instructions language (es|en).")] = "es",
) -> None:
    """Write the printer calibration page (rulers, 50 mm / 2 in squares, margin frames)."""
    from a0td.render.calibration import render_calibration_pdf

    try:
        pid = PaperId(paper.upper())
        if pid is PaperId.CUSTOM:
            raise ValueError(paper)
    except ValueError:
        raise _fail(f"error [INVALID_SETTINGS]: unknown paper '{paper}'") from None
    out = output or Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"calibration-{pid.value}.pdf"
    path.write_bytes(render_calibration_pdf(pid, locale=locale))
    typer.echo(str(path))


if __name__ == "__main__":
    app()
