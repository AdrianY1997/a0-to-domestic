"""Fixtures of the API tests: a real app over a temp data dir and real worker subprocesses."""

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from a0td.api.app import create_app
from a0td.config import AppConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
SUPPORT_DIR = Path(__file__).resolve().parents[1] / "support"

sys.path.insert(0, str(SUPPORT_DIR))


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    path = REPO_ROOT / "fixtures"
    if not (path / "manifest.json").exists():
        pytest.skip("fixtures/ is not available (mount it or run tools/make_fixtures.py)")
    return path


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plug the fake render engine in (in this process and in worker subprocesses)."""
    monkeypatch.syspath_prepend(str(SUPPORT_DIR))
    monkeypatch.setenv("PYTHONPATH", str(SUPPORT_DIR))
    monkeypatch.setenv("A0TD_RENDER_ENGINE", "fake_render_engine")
    monkeypatch.delenv("FAKE_RENDER_MODE", raising=False)


@pytest.fixture
def make_config(tmp_path: Path) -> Callable[..., AppConfig]:
    def _make(**kw: Any) -> AppConfig:
        opts: dict[str, Any] = {
            "data_dir": tmp_path / "data",
            "tmp_dir": tmp_path / "tmp",
            "static_dir": None,
            "job_timeout_s": 120,
            "log_level": "warning",
        }
        opts.update(kw)
        return AppConfig(**opts)

    return _make


@pytest.fixture
def config(make_config: Callable[..., AppConfig]) -> AppConfig:
    return make_config()


@pytest.fixture
def client(config: AppConfig, fake_engine: None) -> Iterator[TestClient]:
    with TestClient(create_app(config)) as c:
        yield c
