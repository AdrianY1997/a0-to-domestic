"""Shared pytest options and the fixtures of the render tests (WP-07)."""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from a0td.config import AppConfig
from a0td.ingest import ingest_document
from a0td.render import RenderResult, render_job
from render_support import FIXED_NOW, make_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="regenerate the golden PNG references instead of comparing against them",
    )


@pytest.fixture(scope="session")
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture(scope="session")
def rfx_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """``fixtures/`` of the repo; regenerated into a temp dir if it is missing."""
    path = REPO_ROOT / "fixtures"
    if (path / "manifest.json").exists():
        return path
    script = REPO_ROOT / "tools" / "make_fixtures.py"
    if not script.exists():
        pytest.skip("fixtures/ and tools/make_fixtures.py are missing")
    out = tmp_path_factory.mktemp("fixtures")
    subprocess.run([sys.executable, str(script), "--out", str(out)], check=True)
    return out


@pytest.fixture(scope="session")
def rfx_ingest(rfx_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], Path]:
    """Ingest a fixture once per session: ``rfx_ingest("a0_vector_grid.pdf") -> doc_dir``."""
    cfg = AppConfig(
        data_dir=tmp_path_factory.mktemp("rdata"), tmp_dir=tmp_path_factory.mktemp("rtmp")
    )
    cache: dict[str, Path] = {}

    def _get(name: str) -> Path:
        if name not in cache:
            work = tmp_path_factory.mktemp("doc_" + Path(name).stem)
            ingest_document(rfx_dir / name, work, config=cfg)
            cache[name] = work
        return cache[name]

    return _get


@pytest.fixture
def render(
    rfx_ingest: Callable[[str], Path], tmp_path: Path
) -> Callable[..., tuple[RenderResult, Path]]:
    """``render("a0_vector_grid.pdf", scale=..., ...) -> (result, pdf_path)``."""

    def _render(name: str, **kw: Any) -> tuple[RenderResult, Path]:
        opts = {
            k: kw.pop(k)
            for k in ("printer", "test_sheet_only", "locale", "doc_name", "progress")
            if k in kw
        }
        settings = make_settings(**kw)
        out_dir = tmp_path / "out"
        result = render_job(
            rfx_ingest(name),
            settings,
            out_dir,
            now=FIXED_NOW,
            doc_name=opts.pop("doc_name", Path(name).stem),
            **opts,
        )
        return result, out_dir / result.outputs[0].name

    return _render


@pytest.fixture(autouse=True)
def _allow_loopback_printers(monkeypatch: pytest.MonkeyPatch) -> None:
    """tests/support/fake_ipp.py listens on 127.0.0.1, which the SSRF policy rejects by default."""
    monkeypatch.setenv("A0TD_IPP_ALLOW_HOSTS", "127.0.0.1")
