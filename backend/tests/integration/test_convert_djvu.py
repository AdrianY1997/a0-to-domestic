"""DjVu converter: ``ddjvu`` -> TIFF -> raster path (SPEC 7.1, WP-17)."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest import default_registry, ingest_document
from a0td.ingest.converters.base import tool_available
from a0td.ingest.converters.djvu import DjvuConverter
from a0td.ingest.models import DocFormat
from convert_support import assert_not_blank, assert_size, convert

needs_ddjvu = pytest.mark.skipif(not tool_available("ddjvu"), reason="DjVuLibre not installed")
needs_djvm = pytest.mark.skipif(
    not (tool_available("djvm") and tool_available("c44")), reason="DjVuLibre tools missing"
)


def test_converter_availability_follows_ddjvu() -> None:
    assert DjvuConverter().available() is tool_available("ddjvu")
    reg = default_registry()
    assert (reg.for_format(DocFormat.DJVU) is not None) is tool_available("ddjvu")


@needs_ddjvu
def test_djvu_dpi_comes_from_the_file(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    result, work = convert(fixtures_dir, "djvu_a4_150dpi.djvu", tmp_path, cfg)
    page = result.pages[0]
    assert result.format is DocFormat.DJVU
    assert result.mime == "image/vnd.djvu"
    assert result.kind == "raster"
    assert (page.px.w, page.px.h) == (1240, 1754)  # type: ignore[union-attr]
    assert page.dpi == pytest.approx(150)
    assert page.dpi_source is not None
    assert page.dpi_source.value == "metadata"
    assert_size(result, 209.97, 297.01, tol=0.5)
    assert page.standard_match is not None
    assert page.standard_match.id == "A4"
    assert_not_blank(work)
    assert (work / "page-0.tif").is_file()


def _multipage(src: Path, tmp_path: Path, pages: int) -> Path:
    out = tmp_path / "multi.djvu"
    subprocess.run(["djvm", "-c", str(out), *[str(src)] * pages], check=True, capture_output=True)
    return out


@needs_ddjvu
@needs_djvm
def test_multipage_djvu_gives_one_page_each(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    multi = _multipage(fixtures_dir / "djvu_a4_150dpi.djvu", tmp_path, 3)
    result = ingest_document(multi, tmp_path / "doc", config=cfg)
    assert [p.index for p in result.pages] == [0, 1, 2]
    assert all(p.dpi == pytest.approx(150) for p in result.pages)


@needs_ddjvu
@needs_djvm
def test_page_limit_is_checked_before_decoding(
    fixtures_dir: Path, tmp_path: Path, make_cfg: Callable[..., AppConfig]
) -> None:
    multi = _multipage(fixtures_dir / "djvu_a4_150dpi.djvu", tmp_path, 3)
    with pytest.raises(DomainError) as exc:
        ingest_document(multi, tmp_path / "doc", config=make_cfg(max_pages=2))
    assert exc.value.code is ErrorCode.TOO_MANY_PAGES
    assert exc.value.params == {"pages": 3, "limit": 2}
    assert not (tmp_path / "doc" / "page-0.tif").exists()


@needs_ddjvu
def test_damaged_djvu_is_a_corrupt_file(tmp_path: Path, cfg: AppConfig) -> None:
    src = tmp_path / "bad.djvu"
    src.write_bytes(b"AT&TFORM\x00\x00\x00\x10DJVUINFO" + b"\x00" * 64)
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "doc", config=cfg)
    assert exc.value.code is ErrorCode.CORRUPT_FILE


def test_djvu_is_unavailable_without_ddjvu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: AppConfig
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    src = tmp_path / "x.djvu"
    src.write_bytes(b"AT&TFORM\x00\x00\x00\x10DJVUINFO" + b"\x00" * 64)
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "doc", config=cfg)
    assert exc.value.code is ErrorCode.FORMAT_NOT_AVAILABLE_IN_IMAGE


def test_dump_dpis_reads_info_chunks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    from a0td.ingest.converters import djvu as djvu_mod

    out = (
        b"  FORM:DJVU [1]\n    INFO [10]         DjVu 1240x1754, v24, 150 dpi, gamma=2.2\n"
        b"  FORM:DJVU [2]\n    INFO [10]         DjVu 100x100, v24, 300 dpi, gamma=2.2\n"
    )
    monkeypatch.setattr(djvu_mod, "tool_available", lambda name: True)
    monkeypatch.setattr(djvu_mod, "run_tool", lambda *a, **k: SimpleNamespace(stdout=out))
    assert djvu_mod._dump_dpis(tmp_path / "x.djvu", tmp_path, 5) == [150.0, 300.0]
