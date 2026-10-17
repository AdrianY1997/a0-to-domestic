"""Signature-based format detection (SPEC 6.1 ING-02, Appendix E).

Detection looks at content, never at the extension alone. The extension (``filename``) is only
used to confirm weak signatures (DXF ASCII, HPGL) and to tell Illustrator files from plain PDF.
"""

import re
import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest.models import DocFormat

_HEAD_BYTES = 8192
_SVG_WINDOW = 4096
_MAX_ZIP_ENTRIES = 100_000

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06")

_SVG_ROOT = re.compile(
    rb"\A\s*(?:<\?xml[^>]*\?>\s*)?(?:<!--.*?-->\s*)*"
    rb"(?:<!DOCTYPE[^>\[]*(?:\[.*?\])?\s*>\s*)?(?:<!--.*?-->\s*)*<svg[\s>/]",
    re.DOTALL | re.IGNORECASE,
)
_DXF_STRONG = re.compile(
    rb"\A(?:\s*999\s*\r?\n[^\r\n]*\r?\n)*\s*0\s*\r?\n\s*SECTION\s*\r?\n\s*2\s*\r?\n\s*"
    rb"(?:HEADER|CLASSES|TABLES|BLOCKS|ENTITIES|OBJECTS)\s*\r?\n"
)
_DXF_WEAK = re.compile(rb"\A(?:\s*999\s*\r?\n[^\r\n]*\r?\n)*\s*0\s*\r?\n\s*SECTION\s*\r?\n")
_HPGL_START = re.compile(
    rb"\A(?:\x1b[^;\n]{0,32})?\s*(?:IN\s*;|IN\s*[A-Z]|SP\s*\d*\s*;|PU|PD|PA|DF\s*;)"
)
_HPGL_STRONG = re.compile(rb"\A\s*IN\s*;.{0,200}?(?:SP|PU|PD)", re.DOTALL)
_HPGL_EXTS = frozenset({"plt", "hpgl", "hpg"})

_BMP_DIB_SIZES = frozenset({12, 40, 52, 56, 64, 108, 124})
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heim", b"heis"})
_AVIF_BRANDS = frozenset({b"avif", b"avis"})

_ODF_KINDS = {
    "text": DocFormat.ODT,
    "spreadsheet": DocFormat.ODS,
    "presentation": DocFormat.ODP,
    "graphics": DocFormat.ODG,
}
_OOXML_ROOTS = (
    ("word/", DocFormat.DOCX),
    ("xl/", DocFormat.XLSX),
    ("ppt/", DocFormat.PPTX),
    ("visio/", DocFormat.VSDX),
)
_OLE_STREAMS = (
    ("worddocument", DocFormat.DOC),
    ("workbook", DocFormat.XLS),
    ("book", DocFormat.XLS),
    ("powerpoint document", DocFormat.PPT),
    ("visiodocument", DocFormat.VSD),
    ("quill", DocFormat.PUB),
)


@dataclass(frozen=True, slots=True)
class Detection:
    """Outcome of :func:`detect`. ``format`` is ``None`` when the file is not supported."""

    format: DocFormat | None
    reason: str | None = None


def supported_format_ids() -> list[str]:
    return sorted(f.value for f in DocFormat)


def _ext(filename: str | None) -> str:
    if not filename:
        return ""
    return Path(filename).suffix.lower().lstrip(".")


def detect_format(path: Path, filename: str | None = None) -> DocFormat | None:
    """The detected format id, or ``None`` if unsupported."""
    return detect(path, filename).format


def detect_or_raise(path: Path, filename: str | None = None) -> DocFormat:
    """Like :func:`detect_format` but raises ``UNSUPPORTED_FORMAT`` listing the known formats."""
    det = detect(path, filename)
    if det.format is None:
        raise DomainError(
            ErrorCode.UNSUPPORTED_FORMAT,
            det.reason or "unrecognized file content",
            params={"supported": supported_format_ids()},
        )
    return det.format


def detect(path: Path, filename: str | None = None) -> Detection:
    """Identify ``path`` by its signature (Appendix E)."""
    ext = _ext(filename if filename is not None else path.name)
    try:
        with path.open("rb") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError as exc:
        return Detection(None, f"unreadable file: {exc.strerror or exc}")
    if not head:
        return Detection(None, "empty file")
    return _detect_head(path, head, ext)


def _detect_head(path: Path, head: bytes, ext: str) -> Detection:
    # Binary signatures with no ambiguity first.
    if b"%PDF-" in head[:1024]:
        return Detection(DocFormat.AI if ext == "ai" else DocFormat.PDF)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Detection(DocFormat.PNG)
    if head.startswith(b"\xff\xd8\xff"):
        return Detection(DocFormat.JPEG)
    if head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return Detection(DocFormat.TIFF)
    if head.startswith((b"GIF87a", b"GIF89a")):
        return Detection(DocFormat.GIF)
    if head.startswith(b"\x00\x00\x00\x0cjP  ") or head.startswith(b"\xff\x4f\xff\x51"):
        return Detection(DocFormat.JP2)
    if head.startswith(b"AT&TFORM"):
        return Detection(DocFormat.DJVU)
    if head.startswith(_OLE2_MAGIC):
        return _detect_ole2(path)
    if head.startswith(b"{\\rtf"):
        return Detection(DocFormat.RTF)
    if head.startswith(b"\xd7\xcd\xc6\x9a") or head[:4] in (
        b"\x01\x00\x09\x00",
        b"\x02\x00\x09\x00",
    ):
        return Detection(DocFormat.WMF)
    if head[:4] == b"\x01\x00\x00\x00" and head[40:44] == b" EMF":
        return Detection(DocFormat.EMF)
    if head.startswith(b"RIFF") and len(head) >= 12:
        if head[8:12] == b"WEBP":
            return Detection(DocFormat.WEBP)
        if head[8:11] == b"CDR":
            return Detection(DocFormat.CDR)
    if re.match(rb"AC10\d\d", head):
        return Detection(DocFormat.DWG)
    if head.startswith(b"AutoCAD Binary DXF"):
        return Detection(DocFormat.DXF)
    if head.startswith(b"BM") and _looks_like_bmp(head):
        return Detection(DocFormat.BMP)
    if head[4:8] == b"ftyp":
        fmt = _detect_ftyp(head)
        if fmt is not None:
            return Detection(fmt)
    if head.startswith(b"\xc5\xd0\xd3\xc6"):
        return Detection(DocFormat.EPS)
    if head.startswith(b"%!"):
        return Detection(_postscript_kind(head))
    if head.startswith(_ZIP_MAGICS):
        return _detect_zip(path)
    if head.startswith(b"\x1f\x8b"):
        return _detect_gzip(path)

    # Text formats (weak signatures).
    if _is_svg(head):
        return Detection(DocFormat.SVG)
    if _DXF_STRONG.match(head) or (ext == "dxf" and _DXF_WEAK.match(head)):
        return Detection(DocFormat.DXF)
    if ext in _HPGL_EXTS and _HPGL_START.match(head):
        return Detection(DocFormat.HPGL)
    if _HPGL_STRONG.match(head):
        return Detection(DocFormat.HPGL)
    return Detection(None, "unrecognized file content")


def _looks_like_bmp(head: bytes) -> bool:
    if len(head) < 18:
        return False
    reserved = struct.unpack_from("<I", head, 6)[0]
    dib = struct.unpack_from("<I", head, 14)[0]
    return reserved == 0 and dib in _BMP_DIB_SIZES


def _detect_ftyp(head: bytes) -> DocFormat | None:
    box_size = struct.unpack_from(">I", head, 0)[0]
    end = min(len(head), box_size if box_size >= 16 else 32)
    brands = {head[8:12]}
    brands.update(head[i : i + 4] for i in range(16, end - 3, 4))
    if brands & _AVIF_BRANDS:
        return DocFormat.AVIF
    if brands & _HEIF_BRANDS:
        return DocFormat.HEIF
    return None


def _postscript_kind(head: bytes) -> DocFormat:
    first_line = head.split(b"\n", 1)[0].split(b"\r", 1)[0]
    if re.search(rb"%%Creator:\s*Adobe Illustrator", head[:4096]):
        return DocFormat.AI
    if b"EPSF" in first_line:
        return DocFormat.EPS
    return DocFormat.PS


def _decode_prolog(head: bytes) -> bytes:
    if head.startswith(b"\xef\xbb\xbf"):
        return head[3:]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return head.decode("utf-16").encode("utf-8")
        except UnicodeError:
            return head
    return head


def _is_svg(head: bytes) -> bool:
    return _SVG_ROOT.match(_decode_prolog(head)[:_SVG_WINDOW]) is not None


def _detect_gzip(path: Path) -> Detection:
    try:
        with path.open("rb") as fh:
            blob = fh.read(65536)
        out = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(blob, _SVG_WINDOW)
    except (OSError, zlib.error):
        return Detection(None, "invalid gzip stream")
    if _is_svg(out):
        return Detection(DocFormat.SVGZ)
    return Detection(None, "gzip file that is not SVGZ")


def _detect_zip(path: Path) -> Detection:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ZIP_ENTRIES:
                return Detection(None, "ZIP archive with too many entries")
            lower = {i.filename.lower() for i in infos}
            if infos and infos[0].filename == "mimetype":
                with zf.open(infos[0]) as fh:
                    mime = fh.read(256).decode("ascii", "replace").strip()
                return _odf_from_mime(mime)
            if any(n.endswith(".fdseq") or "fixeddocumentsequence" in n for n in lower):
                return Detection(DocFormat.XPS)
            if "_rels/.rels" in lower:
                with zf.open("_rels/.rels") as fh:
                    rels = fh.read(65536).lower()
                if b"fixedrepresentation" in rels:
                    return Detection(DocFormat.XPS)
            if "content/riffdata.cdr" in lower:
                return Detection(DocFormat.CDR)
            if "[content_types].xml" in lower:
                if any(n.endswith("vbaproject.bin") for n in lower):
                    return Detection(None, "macro-enabled Office document")
                for prefix, fmt in _OOXML_ROOTS:
                    if any(n.startswith(prefix) for n in lower):
                        return Detection(fmt)
    except (zipfile.BadZipFile, OSError, NotImplementedError, ValueError):
        return Detection(None, "corrupt ZIP archive")
    return Detection(None, "ZIP archive with unrecognized content")


def _odf_from_mime(mime: str) -> Detection:
    prefix = "application/vnd.oasis.opendocument."
    if not mime.startswith(prefix):
        return Detection(None, "ZIP with unknown mimetype entry")
    kind = mime[len(prefix) :].removesuffix("-template")
    fmt = _ODF_KINDS.get(kind)
    return Detection(fmt) if fmt else Detection(None, f"unsupported ODF type: {kind}")


# --- OLE2 / compound file: read only the directory to tell doc/xls/ppt/vsd/pub apart ---------


def _ole2_stream_names(path: Path, limit: int = 4096) -> set[str]:
    """Names of the directory entries of a compound file (lower case). Best effort."""
    names: set[str] = set()
    with path.open("rb") as fh:
        header = fh.read(512)
        if len(header) < 512:
            return names
        shift = struct.unpack_from("<H", header, 30)[0]
        if shift not in (9, 12):
            return names
        sector = 1 << shift
        n_fat = struct.unpack_from("<I", header, 44)[0]
        dir_start = struct.unpack_from("<I", header, 48)[0]
        difat_start, n_difat = struct.unpack_from("<II", header, 68)
        difat = list(struct.unpack_from("<109I", header, 76))

        def read_sector(sid: int) -> bytes:
            fh.seek((sid + 1) * sector)
            return fh.read(sector)

        seen: set[int] = set()
        sid = difat_start
        for _ in range(min(n_difat, 1024)):
            if sid >= 0xFFFFFFFA or sid in seen:
                break
            seen.add(sid)
            raw = read_sector(sid)
            if len(raw) < sector:
                break
            vals = struct.unpack(f"<{sector // 4}I", raw)
            difat.extend(vals[:-1])
            sid = vals[-1]
        fat: list[int] = []
        for fat_sid in [s for s in difat if s < 0xFFFFFFFA][:n_fat]:
            raw = read_sector(fat_sid)
            if len(raw) < sector:
                break
            fat.extend(struct.unpack(f"<{sector // 4}I", raw))
        sid = dir_start
        visited: set[int] = set()
        while sid < 0xFFFFFFFA and sid not in visited and len(visited) < limit:
            visited.add(sid)
            raw = read_sector(sid)
            if len(raw) < sector:
                break
            for off in range(0, sector, 128):
                entry = raw[off : off + 128]
                nlen = struct.unpack_from("<H", entry, 64)[0]
                if entry[66] in (1, 2, 5) and 2 <= nlen <= 64:
                    names.add(entry[: nlen - 2].decode("utf-16le", "replace").lower())
            sid = fat[sid] if sid < len(fat) else 0xFFFFFFFE
    return names


def _detect_ole2(path: Path) -> Detection:
    try:
        names = _ole2_stream_names(path)
    except (OSError, struct.error):
        return Detection(None, "corrupt OLE2 file")
    if "encryptedpackage" in names:
        return Detection(None, "encrypted Office document")
    for stream, fmt in _OLE_STREAMS:
        if stream in names:
            return Detection(fmt)
    return Detection(None, "OLE2 file of unknown type")
