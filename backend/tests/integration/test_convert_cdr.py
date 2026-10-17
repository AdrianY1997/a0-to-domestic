"""CorelDRAW (.cdr) is routed through the Office converter with a low-fidelity warning (SPEC 7.1).

There is no CDR writer in the image, so the tools are replaced by stand-ins that produce a PDF:
what is under test is the routing, the fallback to Inkscape and the warning.
"""

from pathlib import Path

import pymupdf
import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.ingest import ConverterRegistry, ingest_document
from a0td.ingest.converters import office as office_mod
from a0td.ingest.converters.base import ConvertContext
from a0td.ingest.converters.office import OfficeConverter
from a0td.ingest.detect import detect
from a0td.ingest.models import DocFormat

CDR_BYTES = b"RIFF\x24\x00\x00\x00CDRvrsn\x04\x00\x00\x00\x0f\x00\x00\x00" + b"\x00" * 64


def _write_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(50, 50, 400, 600), color=(0, 0, 0), width=2)
    doc.save(path)


@pytest.fixture
def cdr_file(tmp_path: Path) -> Path:
    src = tmp_path / "logo.cdr"
    src.write_bytes(CDR_BYTES)
    return src


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> ConverterRegistry:
    monkeypatch.setattr(OfficeConverter, "available", lambda self: True)
    return ConverterRegistry([OfficeConverter()])


def test_cdr_is_detected_and_handled_by_the_office_converter() -> None:
    assert DocFormat.CDR in OfficeConverter.handles


def test_cdr_detection(cdr_file: Path) -> None:
    assert detect(cdr_file).format is DocFormat.CDR


def test_cdr_through_libreoffice_warns_about_fidelity(
    cdr_file: Path,
    tmp_path: Path,
    cfg: AppConfig,
    registry: ConverterRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_lo(staged: Path, out_dir: Path, scratch: Path, ctx: ConvertContext, env: dict) -> None:  # type: ignore[type-arg]
        seen.append(staged.name)
        _write_pdf(out_dir / "input.pdf")

    monkeypatch.setattr(OfficeConverter, "_libreoffice", staticmethod(fake_lo))
    result = ingest_document(cdr_file, tmp_path / "doc", config=cfg, registry=registry)
    assert seen == ["input.cdr"]
    assert result.format is DocFormat.CDR
    assert result.mime == "application/vnd.corel-draw"
    [warning] = result.warnings
    assert warning.code is WarningCode.LOW_FIDELITY_FORMAT
    assert warning.params == {"format": "cdr"}
    assert result.pages[0].width_mm == pytest.approx(209.9, abs=0.5)


def test_cdr_falls_back_to_inkscape_when_libreoffice_fails(
    cdr_file: Path,
    tmp_path: Path,
    cfg: AppConfig,
    registry: ConverterRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_lo(*args: object, **kw: object) -> None:
        raise DomainError(ErrorCode.CONVERSION_FAILED, "soffice failed")

    def fake_inkscape(
        staged: Path,
        out_dir: Path,
        scratch: Path,
        ctx: ConvertContext,
        env: dict,  # type: ignore[type-arg]
    ) -> Path:
        pdf = out_dir / "inkscape.pdf"
        _write_pdf(pdf)
        return pdf

    monkeypatch.setattr(OfficeConverter, "_libreoffice", staticmethod(failing_lo))
    monkeypatch.setattr(OfficeConverter, "_inkscape", staticmethod(fake_inkscape))
    monkeypatch.setattr(office_mod, "tool_available", lambda name: name == "inkscape")
    result = ingest_document(cdr_file, tmp_path / "doc", config=cfg, registry=registry)
    assert [w.code for w in result.warnings] == [WarningCode.LOW_FIDELITY_FORMAT]


def test_cdr_fails_clearly_without_any_converter(
    cdr_file: Path,
    tmp_path: Path,
    cfg: AppConfig,
    registry: ConverterRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_lo(*args: object, **kw: object) -> None:
        raise DomainError(ErrorCode.CONVERSION_FAILED, "soffice failed")

    monkeypatch.setattr(OfficeConverter, "_libreoffice", staticmethod(failing_lo))
    monkeypatch.setattr(office_mod, "tool_available", lambda name: False)
    with pytest.raises(DomainError) as exc:
        ingest_document(cdr_file, tmp_path / "doc", config=cfg, registry=registry)
    assert exc.value.code is ErrorCode.CONVERSION_FAILED
