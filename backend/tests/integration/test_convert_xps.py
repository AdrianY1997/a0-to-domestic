"""XPS converter (MuPDF through PyMuPDF) (SPEC 7.1)."""

import contextlib
import zipfile
from pathlib import Path

import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest import ingest_document
from convert_support import assert_not_blank, assert_size, convert, fixture_path


def test_xps_converts_to_a4_landscape(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    result, work = convert(fixtures_dir, "xps_a4.xps", tmp_path, cfg)
    assert result.format.value == "xps"
    assert result.kind == "pdf"
    assert len(result.pages) == 1
    assert_size(result, 297, 210)
    assert result.pages[0].standard_match is not None
    assert result.pages[0].standard_match.id == "A4"
    assert_not_blank(work)


def test_xps_with_broken_page_does_not_hang_or_crash(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    src = tmp_path / "broken.xps"
    with (
        zipfile.ZipFile(fixture_path(fixtures_dir, "xps_a4.xps")) as zin,
        zipfile.ZipFile(src, "w") as zout,
    ):
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.endswith(".fpage"):
                data = b"<not xml"
            zout.writestr(info.filename, data)
    # MuPDF may also tolerate the page (rendered empty): both outcomes are acceptable
    with contextlib.suppress(DomainError):
        ingest_document(src, tmp_path / "doc", config=cfg)


def test_xps_page_limit(fixtures_dir: Path, tmp_path: Path) -> None:
    pytest.importorskip("pymupdf")
    cfg = AppConfig(data_dir=tmp_path / "d", tmp_dir=tmp_path / "t", max_page_mm=100)
    with pytest.raises(DomainError) as exc:
        ingest_document(fixture_path(fixtures_dir, "xps_a4.xps"), tmp_path / "doc", config=cfg)
    assert exc.value.code is ErrorCode.PAGE_TOO_LARGE
