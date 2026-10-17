"""Strip remote / linked content from OOXML and ODF packages before LibreOffice opens them
(SPEC 14, "Red en conversiones"). Works on the staged copy, never on the upload.

* OOXML: every ``<Relationship TargetMode="External">`` in a ``.rels`` part is dropped except
  plain hyperlinks (never fetched). That covers linked images, OLE links, remote attached
  templates, frames and ``altChunk`` sources.
* ODF: ``xlink:href`` pointing at a URL / UNC path is blanked on every element but ``text:a`` /
  ``draw:a`` (images, objects, frames, section and table sources).
"""

import re
import shutil
import zipfile
from pathlib import Path

_REL = re.compile(r"<(?:\w+:)?Relationship\b[^>]*?/?>", re.S)
_EXTERNAL = re.compile(r'TargetMode\s*=\s*["\']External["\']', re.I)
_HYPERLINK = re.compile(r'Type\s*=\s*"[^"]*/relationships/hyperlink"')
_ODF_PARTS = re.compile(
    r"^(content|styles|meta|settings)\.xml$|^(Object [^/]*|Objects/[^/]*)/content\.xml$"
)
_ODF_TAG = re.compile(r"<([\w.-]+:[\w.-]+)\b[^>]*>", re.S)
_ODF_HREF = re.compile(
    r"""(xlink:href\s*=\s*)(["'])\s*(?:[a-zA-Z][a-zA-Z0-9+.-]*://|\\|//|file:)[^"']*\2"""
)
_ODF_KEEP = frozenset({"text:a", "draw:a"})
_MAX_PART = 64 * 1024 * 1024


def _strip_rels(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        tag = m.group(0)
        if _EXTERNAL.search(tag) and not _HYPERLINK.search(tag):
            return ""
        return tag

    return _REL.sub(repl, text)


def _strip_odf(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        tag = m.group(0)
        if m.group(1) in _ODF_KEEP:
            return tag
        return _ODF_HREF.sub(lambda h: f"{h.group(1)}{h.group(2)}{h.group(2)}", tag)

    return _ODF_TAG.sub(repl, text)


def _clean(name: str, data: bytes) -> bytes:
    if name.endswith(".rels"):
        fn = _strip_rels
    elif _ODF_PARTS.match(name):
        fn = _strip_odf
    else:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return fn(text).encode("utf-8")


def sanitize_package(path: Path) -> bool:
    """Rewrite ``path`` in place without remote references; ``False`` if it is not a zip (or is
    unreadable, in which case LibreOffice gets it untouched)."""
    if not zipfile.is_zipfile(path):
        return False
    tmp = path.with_name(path.name + ".clean")
    try:
        with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w") as zout:
            for info in zin.infolist():
                if info.is_dir():
                    continue
                if info.file_size > _MAX_PART:
                    raise OSError("part too large")
                data = _clean(info.filename, zin.read(info))
                ctype = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
                zout.writestr(zipfile.ZipInfo(info.filename, info.date_time), data, ctype)
        shutil.move(tmp, path)
    except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError):
        tmp.unlink(missing_ok=True)
        return False
    return True
