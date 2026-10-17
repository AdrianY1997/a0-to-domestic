"""Helpers shared by the F2 converter integration tests (WP-12)."""

from pathlib import Path

import pytest
from PIL import Image

from a0td.config import AppConfig
from a0td.ingest import IngestResult, ingest_document


def fixture_path(fixtures_dir: Path, name: str) -> Path:
    path = fixtures_dir / name
    if not path.exists():
        pytest.skip(f"fixture {name} was not generated in this environment")
    return path


def convert(
    fixtures_dir: Path, name: str, tmp_path: Path, cfg: AppConfig
) -> tuple[IngestResult, Path]:
    """Ingest a fixture into ``tmp_path / "doc"``."""
    work = tmp_path / "doc"
    result = ingest_document(fixture_path(fixtures_dir, name), work, config=cfg)
    return result, work


def assert_not_blank(work: Path, index: int = 0) -> None:
    """The page preview has visible ink (not an all-white page)."""
    with Image.open(work / "pages" / str(index) / "preview.webp") as img:
        low, _ = img.convert("L").getextrema()
    assert low < 235, "the converted page is blank"


def assert_size(result: IngestResult, w: float, h: float, tol: float = 2.0, page: int = 0) -> None:
    p = result.pages[page]
    assert (p.width_mm, p.height_mm) == (
        pytest.approx(w, abs=tol),
        pytest.approx(h, abs=tol),
    )
