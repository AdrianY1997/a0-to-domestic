"""Helpers shared by the external-tool converters (SPEC 7.2, 14)."""

import shutil
from pathlib import Path

from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest.canonical import canonicalize_pdf
from a0td.ingest.converters.base import ConvertContext
from a0td.ingest.models import CanonicalDocument, DocFormat

FONTCONFIG_TEMPLATE = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>{fonts}</dir>
  <cachedir>{cache}</cachedir>
</fontconfig>
"""


def user_fonts_dir(ctx: ConvertContext) -> Path | None:
    """``<A0TD_DATA_DIR>/fonts`` when it exists and holds something (ING-11)."""
    path = ctx.config.data_dir / "fonts"
    try:
        if path.is_dir() and any(path.iterdir()):
            return path
    except OSError:
        return None
    return None


def tool_env(ctx: ConvertContext, scratch: Path) -> dict[str, str]:
    """Extra environment for LibreOffice / Inkscape / rsvg: a fontconfig file that adds the user
    fonts directory on top of the system configuration, and a cache inside the scratch dir."""
    cache = scratch / "fontcache"
    cache.mkdir(parents=True, exist_ok=True)
    env = {"XDG_CACHE_HOME": str(scratch / "cache")}
    fonts = user_fonts_dir(ctx)
    if fonts is not None:
        conf = scratch / "fonts.conf"
        conf.write_text(
            FONTCONFIG_TEMPLATE.format(fonts=fonts.resolve(), cache=cache), encoding="utf-8"
        )
        env["FONTCONFIG_FILE"] = str(conf)
    return env


def pdf_to_canonical(
    pdf: Path, work: Path, ctx: ConvertContext, source_format: DocFormat
) -> CanonicalDocument:
    """Canonicalize a PDF produced by a converter and restore the original format id."""
    if not pdf.is_file() or pdf.stat().st_size == 0:
        raise DomainError(ErrorCode.CONVERSION_FAILED, "the converter produced no output")
    canon = canonicalize_pdf(pdf, work, ctx)
    canon.source_format = source_format
    return canon


def stage_input(src: Path, scratch: Path, name: str) -> Path:
    """Copy ``src`` into ``scratch/in/<name>`` (clean, predictable name and extension)."""
    folder = scratch / "in"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    dst = folder / name
    shutil.copyfile(src, dst)
    return dst


def fresh_dir(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path
