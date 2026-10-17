"""Office / ODF / metafile converter: LibreOffice headless ``--convert-to pdf`` (SPEC 7.1).

Every job gets its own LibreOffice profile, seeded with the macros-disabled
``registrymodifications.xcu`` *before* the first launch (AGENTS.md > Docker). Macros therefore
never run, whatever the document asks for.
"""

import re
import zipfile
from pathlib import Path

from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.domain.plan import PlanWarning
from a0td.ingest.converters.base import ConvertContext, run_tool, tool_available
from a0td.ingest.converters.common import (
    fresh_dir,
    pdf_to_canonical,
    stage_input,
    tool_env,
)
from a0td.ingest.converters.office_sanitize import sanitize_package
from a0td.ingest.models import CanonicalDocument, DocFormat

XCU_TEMPLATE = Path("/usr/share/a0td/registrymodifications.xcu")

# Same settings as docker/libreoffice/registrymodifications.xcu, used outside the image.
_FALLBACK_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" \
xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop \
oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop \
oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop \
oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="ShowTipOfTheDay" \
oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" \
oor:op="fuse"><value>false</value></prop></item>
</oor:items>
"""

# Never refresh linked / remote content when opening a document (0 = always, 1 = ask, 2 = never).
# Appended to whichever template is used, so an older image template is hardened too.
_HARDENING_ITEMS = (
    '<item oor:path="/org.openoffice.Office.Writer/Content/Update">'
    '<prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>\n'
    '<item oor:path="/org.openoffice.Office.Calc/Content/Update">'
    '<prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>\n'
    '<item oor:path="/org.openoffice.Office.Common/Load">'
    '<prop oor:name="UpdateDocMode" oor:op="fuse"><value>0</value></prop></item>\n'
)

_LO_FORMATS = frozenset(
    {
        DocFormat.DOCX,
        DocFormat.XLSX,
        DocFormat.PPTX,
        DocFormat.VSDX,
        DocFormat.ODT,
        DocFormat.ODS,
        DocFormat.ODP,
        DocFormat.ODG,
        DocFormat.DOC,
        DocFormat.XLS,
        DocFormat.PPT,
        DocFormat.VSD,
        DocFormat.PUB,
        DocFormat.RTF,
        DocFormat.EMF,
        DocFormat.WMF,
        DocFormat.CDR,
    }
)
_METAFILES = frozenset({DocFormat.EMF, DocFormat.WMF})
# Formats Inkscape can read when LibreOffice cannot (EMF / WMF natively, CDR through libcdr).
_INKSCAPE_FALLBACK = _METAFILES | {DocFormat.CDR}
# Reverse-engineered formats: the result is usually close but not guaranteed (SPEC 7.1).
_LOW_FIDELITY = frozenset({DocFormat.CDR})

# Parts that declare the fonts a document actually uses (not the generic font tables).
_FONT_PARTS = re.compile(
    r"^(word/document\.xml|ppt/(slides/slide\d+|theme/theme\d+)\.xml|xl/styles\.xml"
    r"|content\.xml|styles\.xml)$"
)
_FONT_ATTR = re.compile(
    r'(?:w:ascii|w:hAnsi|style:font-name|fo:font-family|svg:font-family)="([^"]{1,80})"'
    r'|<a:latin typeface="([^"]{1,80})"'
    r'|<name val="([^"]{1,80})"'
)
_MAX_PART = 2_000_000
_MAX_FONTS = 24


def prepare_profile(profile: Path, template: Path = XCU_TEMPLATE) -> Path:
    """Create ``<profile>/user/registrymodifications.xcu`` (macros off); returns the profile."""
    user = profile / "user"
    user.mkdir(parents=True, exist_ok=True)
    target = user / "registrymodifications.xcu"
    closing = "</oor:items>"
    text = template.read_text(encoding="utf-8") if template.is_file() else _FALLBACK_XCU
    if closing not in text:
        text = _FALLBACK_XCU
    head, _, tail = text.rpartition(closing)
    target.write_text(head + _HARDENING_ITEMS + closing + tail, encoding="utf-8")
    return profile


def declared_fonts(src: Path) -> list[str]:
    """Font families named by the content of an OOXML / ODF file (best effort, may be empty)."""
    names: dict[str, None] = {}
    try:
        with zipfile.ZipFile(src) as zf:
            for info in zf.infolist():
                if not _FONT_PARTS.match(info.filename) or info.file_size > _MAX_PART:
                    continue
                text = zf.read(info).decode("utf-8", "replace")
                for m in _FONT_ATTR.finditer(text):
                    name = (m.group(1) or m.group(2) or m.group(3) or "").strip()
                    if name and not name.startswith("+") and name.lower() != "symbol":
                        names.setdefault(name, None)
                if len(names) >= _MAX_FONTS:
                    break
    except (zipfile.BadZipFile, OSError, KeyError, NotImplementedError, RuntimeError):
        return []
    return list(names)[:_MAX_FONTS]


def substituted_fonts(names: list[str], scratch: Path, env: dict[str, str]) -> list[str]:
    """Fonts from ``names`` that fontconfig does not resolve to themselves (nor to a
    metric-compatible alias such as Carlito for Calibri)."""
    if not names or not tool_available("fc-match"):
        return []
    out: list[str] = []
    for name in names:
        try:
            res = run_tool(
                ["fc-match", "-f", "%{family}", "--", name], scratch=scratch, timeout_s=10, env=env
            )
        except DomainError:
            return []
        got = {f.strip().lower() for f in res.stdout.decode("utf-8", "replace").split(",")}
        if name.lower() in got:
            continue
        low = name.lower()
        if any(low.startswith(k) and got & alt for k, alt in _COMPATIBLE.items()):
            continue
        out.append(name)
    return out


_COMPATIBLE: dict[str, set[str]] = {
    "calibri": {"carlito"},
    "cambria": {"caladea"},
    "arial": {"liberation sans"},
    "helvetica": {"liberation sans"},
    "times new roman": {"liberation serif"},
    "courier new": {"liberation mono"},
    "georgia": {"gelasio"},
}


class OfficeConverter:
    id = "office"
    handles = _LO_FORMATS

    def available(self) -> bool:
        return tool_available("soffice")

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        fmt = ctx.format or DocFormat.DOCX
        scratch = ctx.scratch_dir(work)
        env = tool_env(ctx, scratch)
        staged = stage_input(src, scratch, f"input.{fmt.value}")
        sanitize_package(staged)  # no remote / linked content reaches LibreOffice
        out_dir = fresh_dir(scratch / "lo-out")
        ctx.report("convert", 5, "ingest.convert", tool="libreoffice")
        pdf = out_dir / "input.pdf"
        try:
            self._libreoffice(staged, out_dir, scratch, ctx, env)
            if not pdf.is_file() and fmt in _INKSCAPE_FALLBACK:
                raise DomainError(ErrorCode.CONVERSION_FAILED, "LibreOffice produced no PDF")
        except DomainError as exc:
            if fmt not in _INKSCAPE_FALLBACK or not tool_available("inkscape"):
                raise
            if exc.code is ErrorCode.CONVERSION_TIMEOUT:
                raise
            pdf = self._inkscape(staged, out_dir, scratch, ctx, env)
        if not pdf.is_file():
            raise DomainError(ErrorCode.CONVERSION_FAILED, "LibreOffice produced no PDF")
        canon = pdf_to_canonical(pdf, work, ctx, fmt)
        if fmt in _LOW_FIDELITY:
            canon.warnings.append(
                PlanWarning(code=WarningCode.LOW_FIDELITY_FORMAT, params={"format": fmt.value})
            )
        if fmt not in _INKSCAPE_FALLBACK:
            fonts = substituted_fonts(declared_fonts(src), scratch, env)
            if fonts:
                canon.warnings.append(
                    PlanWarning(code=WarningCode.FONTS_SUBSTITUTED, params={"fonts": fonts})
                )
        return canon

    @staticmethod
    def _libreoffice(
        staged: Path, out_dir: Path, scratch: Path, ctx: ConvertContext, env: dict[str, str]
    ) -> None:
        profile = prepare_profile(fresh_dir(scratch / "lo-profile"))
        run_tool(
            [
                "soffice",
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--nodefault",
                "--nologo",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                out_dir,
                staged,
            ],
            scratch=scratch,
            timeout_s=ctx.config.job_timeout_s,
            memory_mb=ctx.config.job_memory_mb,
            env=env,
        )

    @staticmethod
    def _inkscape(
        staged: Path, out_dir: Path, scratch: Path, ctx: ConvertContext, env: dict[str, str]
    ) -> Path:
        pdf = out_dir / "inkscape.pdf"
        run_tool(
            ["inkscape", staged, "--export-type=pdf", f"--export-filename={pdf}"],
            scratch=scratch,
            timeout_s=ctx.config.job_timeout_s,
            memory_mb=ctx.config.job_memory_mb,
            env=env,
        )
        return pdf
