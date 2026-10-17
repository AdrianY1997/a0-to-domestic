"""HPGL / plotter converter: Inkscape's HPGL import extension, then SVG -> PDF (SPEC 7.1).

Inkscape's ``hpgl2_input.py`` (inkex; options ``--width`` / ``--height`` in inches and
``--resolution`` in plu/in, checked against the extension in the image) lays the plot out on a fixed
"picture frame" (default 8 x 10 in), and anything outside it is cut off. The frame is therefore
sized from the extent of the drawing commands of the file, PE polylines and IP / IR / SC scaling
included (40 plotter units per mm = 1016 per inch) and the extension is run as a script, which
gives control over its parameters; the SVG it writes is turned into a PDF with Inkscape. If the
script is not on the image, ``inkscape file.hpgl`` is used with the default frame.

The extension needs the system ``python3`` with lxml, numpy, pyparsing and tinycss2 (Dockerfile,
``full-tools``); the virtualenv Python used by the app does not have them.
"""

import math
import re
from pathlib import Path

from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.domain.plan import PlanWarning
from a0td.ingest.converters.base import ConvertContext, run_tool, tool_available
from a0td.ingest.converters.common import fresh_dir, pdf_to_canonical, stage_input, tool_env
from a0td.ingest.models import CanonicalDocument, DocFormat

EXTENSION_DIR = Path("/usr/share/inkscape/extensions")
EXTENSION_SCRIPT = "hpgl2_input.py"
SYSTEM_PYTHON = "/usr/bin/python3"

PLU_PER_INCH = 1016.0  # HPGL plotter units: 0.025 mm
DEFAULT_FRAME_IN = (8.0, 10.0)  # the extension's own default
MAX_FRAME_IN = 1000.0  # limit of the extension parameter
_PAD_IN = 0.1

_COMMAND = re.compile(r"([A-Za-z]{2})([^;\n\r]*)")
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)")
_LABEL = re.compile(r"LB[^\x03]*\x03", re.DOTALL)
_ESCAPE_SEQ = re.compile(r"\x1b[^\x00-\x1f]{0,40}?[A-Z]")


def _numbers(arg: str) -> list[float]:
    return [float(n) for n in _NUMBER.findall(arg)]


def decode_pe(data: str, x: float, y: float) -> tuple[list[tuple[float, float]], float, float]:
    """Decode the data of a ``PE`` (polyline encoded) command.

    Returns the successive pen positions (plotter or user units, as the file uses them) and the
    final position. Coordinates are relative unless preceded by ``=``; ``<`` lifts the pen,
    ``>n`` sets ``n`` fraction bits, ``:n`` selects a pen, ``7`` switches to 7-bit (base 32) data.
    """
    base32 = False
    frac = 0
    absolute = False
    special: str | None = None
    acc = shift = 0
    pending: list[float] = []
    points: list[tuple[float, float]] = []
    for ch in data:
        c = ord(ch)
        if acc == 0 and shift == 0:
            if ch in "<":
                continue
            if ch == ">":
                special = "frac"
                continue
            if ch == ":":
                special = "pen"
                continue
            if ch == "=":
                absolute = True
                continue
            if ch == "7":
                base32 = True
                continue
            if ch == "8":
                base32 = False
                continue
        bits = 5 if base32 else 6
        if base32:
            terminal = 95 <= c <= 126
            value = c - 95 if terminal else c - 63
            ok = 63 <= c <= 126
        else:
            terminal = 191 <= c <= 254
            value = c - 191 if terminal else c - 63
            ok = 63 <= c <= 126 or terminal
        if not ok:
            continue
        acc |= value << shift
        shift += bits
        if not terminal:
            continue
        number = -(acc >> 1) if acc & 1 else acc >> 1
        acc = shift = 0
        if special == "frac":
            frac = number
            special = None
            continue
        if special == "pen":
            special = None
            continue
        pending.append(number / (2**frac) if frac > 0 else float(number))
        if len(pending) == 2:
            dx, dy = pending
            pending = []
            x, y = (dx, dy) if absolute else (x + dx, y + dy)
            absolute = False
            points.append((x, y))
    return points, x, y


class _Extent:
    """Upper-right corner of what a plot draws, in plotter units (follows IP / SC / IR)."""

    def __init__(self) -> None:
        self.default_p2 = (DEFAULT_FRAME_IN[0] * PLU_PER_INCH, DEFAULT_FRAME_IN[1] * PLU_PER_INCH)
        self.reset()
        self.max_x = self.max_y = 0.0
        self.seen = False

    def reset(self) -> None:
        self.x = self.y = 0.0
        self.absolute = True
        self.p1 = (0.0, 0.0)
        self.p2 = self.default_p2
        self.sc: tuple[float, float, float, float, int] | None = None

    def add(self, ux: float, uy: float) -> None:
        """Include the point ``(ux, uy)`` (in the file's current coordinate system)."""
        x, y = ux, uy
        if self.sc is not None:
            xmin, xmax, ymin, ymax, kind = self.sc
            if kind == 2:  # point-factor scaling: the IP box is the best bound there is
                x, y = self.p2
            else:
                sx = (self.p2[0] - self.p1[0]) / ((xmax - xmin) or 1.0)
                sy = (self.p2[1] - self.p1[1]) / ((ymax - ymin) or 1.0)
                x, y = self.p1[0] + (ux - xmin) * sx, self.p1[1] + (uy - ymin) * sy
        self.max_x, self.max_y, self.seen = max(self.max_x, x), max(self.max_y, y), True

    def move(self, nx: float, ny: float, *, relative: bool) -> None:
        self.x, self.y = (self.x + nx, self.y + ny) if relative else (nx, ny)
        self.add(self.x, self.y)

    def corner(self, nx: float, ny: float, *, relative: bool) -> tuple[float, float]:
        return (self.x + nx, self.y + ny) if relative else (nx, ny)

    def command(self, op: str, arg: str) -> None:
        nums = _numbers(arg) if op != "PE" else []
        if op == "IN":
            self.reset()
        elif op in ("IP", "IR"):
            self._ip(op, nums)
        elif op == "SC":
            if len(nums) >= 4:
                kind = int(nums[4]) if len(nums) > 4 else 0
                self.sc = (nums[0], nums[1], nums[2], nums[3], kind)
            else:
                self.sc = None
        elif op in ("PA", "PR"):
            self.absolute = op == "PA"  # PU / PD keep the mode set by the last PA / PR
            self._pairs(nums)
        elif op in ("PU", "PD"):
            self._pairs(nums)
        elif op == "PE":
            points, self.x, self.y = decode_pe(arg, self.x, self.y)
            for px, py in points:
                self.add(px, py)
        elif op in ("CI", "WG", "EW") and nums:
            r = abs(nums[0])
            self.add(self.x + r, self.y + r)
        elif op in ("AA", "AR") and len(nums) >= 3:
            cx, cy = self.corner(nums[0], nums[1], relative=op == "AR")
            r = math.hypot(self.x - cx, self.y - cy)
            self.add(cx + r, cy + r)
            start = math.atan2(self.y - cy, self.x - cx)
            end = start + math.radians(nums[2])
            self.x, self.y = cx + r * math.cos(end), cy + r * math.sin(end)
        elif op in ("EA", "ER", "RA", "RR") and len(nums) >= 2:
            self.add(*self.corner(nums[0], nums[1], relative=op in ("ER", "RR")))
        elif op in ("AT", "RT") and len(nums) >= 4:
            rel = op == "RT"
            self.add(*self.corner(nums[0], nums[1], relative=rel))
            self.move(nums[2], nums[3], relative=rel)

    def _pairs(self, nums: list[float]) -> None:
        for i in range(0, len(nums) - 1, 2):
            self.move(nums[i], nums[i + 1], relative=not self.absolute)

    def _ip(self, op: str, nums: list[float]) -> None:
        if op == "IR":  # percentages of the (default) frame
            nums = [v * self.default_p2[i % 2] / 100 for i, v in enumerate(nums)]
        if len(nums) >= 4:
            self.p1, self.p2 = (nums[0], nums[1]), (nums[2], nums[3])
        elif len(nums) >= 2:
            w, h = self.p2[0] - self.p1[0], self.p2[1] - self.p1[1]
            self.p1 = (nums[0], nums[1])
            self.p2 = (nums[0] + w, nums[1] + h)
        else:
            self.p1, self.p2 = (0.0, 0.0), self.default_p2


def plot_extent_in(text: str) -> tuple[float, float] | None:
    """Upper-right corner of what the plot draws, in inches (``None`` if it draws nothing).

    Follows PA / PR / PU / PD / PE / CI / AA / AR / EA / ER / RA / RR / AT / RT / WG / EW and the
    IP / IR / SC scaling the Inkscape extension applies (user units are mapped onto the IP box).
    """
    ext = _Extent()
    for m in _COMMAND.finditer(text):
        ext.command(m.group(1).upper(), m.group(2))
    if not ext.seen:
        return None
    return ext.max_x / PLU_PER_INCH, ext.max_y / PLU_PER_INCH


def frame_inches(text: str, max_page_mm: float) -> tuple[float, float]:
    """Picture frame (width, height) in inches for the extension."""
    extent = plot_extent_in(text)
    if extent is None:
        return DEFAULT_FRAME_IN
    w = max(extent[0] + _PAD_IN, 1.0)
    h = max(extent[1] + _PAD_IN, 1.0)
    if max(w, h) * 25.4 > max_page_mm or max(w, h) > MAX_FRAME_IN:
        raise DomainError(
            ErrorCode.PAGE_TOO_LARGE,
            f"the plot is {w * 25.4:.0f} x {h * 25.4:.0f} mm, the limit is {max_page_mm:.0f} mm",
            params={"limit_mm": max_page_mm},
        )
    return math.ceil(round(w * 100, 6)) / 100, math.ceil(round(h * 100, 6)) / 100


def strip_labels(text: str) -> str:
    """Remove text labels (``LB…<ETX>``) and device escape sequences that the parser rejects."""
    return _LABEL.sub("", _ESCAPE_SEQ.sub("", text))


class HpglConverter:
    id = "hpgl"
    handles = frozenset({DocFormat.HPGL})

    def available(self) -> bool:
        return tool_available("inkscape")

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        scratch = ctx.scratch_dir(work)
        env = tool_env(ctx, scratch)
        staged = stage_input(src, scratch, "input.hpgl")  # Inkscape keys the importer on .hpgl
        out_dir = fresh_dir(scratch / "hpgl-out")
        pdf = out_dir / "plot.pdf"
        ctx.report("convert", 5, "ingest.convert", tool="inkscape")
        script = EXTENSION_DIR / EXTENSION_SCRIPT
        if script.is_file() and Path(SYSTEM_PYTHON).is_file():
            self._via_extension(staged, script, out_dir, pdf, scratch, ctx, env)
        else:
            self._inkscape_direct(staged, pdf, scratch, ctx, env)
        canon = pdf_to_canonical(pdf, work, ctx, DocFormat.HPGL)
        if _LABEL.search(staged.read_text(encoding="latin-1")):
            # Inkscape's importer does not draw text labels (LB): the plot comes out without them.
            canon.warnings.append(
                PlanWarning(
                    code=WarningCode.LOW_FIDELITY_FORMAT,
                    params={"format": "hpgl", "reason": "labels_dropped"},
                )
            )
        return canon

    @staticmethod
    def _extension_args(
        script: Path, staged: Path, svg: Path, frame: tuple[float, float]
    ) -> list[str | Path]:
        return [
            SYSTEM_PYTHON,
            script,
            f"--width={frame[0]}",
            f"--height={frame[1]}",
            f"--output={svg}",
            staged,
        ]

    def _via_extension(
        self,
        staged: Path,
        script: Path,
        out_dir: Path,
        pdf: Path,
        scratch: Path,
        ctx: ConvertContext,
        env: dict[str, str],
    ) -> None:
        """HPGL -> SVG (extension) -> PDF. A file the parser rejects is retried without labels."""
        text = staged.read_text(encoding="latin-1")
        frame = frame_inches(text, float(ctx.config.max_page_mm))
        svg = out_dir / "plot.svg"
        # The extension needs its own interpreter, not the app's virtualenv.
        run_env = {**env, "PATH": "/usr/bin:/bin", "PYTHONPATH": ""}
        try:
            self._run_extension(staged, script, svg, frame, scratch, ctx, run_env)
        except DomainError as exc:
            if exc.code is ErrorCode.CONVERSION_TIMEOUT:
                raise
            clean = strip_labels(text)
            if clean == text:
                raise DomainError(
                    ErrorCode.CORRUPT_FILE, f"cannot read the HPGL file: {exc.detail}"
                ) from exc
            fixed = staged.with_name("input-clean.hpgl")
            fixed.write_text(clean, encoding="latin-1")
            self._run_extension(fixed, script, svg, frame, scratch, ctx, run_env)
        run_tool(
            ["inkscape", svg, "--export-type=pdf", f"--export-filename={pdf}"],
            scratch=scratch,
            timeout_s=ctx.config.job_timeout_s,
            memory_mb=ctx.config.job_memory_mb,
            env=env,
        )

    def _run_extension(
        self,
        staged: Path,
        script: Path,
        svg: Path,
        frame: tuple[float, float],
        scratch: Path,
        ctx: ConvertContext,
        env: dict[str, str],
    ) -> None:
        svg.unlink(missing_ok=True)
        run_tool(
            self._extension_args(script, staged, svg, frame),
            scratch=scratch,
            cwd=script.parent,
            timeout_s=ctx.config.job_timeout_s,
            memory_mb=ctx.config.job_memory_mb,
            env=env,
        )
        if not svg.is_file() or svg.stat().st_size == 0:
            raise DomainError(ErrorCode.CONVERSION_FAILED, "the HPGL import produced no drawing")

    @staticmethod
    def _inkscape_direct(
        staged: Path, pdf: Path, scratch: Path, ctx: ConvertContext, env: dict[str, str]
    ) -> None:
        run_tool(
            ["inkscape", staged, "--export-type=pdf", f"--export-filename={pdf}"],
            scratch=scratch,
            timeout_s=ctx.config.job_timeout_s,
            memory_mb=ctx.config.job_memory_mb,
            env=env,
        )
