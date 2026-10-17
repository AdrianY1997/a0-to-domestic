"""SVG sanitizer hardening and px-size rules (WP-12 F2 audit)."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.ingest import ingest_document
from a0td.ingest.analyze import fit_to_a0_mm_per_px
from a0td.ingest.converters import svg as svg_mod
from a0td.ingest.converters.base import tool_available
from a0td.ingest.converters.svg import has_physical_size, sanitize_svg
from convert_support import assert_size

needs_svg = pytest.mark.skipif(
    not (tool_available("rsvg-convert") or tool_available("inkscape")), reason="no SVG renderer"
)

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'


def _svg(body: str, attrs: str = 'width="10mm" height="10mm"') -> bytes:
    return f"<svg {NS} {attrs}>{body}</svg>".encode()


# ------------------------------------------------------------------------------- CSS escapes


@pytest.mark.parametrize(
    "css",
    [
        r"@\69mport url(http://evil.example/a.css);",
        r".a{fill:u\72l(http://evil.example/x)}",
        r".a{fill:\75rl(http://evil.example/x)}",
    ],
)
def test_css_escapes_in_style_elements_are_dropped(css: str) -> None:
    clean, _ = sanitize_svg(_svg(f"<style>{css}</style><rect/>"))
    text = clean.decode()
    assert "evil" not in text
    assert "\\" not in text


def test_css_escape_in_a_style_attribute_is_dropped() -> None:
    clean, _ = sanitize_svg(
        _svg(r'<rect style="fill:u\72l(http://evil.example/x)" stroke="red" width="1"/>')
    )
    text = clean.decode()
    assert "evil" not in text
    assert "style" not in text
    assert 'stroke="red"' in text


def test_at_rules_are_dropped_wholesale() -> None:
    css = (
        "@import 'http://evil.example/a.css'; .keep{fill:red}"
        "@font-face{font-family:x;src:url(http://evil.example/f.woff)}"
        "@media print{.a{fill:url(http://evil.example/y)}} .also{fill:blue}"
        "@/**/import url(http://evil.example/z);"
    )
    clean, _ = sanitize_svg(_svg(f"<style>{css}</style>"))
    text = clean.decode()
    assert "evil" not in text
    assert "@" not in text
    assert ".keep{fill:red}" in text
    assert ".also{fill:blue}" in text


def test_css_comments_cannot_hide_a_url() -> None:
    clean, _ = sanitize_svg(_svg("<style>.a{fill:url(/**/http://evil.example/x)}</style>"))
    assert b"evil" not in clean


# ----------------------------------------------------------------------- robustness limits


def test_deeply_nested_svg_is_a_corrupt_file_not_a_crash() -> None:
    n = 1500
    data = _svg("<g>" * n + "<rect/>" + "</g>" * n)
    with pytest.raises(DomainError) as exc:
        sanitize_svg(data)
    assert exc.value.code is ErrorCode.CORRUPT_FILE
    assert "deep" in exc.value.detail


def test_moderate_nesting_is_accepted() -> None:
    n = 100
    clean, _ = sanitize_svg(_svg("<g>" * n + "<rect/>" + "</g>" * n))
    assert clean.count(b"<g>") == n


def test_data_uri_payload_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svg_mod, "_MAX_DATA_URI_BYTES", 100)
    one = "data:image/png;base64," + "A" * 60
    sanitize_svg(_svg(f'<image href="{one}"/>'))
    with pytest.raises(DomainError) as exc:
        sanitize_svg(_svg(f'<image href="{one}"/><image href="{one}"/>'))
    assert exc.value.code is ErrorCode.CORRUPT_FILE
    assert "images" in exc.value.detail


def test_namespaces_are_not_registered_globally() -> None:
    before = dict(ET._namespace_map)  # type: ignore[attr-defined]
    clean, _ = sanitize_svg(_svg('<use xlink:href="#a"/><rect id="a"/>'))
    assert dict(ET._namespace_map) == before  # type: ignore[attr-defined]
    root = ET.fromstring(clean)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    use = root[0]
    assert use.get("{http://www.w3.org/1999/xlink}href") == "#a"


@pytest.mark.parametrize("attr", ["href", "xlink:href", "XLINK:HREF"])
@pytest.mark.parametrize("tag", ["set", "animate"])
def test_href_animations_are_dropped(tag: str, attr: str) -> None:
    body = (
        f'<image href="#a" id="a"><{tag} attributeName="{attr}" to="http://evil.example/x"/>'
        f'<{tag} attributeName="opacity" to="0"/></image>'
    )
    clean, root = sanitize_svg(_svg(body))
    assert b"evil" not in clean
    image = root[0]
    assert [c.get("attributeName") for c in image] == ["opacity"]


# ------------------------------------------------------------------------------- sizes


def test_fit_to_a0_accepts_tiny_floats() -> None:
    assert fit_to_a0_mm_per_px(0.3, 0.2) > 0
    assert fit_to_a0_mm_per_px(0.4, 0.4) == pytest.approx(841 / 0.4)


@needs_svg
def test_tiny_viewbox_does_not_divide_by_zero(tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "tiny.svg"
    src.write_bytes(_svg('<rect width="0.3" height="0.2"/>', 'viewBox="0 0 0.3 0.2"'))
    details: list[str] = []
    try:
        ingest_document(src, tmp_path / "doc", config=cfg)
    except DomainError as exc:
        details.append(exc.detail.lower())
    assert not any("division" in d for d in details)


@pytest.mark.parametrize(
    ("w", "h", "physical"),
    [
        ("794px", "1123px", True),  # A4 at 96 px/in
        ("1123px", "794px", True),
        ("1587px", "2245px", True),  # A3
        ("500px", "300px", False),
        ("794", "1123", False),  # unitless
        ("794px", "1123", False),
        ("100%", "100%", False),
    ],
)
def test_px_sizes_are_physical_only_when_they_match_a_standard(
    w: str, h: str, physical: bool
) -> None:
    _, root = sanitize_svg(_svg("<rect/>", f'width="{w}" height="{h}"'))
    assert has_physical_size(root) is physical


@needs_svg
def test_px_svg_matching_a4_is_physical_without_a_warning(tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "a4.svg"
    src.write_bytes(
        _svg('<rect width="794" height="1123" fill="#ccc"/>', 'width="794px" height="1123px"')
    )
    result = ingest_document(src, tmp_path / "doc", config=cfg)
    assert not result.pages[0].has_warning(WarningCode.PHYSICAL_SIZE_ASSUMED)
    assert_size(result, 210, 297, tol=1.0)


@needs_svg
def test_px_svg_of_an_unknown_size_is_still_assumed_a0(tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "odd.svg"
    src.write_bytes(
        _svg('<rect width="500" height="300" fill="#ccc"/>', 'width="500px" height="300px"')
    )
    result = ingest_document(src, tmp_path / "doc", config=cfg)
    assert result.pages[0].has_warning(WarningCode.PHYSICAL_SIZE_ASSUMED)
    assert_size(result, 1189, 713.4, tol=1.5)
