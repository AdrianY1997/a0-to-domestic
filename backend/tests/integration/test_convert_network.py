"""No network from converters (SPEC 14): remote content stripped, network namespace when allowed."""

import http.server
import socket
import sys
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

from a0td.config import AppConfig
from a0td.ingest.converters import base
from a0td.ingest.converters.base import ConvertContext, run_tool, tool_available
from a0td.ingest.converters.office import OfficeConverter
from a0td.ingest.converters.office_sanitize import sanitize_package
from a0td.ingest.models import DocFormat

needs_lo = pytest.mark.skipif(not tool_available("soffice"), reason="LibreOffice not installed")

_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId1" Type="{_REL_NS}/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


class _Hits(http.server.BaseHTTPRequestHandler):
    hits: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        type(self).hits.append(self.path)
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[tuple[str, list[str]]]:
    _Hits.hits = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hits)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", _Hits.hits
    srv.shutdown()


def _docx(path: Path, url: str) -> Path:
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId9" Type="{_REL_NS}/image" Target="{url}/pic.png" '
        'TargetMode="External"/>'
        f'<Relationship Id="rId8" Type="{_REL_NS}/hyperlink" Target="https://example.org/" '
        'TargetMode="External"/>'
        "</Relationships>"
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        f'xmlns:r="{_REL_NS}" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body><w:p><w:r>'
        '<w:drawing><wp:inline><wp:extent cx="900000" cy="900000"/><wp:docPr id="1" name="x"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="x"/><pic:cNvPicPr/></pic:nvPicPr>'
        '<pic:blipFill><a:blip r:link="rId9"/></pic:blipFill><pic:spPr/></pic:pic>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p></w:body></w:document>"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", types)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("word/document.xml", body)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
    return path


def test_sanitize_drops_external_ooxml_relationships(tmp_path: Path) -> None:
    pkg = _docx(tmp_path / "a.docx", "http://example.invalid")
    assert sanitize_package(pkg)
    with zipfile.ZipFile(pkg) as zf:
        rels = zf.read("word/_rels/document.xml.rels").decode()
        assert zf.read("_rels/.rels").decode() == RELS
    assert "example.invalid" not in rels
    assert "https://example.org/" in rels  # plain hyperlinks are never fetched


def test_sanitize_blanks_odf_remote_references(tmp_path: Path) -> None:
    content = (
        '<office:document-content xmlns:office="o" xmlns:draw="d" xmlns:text="t" xmlns:xlink="x">'
        '<draw:image xlink:href="http://example.invalid/a.png" xlink:type="simple"/>'
        '<draw:image xlink:href="Pictures/local.png"/>'
        '<text:a xlink:href="http://example.org/">link</text:a></office:document-content>'
    )
    odt = tmp_path / "a.odt"
    with zipfile.ZipFile(odt, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text", zipfile.ZIP_STORED)
        zf.writestr("content.xml", content)
    assert sanitize_package(odt)
    with zipfile.ZipFile(odt) as zf:
        assert zf.namelist()[0] == "mimetype"
        out = zf.read("content.xml").decode()
    assert "example.invalid" not in out
    assert "Pictures/local.png" in out
    assert 'xlink:href="http://example.org/"' in out


def test_sanitize_ignores_non_zip(tmp_path: Path) -> None:
    f = tmp_path / "x.doc"
    f.write_bytes(b"\xd0\xcf\x11\xe0 not a zip")
    assert sanitize_package(f) is False


@needs_lo
def test_libreoffice_makes_no_request_for_a_linked_image(
    tmp_path: Path, server: tuple[str, list[str]]
) -> None:
    url, hits = server
    src = _docx(tmp_path / "linked.docx", url)
    cfg = AppConfig(data_dir=tmp_path / "data", tmp_dir=tmp_path / "tmp")
    ctx = ConvertContext(config=cfg, format=DocFormat.DOCX)
    OfficeConverter().convert(src, tmp_path / "work", ctx)
    assert hits == []


_CONNECT = (
    "import socket,sys\n"
    "s=socket.socket(); s.settimeout(3)\n"
    "try:\n    s.connect(('127.0.0.1', int(sys.argv[1]))); print('CONNECTED')\n"
    "except OSError as e:\n    print('BLOCKED', e)\n"
)


def test_wrapped_converter_cannot_open_a_tcp_connection(tmp_path: Path) -> None:
    base._network_isolation_prefix.cache_clear()
    if not base._network_isolation_prefix():
        pytest.skip("user namespaces denied by the runtime (default seccomp); wrapper inactive")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = str(listener.getsockname()[1])
    try:
        res = run_tool([sys.executable, "-c", _CONNECT, port], scratch=tmp_path)
        assert res.stdout.startswith(b"BLOCKED")
        free = run_tool([sys.executable, "-c", _CONNECT, port], scratch=tmp_path, no_network=False)
        assert free.stdout.startswith(b"CONNECTED")
    finally:
        listener.close()


def test_opt_out_disables_the_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A0TD_CONVERTER_NO_NETWORK", "false")
    base._network_isolation_prefix.cache_clear()
    try:
        assert base._network_isolation_prefix() == ()
    finally:
        monkeypatch.undo()
        base._network_isolation_prefix.cache_clear()
