"""Shared fixtures of the ingest integration tests."""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from a0td.config import AppConfig
from a0td.ingest import IngestResult, ingest_document

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """``fixtures/`` of the repo; regenerated into a temp dir if it is missing."""
    path = REPO_ROOT / "fixtures"
    if (path / "manifest.json").exists():
        return path
    out = tmp_path_factory.mktemp("fixtures")
    script = REPO_ROOT / "tools" / "make_fixtures.py"
    if not script.exists():
        pytest.skip("fixtures/ and tools/make_fixtures.py are missing")
    subprocess.run([sys.executable, str(script), "--out", str(out)], check=True)
    return out


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(data_dir=tmp_path / "data", tmp_dir=tmp_path / "tmp")


@pytest.fixture
def make_cfg(tmp_path: Path) -> Callable[..., AppConfig]:
    def _make(**kw: object) -> AppConfig:
        return AppConfig(data_dir=tmp_path / "data", tmp_dir=tmp_path / "tmp", **kw)  # type: ignore[arg-type]

    return _make


@pytest.fixture(scope="session")
def ingested(
    fixtures_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> Callable[[str], tuple[IngestResult, Path]]:
    """Ingest a fixture once per session: ``ingested("a0_vector_grid.pdf") -> (result, workdir)``.

    Do not mutate what it returns; tests that change the document ingest their own copy.
    """
    cache: dict[str, tuple[IngestResult, Path]] = {}
    cfg = AppConfig(
        data_dir=tmp_path_factory.mktemp("data"), tmp_dir=tmp_path_factory.mktemp("tmp")
    )

    def _get(name: str) -> tuple[IngestResult, Path]:
        if name not in cache:
            workdir = tmp_path_factory.mktemp(Path(name).stem)
            password = "secret" if name == "encrypted.pdf" else None
            result = ingest_document(fixtures_dir / name, workdir, config=cfg, password=password)
            cache[name] = (result, workdir)
        return cache[name]

    return _get
