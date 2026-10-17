"""Format detection (SPEC Appendix E)."""

import gzip
import io
import json
import struct
import zipfile
from pathlib import Path

import pytest

from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest import DocFormat, detect, detect_format, detect_or_raise

# Fixture manifest ids that differ from the ids detection returns (reported, not edited).
MANIFEST_ALIASES: dict[str, DocFormat | None] = {"docm": None}


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _zip(entries: list[tuple[str, bytes]], *, stored_first: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(
        buf, "w", zipfile.ZIP_STORED if stored_first else zipfile.ZIP_DEFLATED
    ) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def _cfb(stream_names: list[str]) -> bytes:
    """Minimal OLE2 compound file (v3) whose directory lists ``stream_names`` (<= 3)."""
    free, end, fat_sect = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", header, 44, 1)  # one FAT sector
    struct.pack_into("<I", header, 48, 1)  # directory starts at sector 1
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, end)
    struct.pack_into("<I", header, 68, end)
    struct.pack_into("<109I", header, 76, 0, *([free] * 108))
    fat = struct.pack("<128I", fat_sect, end, *([free] * 126))
    entries = b""
    for i, name in enumerate(["Root Entry", *stream_names]):
        raw = name.encode("utf-16le")
        entry = bytearray(128)
        entry[: len(raw)] = raw
        struct.pack_into("<H", entry, 64, len(raw) + 2)
        entry[66] = 5 if i == 0 else 2
        struct.pack_into("<III", entry, 68, free, free, free)
        entries += bytes(entry)
    entries = entries.ljust(512, b"\x00")
    return bytes(header) + fat + entries


def _heif(brand: bytes, compat: bytes = b"") -> bytes:
    body = brand + b"\x00\x00\x00\x00" + compat
    return struct.pack(">I", 8 + len(body)) + b"ftyp" + body + b"\x00" * 64


SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="10mm"/>'

CASES: list[tuple[str, str, bytes, DocFormat]] = [
    ("a.bin", "pdf", b"junk\n%PDF-1.7\n", DocFormat.PDF),
    ("a.bin", "png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, DocFormat.PNG),
    ("a.bin", "jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 20, DocFormat.JPEG),
    ("a.bin", "tiff-le", b"II*\x00" + b"\x00" * 20, DocFormat.TIFF),
    ("a.bin", "tiff-be", b"MM\x00*" + b"\x00" * 20, DocFormat.TIFF),
    ("a.bin", "bigtiff", b"II+\x00" + b"\x00" * 20, DocFormat.TIFF),
    ("a.bin", "gif87", b"GIF87a" + b"\x00" * 20, DocFormat.GIF),
    ("a.bin", "gif89", b"GIF89a" + b"\x00" * 20, DocFormat.GIF),
    ("a.bin", "bmp", b"BM" + struct.pack("<IIII", 100, 0, 54, 40) + b"\x00" * 40, DocFormat.BMP),
    ("a.bin", "webp", b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8, DocFormat.WEBP),
    ("a.bin", "heic", _heif(b"heic"), DocFormat.HEIF),
    ("a.bin", "mif1", _heif(b"mif1", b"heic"), DocFormat.HEIF),
    ("a.bin", "avif", _heif(b"avif"), DocFormat.AVIF),
    ("a.bin", "avif-compat", _heif(b"mif1", b"avif"), DocFormat.AVIF),
    ("a.bin", "jp2", b"\x00\x00\x00\x0cjP  \r\n\x87\n" + b"\x00" * 20, DocFormat.JP2),
    ("a.bin", "j2k", b"\xff\x4f\xff\x51" + b"\x00" * 20, DocFormat.JP2),
    ("a.bin", "ps", b"%!PS-Adobe-3.0\n%%Pages: 1\n", DocFormat.PS),
    ("a.bin", "eps", b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n", DocFormat.EPS),
    ("a.bin", "eps-dos", b"\xc5\xd0\xd3\xc6" + b"\x00" * 28, DocFormat.EPS),
    ("a.bin", "ai-ps", b"%!PS-Adobe-3.0\n%%Creator: Adobe Illustrator(R) 8.0\n", DocFormat.AI),
    ("a.ai", "ai-pdf", b"%PDF-1.5\n", DocFormat.AI),
    ("a.bin", "svg", SVG, DocFormat.SVG),
    ("a.bin", "svg-doctype", b'<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="x"/>', DocFormat.SVG),
    ("a.bin", "svg-bom", b"\xef\xbb\xbf" + SVG, DocFormat.SVG),
    ("a.bin", "svgz", gzip.compress(SVG), DocFormat.SVGZ),
    ("a.bin", "rtf", b"{\\rtf1\\ansi hello}", DocFormat.RTF),
    ("a.bin", "emf", b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 20, DocFormat.EMF),
    ("a.bin", "wmf-placeable", b"\xd7\xcd\xc6\x9a" + b"\x00" * 20, DocFormat.WMF),
    ("a.bin", "wmf-hdr", b"\x01\x00\x09\x00" + b"\x00" * 20, DocFormat.WMF),
    ("a.bin", "dwg", b"AC1032\x00\x00\x00", DocFormat.DWG),
    ("a.bin", "dxf-bin", b"AutoCAD Binary DXF\r\n\x1a\x00", DocFormat.DXF),
    ("a.bin", "dxf-ascii", b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n  9\r\n", DocFormat.DXF),
    ("a.bin", "cdr-riff", b"RIFF\x10\x00\x00\x00CDRX" + b"\x00" * 8, DocFormat.CDR),
    ("a.bin", "djvu", b"AT&TFORM\x00\x00\x00\x10DJVU", DocFormat.DJVU),
    ("a.plt", "hpgl-ext", b"IN;SP1;PU0,0;PD100,100;", DocFormat.HPGL),
    ("a.bin", "hpgl-strong", b"IN;SP1;PU0,0;PD100,100;", DocFormat.HPGL),
    ("a.dxf", "dxf-weak-ext", b"0\nSECTION\n9\n", DocFormat.DXF),
]


@pytest.mark.parametrize(
    ("filename", "label", "data", "expected"), CASES, ids=[c[1] for c in CASES]
)
def test_signatures(
    tmp_path: Path, filename: str, label: str, data: bytes, expected: DocFormat
) -> None:
    assert detect_format(_write(tmp_path, filename, data)) == expected


ZIP_CASES: list[tuple[str, bytes, DocFormat]] = [
    (
        "docx",
        _zip([("[Content_Types].xml", b"<x/>"), ("word/document.xml", b"<x/>")]),
        DocFormat.DOCX,
    ),
    (
        "xlsx",
        _zip([("[Content_Types].xml", b"<x/>"), ("xl/workbook.xml", b"<x/>")]),
        DocFormat.XLSX,
    ),
    (
        "pptx",
        _zip([("[Content_Types].xml", b"<x/>"), ("ppt/presentation.xml", b"<x/>")]),
        DocFormat.PPTX,
    ),
    (
        "vsdx",
        _zip([("[Content_Types].xml", b"<x/>"), ("visio/document.xml", b"<x/>")]),
        DocFormat.VSDX,
    ),
    (
        "xps",
        _zip([("[Content_Types].xml", b"<x/>"), ("FixedDocumentSequence.fdseq", b"<x/>")]),
        DocFormat.XPS,
    ),
    (
        "oxps",
        _zip([("_rels/.rels", b'<R Type="x/fixedrepresentation" Target="a"/>'), ("a", b"x")]),
        DocFormat.XPS,
    ),
    ("cdr-zip", _zip([("content/riffData.cdr", b"RIFF")]), DocFormat.CDR),
]


@pytest.mark.parametrize(("label", "data", "expected"), ZIP_CASES, ids=[c[0] for c in ZIP_CASES])
def test_zip_containers(tmp_path: Path, label: str, data: bytes, expected: DocFormat) -> None:
    assert detect_format(_write(tmp_path, "a.zip", data)) == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("text", DocFormat.ODT),
        ("spreadsheet", DocFormat.ODS),
        ("presentation", DocFormat.ODP),
        ("graphics", DocFormat.ODG),
        ("text-template", DocFormat.ODT),
    ],
)
def test_odf(tmp_path: Path, kind: str, expected: DocFormat) -> None:
    mime = f"application/vnd.oasis.opendocument.{kind}".encode()
    data = _zip([("mimetype", mime), ("content.xml", b"<x/>")], stored_first=True)
    assert detect_format(_write(tmp_path, "a.zip", data)) == expected


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        ("WordDocument", DocFormat.DOC),
        ("Workbook", DocFormat.XLS),
        ("Book", DocFormat.XLS),
        ("PowerPoint Document", DocFormat.PPT),
        ("VisioDocument", DocFormat.VSD),
        ("Quill", DocFormat.PUB),
    ],
)
def test_ole2(tmp_path: Path, stream: str, expected: DocFormat) -> None:
    assert (
        detect_format(_write(tmp_path, "a.bin", _cfb([stream, "\x05SummaryInformation"])))
        == expected
    )


def test_ole2_unknown_and_encrypted(tmp_path: Path) -> None:
    assert detect(_write(tmp_path, "a.bin", _cfb(["Foo"]))).format is None
    enc = detect(_write(tmp_path, "b.bin", _cfb(["EncryptedPackage"])))
    assert enc.format is None
    assert "encrypted" in (enc.reason or "")


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("a.dxf", b"hello world"),
        ("a.txt", b"0\nSECTION\n"),  # weak DXF signature without the extension
        ("a.txt", b"IN;"),  # weak HPGL
        ("a.txt", b"BM is not a bitmap"),
        ("a.txt", b"plain text"),
        ("a.html", b"<html><body><svg/></body></html>"),
        ("a.zip", _zip([("readme.txt", b"hi")])),
        ("a.gz", gzip.compress(b"not an svg")),
        ("a.bin", b""),
        ("a.bin", b"PK\x03\x04 broken zip"),
    ],
)
def test_unsupported(tmp_path: Path, filename: str, data: bytes) -> None:
    assert detect_format(_write(tmp_path, filename, data)) is None


def test_extension_never_decides(tmp_path: Path) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    assert detect_format(_write(tmp_path, "totally.pdf", png)) == DocFormat.PNG
    assert detect_format(_write(tmp_path, "x.png", b"%PDF-1.4\n")) == DocFormat.PDF
    # the explicit filename hint is used instead of the stored name
    assert (
        detect_format(_write(tmp_path, "original.bin", b"0\nSECTION\n"), "drawing.dxf")
        == DocFormat.DXF
    )


def test_macro_document_is_rejected(tmp_path: Path) -> None:
    data = _zip(
        [
            ("[Content_Types].xml", b"<x/>"),
            ("word/document.xml", b"<x/>"),
            ("word/vbaProject.bin", b"x"),
        ]
    )
    det = detect(_write(tmp_path, "a.docm", data))
    assert det.format is None
    assert "macro" in (det.reason or "")


def test_detect_or_raise_lists_formats(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as exc:
        detect_or_raise(_write(tmp_path, "a.txt", b"nothing"))
    assert exc.value.code is ErrorCode.UNSUPPORTED_FORMAT
    assert "pdf" in exc.value.params["supported"]
    assert set(exc.value.params["supported"]) == {f.value for f in DocFormat}


def test_missing_file(tmp_path: Path) -> None:
    assert detect(tmp_path / "nope.pdf").format is None


def test_format_ids_cover_appendix_e() -> None:
    expected = (
        "pdf png jpeg tiff gif bmp webp heif avif jp2 ps eps ai svg svgz docx xlsx pptx vsdx "
        "odt ods odp odg doc xls ppt vsd pub rtf emf wmf xps dwg dxf cdr hpgl djvu"
    )
    assert {f.value for f in DocFormat} == set(expected.split())


def test_fixtures_match_manifest(fixtures_dir: Path) -> None:
    manifest = json.loads((fixtures_dir / "manifest.json").read_text())
    checked = 0
    for rel, info in manifest.items():
        fid = info["format"]
        expected = MANIFEST_ALIASES[fid] if fid in MANIFEST_ALIASES else DocFormat(fid)
        assert detect_format(fixtures_dir / rel) == expected, rel
        checked += 1
    assert checked >= 20
