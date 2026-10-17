"""SVG / SVGZ converter: rsvg, Inkscape fallback, sanitizing, assumed size (SPEC 7.1, 14)."""

import gzip
import http.server
import threading
from pathlib import Path

import pymupdf
import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.ingest import IngestResult, SizeOverride, ingest_document, set_physical_size
from a0td.ingest.converters import svg as svg_mod
from a0td.ingest.converters.base import tool_available
from a0td.ingest.converters.svg import sanitize_svg
from convert_support import assert_not_blank, assert_size, convert, fixture_path

needs_svg = pytest.mark.skipif(
    not (tool_available("rsvg-convert") or tool_available("inkscape")), reason="no SVG renderer"
)


def _assumed(result: IngestResult) -> bool:
    return result.pages[0].has_warning(WarningCode.PHYSICAL_SIZE_ASSUMED)


# ---------------------------------------------------------------------------------- sanitizer


def test_sanitizer_strips_remote_references_and_active_content() -> None:
    dirty = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        b'onload="evil()" width="10mm" height="10mm">'
        b'<defs><linearGradient id="g"/></defs>'
        b"<style>@import url(http://evil.example/a.css); .a{fill:url(http://evil.example/x)}"
        b".b{fill:url(#g)}</style>"
        b"<script>alert(1)</script><foreignObject><div/></foreignObject>"
        b'<image xlink:href="http://127.0.0.1:9/e.png" width="5" height="5"/>'
        b'<image href="file:///etc/passwd" width="5" height="5"/>'
        b'<image href="data:image/png;base64,AAAA" width="5" height="5"/>'
        b'<use href="#g"/><rect style="fill:url(//evil/x)" fill="url(#g)"/></svg>'
    )
    clean, root = sanitize_svg(dirty)
    text = clean.decode()
    for needle in ("evil", "127.0.0.1", "/etc/passwd", "alert", "foreignObject", "onload"):
        assert needle not in text, needle
    assert "data:image/png" in text
    assert 'href="#g"' in text
    assert "url(#g)" in text
    assert root.tag.endswith("svg")


def test_sanitizer_accepts_the_plain_illustrator_doctype() -> None:
    svg = (
        b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1mm" height="1mm"/>'
    )
    clean, _ = sanitize_svg(svg)
    assert b"DOCTYPE" not in clean


def test_sanitizer_rejects_entities(fixtures_dir: Path) -> None:
    data = fixture_path(fixtures_dir, "malicious/svg_xxe.svg").read_bytes()
    with pytest.raises(DomainError) as exc:
        sanitize_svg(data)
    assert exc.value.code is ErrorCode.CORRUPT_FILE
    bomb = (
        b'<!DOCTYPE svg [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg">&b;</svg>'
    )
    with pytest.raises(DomainError):
        sanitize_svg(bomb)


@pytest.mark.parametrize("data", [b"<svg", b"<html/>", b"\x00\x01"])
def test_sanitizer_rejects_broken_documents(data: bytes) -> None:
    with pytest.raises(DomainError) as exc:
        sanitize_svg(data)
    assert exc.value.code is ErrorCode.CORRUPT_FILE


# ----------------------------------------------------------------------------------- converter


@needs_svg
def test_svg_with_physical_units(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    result, work = convert(fixtures_dir, "svg_physical.svg", tmp_path, cfg)
    assert result.format.value == "svg"
    assert result.kind == "pdf"
    assert len(result.pages) == 1
    assert_size(result, 200, 100, tol=0.5)
    assert not _assumed(result)
    assert_not_blank(work)


@needs_svg
def test_svg_without_physical_units_assumes_a0(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    result, work = convert(fixtures_dir, "svg_no_units.svg", tmp_path, cfg)
    assert _assumed(result)
    assert result.pages[0].warnings[0].params == {"paper": "A0"}
    assert_size(result, 1189, 594.5, tol=1.0)  # A0 landscape, the SVG's own orientation
    assert_not_blank(work)
    assert result.pages[0].to_geometry().physical_size_assumed


@needs_svg
def test_assumed_svg_size_can_be_set_by_the_user(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    _, work = convert(fixtures_dir, "svg_no_units.svg", tmp_path, cfg)

    result = set_physical_size(work, 0, width_mm=400, config=cfg)
    page = result.pages[0]
    assert (page.width_mm, page.height_mm) == (pytest.approx(400, abs=0.01), pytest.approx(200))
    assert not _assumed(result)
    with pymupdf.open(work / "canonical.pdf") as doc:
        assert doc[0].rect.width == pytest.approx(400 / 25.4 * 72, abs=0.1)
    assert IngestResult.load(work).pages[0].width_mm == pytest.approx(400, abs=0.01)
    assert_not_blank(work)

    page = set_physical_size(work, 0, dpi=96, config=cfg).pages[0]  # 200 CSS px at 96 dpi
    assert page.width_mm == pytest.approx(200 * 25.4 / 96, abs=0.01)

    page = set_physical_size(work, 0, paper="A4", config=cfg).pages[0]
    assert page.width_mm == pytest.approx(297, abs=0.01)
    assert page.height_mm == pytest.approx(148.5, abs=0.01)

    page = set_physical_size(work, 0, height_mm=100, config=cfg).pages[0]
    assert (page.width_mm, page.height_mm) == (
        pytest.approx(200, abs=0.05),
        pytest.approx(100, abs=0.05),
    )

    with pytest.raises(DomainError) as exc:
        set_physical_size(work, 0, width_mm=-1, config=cfg)
    assert exc.value.code is ErrorCode.INVALID_SETTINGS


@needs_svg
def test_size_override_applies_during_ingest(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    result = ingest_document(
        fixture_path(fixtures_dir, "svg_no_units.svg"),
        tmp_path / "doc",
        config=cfg,
        size_override=SizeOverride(width_mm=300),
    )
    assert result.pages[0].width_mm == pytest.approx(300, abs=0.01)
    assert not _assumed(result)


@needs_svg
def test_svg_with_a_plain_doctype_converts(tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "ai-export.svg"
    src.write_bytes(
        b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
        b'<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm">'
        b'<rect width="100" height="50" fill="#333"/></svg>'
    )
    result = ingest_document(src, tmp_path / "doc", config=cfg)
    assert_size(result, 100, 50, tol=0.5)


@needs_svg
def test_svgz_is_decompressed(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "plan.svgz"
    src.write_bytes(gzip.compress(fixture_path(fixtures_dir, "svg_physical.svg").read_bytes()))
    result = ingest_document(src, tmp_path / "doc", config=cfg)
    assert result.format.value == "svgz"
    assert_size(result, 200, 100, tol=0.5)


@needs_svg
def test_xxe_svg_is_rejected(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    with pytest.raises(DomainError) as exc:
        ingest_document(fixtures_dir / "malicious" / "svg_xxe.svg", tmp_path / "doc", config=cfg)
    assert exc.value.code is ErrorCode.CORRUPT_FILE


@needs_svg
def test_remote_and_file_hrefs_are_neutralized(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    result, work = convert(fixtures_dir, "malicious/svg_remote_href.svg", tmp_path, cfg)
    assert len(result.pages) == 1
    with pymupdf.open(work / "canonical.pdf") as doc:
        assert not doc[0].get_images()  # the images were dropped, not fetched


@needs_svg
def test_remote_references_are_never_fetched(tmp_path: Path, cfg: AppConfig) -> None:
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args: object) -> None:  # silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/probe.png"
        src = tmp_path / "probe.svg"
        src.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="50mm" height="50mm"><rect width="50" height="50" fill="#eee" '
            f'style="fill:url({url})"/>'
            f'<image xlink:href="{url}" width="10" height="10"/></svg>',
            encoding="utf-8",
        )
        ingest_document(src, tmp_path / "doc", config=cfg)
    finally:
        server.shutdown()
        server.server_close()
    assert hits == []


@pytest.mark.skipif(
    not (tool_available("rsvg-convert") and tool_available("inkscape")),
    reason="needs both rsvg-convert and Inkscape",
)
def test_inkscape_is_the_fallback_when_rsvg_fails(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = svg_mod.run_tool

    def fake(argv, **kw):  # type: ignore[no-untyped-def]
        calls.append(Path(str(argv[0])).name)
        if calls[-1] == "rsvg-convert":
            raise DomainError(ErrorCode.CONVERSION_FAILED, "rsvg failed")
        return real(argv, **kw)

    monkeypatch.setattr(svg_mod, "run_tool", fake)
    result, work = convert(fixtures_dir, "svg_physical.svg", tmp_path, cfg)
    assert calls == ["rsvg-convert", "inkscape"]
    assert_size(result, 200, 100, tol=0.5)
    assert_not_blank(work)


@needs_svg
def test_rsvg_is_used_by_default(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not tool_available("rsvg-convert"):
        pytest.skip("rsvg-convert not installed")
    calls: list[str] = []
    real = svg_mod.run_tool

    def spy(argv, **kw):  # type: ignore[no-untyped-def]
        calls.append(Path(str(argv[0])).name)
        return real(argv, **kw)

    monkeypatch.setattr(svg_mod, "run_tool", spy)
    convert(fixtures_dir, "svg_physical.svg", tmp_path, cfg)
    assert calls == ["rsvg-convert"]
