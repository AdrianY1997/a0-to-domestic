"""HEIC / AVIF / JPEG 2000 converter (pillow-heif, Pillow) (SPEC 7.1)."""

from pathlib import Path

import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.ingest import DpiSource, ingest_document, set_physical_size
from a0td.ingest.converters.heif import HeifConverter
from convert_support import assert_not_blank, assert_size, convert, fixture_path

pytestmark = pytest.mark.skipif(not HeifConverter().available(), reason="pillow-heif missing")


def test_heic_with_exif_dpi(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    result, work = convert(fixtures_dir, "photo_150dpi.heic", tmp_path, cfg)
    page = result.pages[0]
    assert result.format.value == "heif"
    assert result.kind == "raster"
    assert (page.px.w, page.px.h) == (800, 600)  # type: ignore[union-attr]
    assert page.dpi_source is DpiSource.METADATA
    assert_size(result, 135.5, 101.6, tol=0.5)
    assert not page.has_warning(WarningCode.PHYSICAL_SIZE_ASSUMED)
    assert (work / "page-0.tif").exists()
    assert_not_blank(work)


@pytest.mark.parametrize(
    ("name", "fmt"), [("photo_nodpi.avif", "avif"), ("photo_nodpi.jp2", "jp2")]
)
def test_modern_formats_without_dpi_assume_a0(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig, name: str, fmt: str
) -> None:
    result, work = convert(fixtures_dir, name, tmp_path, cfg)
    page = result.pages[0]
    assert result.format.value == fmt
    assert page.has_warning(WarningCode.PHYSICAL_SIZE_ASSUMED)
    assert page.dpi_source is DpiSource.ASSUMED
    assert max(page.width_mm, page.height_mm) == pytest.approx(1121.3, abs=1.0)
    assert_not_blank(work)
    fixed = set_physical_size(work, 0, width_mm=400, config=cfg).pages[0]
    assert fixed.width_mm == pytest.approx(400)
    assert fixed.dpi_source is DpiSource.USER


def test_heic_pixel_limit(fixtures_dir: Path, tmp_path: Path, cfg: AppConfig) -> None:
    tiny = cfg.model_copy(update={"max_megapixels": 0})  # 800x600 px is above a 0 Mpx limit
    with pytest.raises(DomainError) as exc:
        ingest_document(
            fixture_path(fixtures_dir, "photo_150dpi.heic"), tmp_path / "doc", config=tiny
        )
    assert exc.value.code is ErrorCode.PIXEL_LIMIT_EXCEEDED


def test_corrupt_heic_is_rejected_cleanly(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    good = (
        (fixtures_dir / "photo_150dpi.heic").read_bytes()
        if (fixtures_dir / "photo_150dpi.heic").exists()
        else None
    )
    if good is None:
        pytest.skip("fixture missing")
    src = tmp_path / "cut.heic"
    src.write_bytes(good[: len(good) // 3])
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "doc", config=cfg)
    assert exc.value.code in (ErrorCode.CORRUPT_FILE, ErrorCode.UNSUPPORTED_FORMAT)
