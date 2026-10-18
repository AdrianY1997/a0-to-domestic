"""Office / ODF / metafile converter (LibreOffice) and its hardening (SPEC 7.1, 14, 15.5, 15.6)."""

import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.ingest import ingest_document
from a0td.ingest.converters import office as office_mod
from a0td.ingest.converters.base import ConvertContext, run_tool, tool_available
from a0td.ingest.converters.common import tool_env
from a0td.ingest.converters.office import (
    OfficeConverter,
    declared_fonts,
    prepare_profile,
)
from convert_support import assert_not_blank, assert_size, convert, fixture_path

needs_lo = pytest.mark.skipif(not tool_available("soffice"), reason="LibreOffice not installed")

MACRO_MARKER = Path("/tmp/a0td_macro_pwned")


# --------------------------------------------------------------------------- no soffice needed


def test_office_converter_availability_follows_soffice() -> None:
    assert OfficeConverter().available() is tool_available("soffice")


def test_profile_gets_the_macro_disabling_registry_first(tmp_path: Path) -> None:
    template = tmp_path / "template.xcu"
    template.write_text(
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">TEMPLATE</oor:items>',
        encoding="utf-8",
    )
    profile = prepare_profile(tmp_path / "profile", template)
    text = (profile / "user" / "registrymodifications.xcu").read_text()
    assert "TEMPLATE" in text
    _assert_links_never_update(text)


def _assert_links_never_update(text: str) -> None:
    root = ET.fromstring(text)  # still well-formed once the hardening items are appended
    assert root is not None
    for app in ("Writer", "Calc"):
        assert f'oor:path="/org.openoffice.Office.{app}/Content/Update"' in text
    assert text.count('<prop oor:name="Link" oor:op="fuse"><value>2</value>') == 2
    assert 'oor:name="UpdateDocMode" oor:op="fuse"><value>0</value>' in text


def test_profile_falls_back_to_the_builtin_registry(tmp_path: Path) -> None:
    profile = prepare_profile(tmp_path / "profile", tmp_path / "missing.xcu")
    text = (profile / "user" / "registrymodifications.xcu").read_text()
    assert "DisableMacrosExecution" in text
    assert "<value>true</value>" in text
    _assert_links_never_update(text)


def test_fc_match_gets_the_font_name_after_double_dash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    class Result:
        stdout = b"Whatever"

    def fake_run(argv, **kw):  # type: ignore[no-untyped-def]
        seen.append([str(a) for a in argv])
        return Result()

    monkeypatch.setattr(office_mod, "tool_available", lambda name: True)
    monkeypatch.setattr(office_mod, "run_tool", fake_run)
    assert office_mod.substituted_fonts(["--evil=1"], tmp_path, {}) == ["--evil=1"]
    assert seen == [["fc-match", "-f", "%{family}", "--", "--evil=1"]]


def test_user_fonts_dir_reaches_fontconfig(tmp_path: Path) -> None:
    data = tmp_path / "data"
    cfg = AppConfig(data_dir=data, tmp_dir=tmp_path / "tmp")
    ctx = ConvertContext(config=cfg)
    assert "FONTCONFIG_FILE" not in tool_env(ctx, tmp_path / "s1")  # no fonts directory yet
    (data / "fonts").mkdir(parents=True)
    assert "FONTCONFIG_FILE" not in tool_env(ctx, tmp_path / "s2")  # empty directory
    (data / "fonts" / "x.ttf").write_bytes(b"not really a font")
    env = tool_env(ctx, tmp_path / "s3")
    conf = Path(env["FONTCONFIG_FILE"]).read_text()
    assert f"<dir>{(data / 'fonts').resolve()}</dir>" in conf
    assert "/etc/fonts/fonts.conf" in conf


def test_declared_fonts_reads_the_document_content(fixtures_dir: Path) -> None:
    fonts = declared_fonts(fixture_path(fixtures_dir, "docx_missing_font.docx"))
    assert "Zapfino Imaginary" in fonts
    assert declared_fonts(fixture_path(fixtures_dir, "svg_physical.svg")) == []  # not a ZIP


def test_docm_is_rejected_before_any_conversion(fixtures_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(DomainError) as exc:
        ingest_document(fixtures_dir / "malicious" / "docx_macro.docm", tmp_path / "doc")
    assert exc.value.code is ErrorCode.UNSUPPORTED_FORMAT


# ------------------------------------------------------------------------------- LibreOffice


@needs_lo
def test_pptx_a0_keeps_its_slide_size(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    result, work = convert(fixtures_dir, "pptx_a0.pptx", tmp_path, cfg)
    assert result.format.value == "pptx"
    assert result.kind == "pdf"
    assert len(result.pages) == 1
    assert_size(result, 841, 1189)
    assert result.pages[0].standard_match is not None
    assert result.pages[0].standard_match.id == "A0"
    assert_not_blank(work)


@needs_lo
def test_docx_converts_to_an_a4_page(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    result, work = convert(fixtures_dir, "docx_simple.docx", tmp_path, cfg)
    assert len(result.pages) == 1
    assert_size(result, 210, 297)
    assert_not_blank(work)


@needs_lo
def test_missing_font_is_reported(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    result, _ = convert(fixtures_dir, "docx_missing_font.docx", tmp_path, cfg)
    warnings = [w for w in result.warnings if w.code is WarningCode.FONTS_SUBSTITUTED]
    assert warnings
    assert "Zapfino Imaginary" in warnings[0].params["fonts"]


@needs_lo
@pytest.mark.parametrize("name", ["metafile_a4.emf", "metafile_a4.wmf"])
def test_metafiles_convert_through_libreoffice_draw(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig, name: str
) -> None:
    result, work = convert(fixtures_dir, name, tmp_path, cfg)
    assert result.kind == "pdf"
    assert len(result.pages) == 1
    assert_not_blank(work)


@needs_lo
def test_document_with_an_autorun_macro_does_not_execute_it(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    MACRO_MARKER.unlink(missing_ok=True)
    try:
        result, work = convert(fixtures_dir, "malicious/odt_macro.odt", tmp_path, cfg)
        assert not MACRO_MARKER.exists(), "the document macro ran"
        assert len(result.pages) == 1
        assert_not_blank(work)
    finally:
        MACRO_MARKER.unlink(missing_ok=True)


@needs_lo
def test_each_job_uses_its_own_profile_and_cleans_scratch(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    _, work = convert(fixtures_dir, "docx_simple.docx", tmp_path, cfg)
    assert not (work / ".work").exists()  # scratch (profile included) is removed after ingest


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_conversion_timeout_is_reported(tmp_path: Path) -> None:
    """A tool that outlives its budget raises CONVERSION_TIMEOUT and its whole group dies."""
    pid_file = tmp_path / "child.pid"
    script = f"sleep 30 & echo $! > {pid_file}; wait"
    with pytest.raises(DomainError) as exc:
        run_tool(["sh", "-c", script], scratch=tmp_path / "scratch", timeout_s=1, no_network=False)
    assert exc.value.code is ErrorCode.CONVERSION_TIMEOUT
    child = int(pid_file.read_text().strip())
    for _ in range(50):  # the grandchild must be gone (reaped by init or already dead)
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("child process survived the timeout")
