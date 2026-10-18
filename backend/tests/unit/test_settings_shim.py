"""``a0td.config`` without pydantic-settings (the browser build, SPEC 17bis)."""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def config_without_pydantic_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    original = sys.modules.get("a0td.config")
    monkeypatch.setitem(sys.modules, "pydantic_settings", None)  # makes the import fail
    sys.modules.pop("a0td.config", None)
    try:
        yield importlib.import_module("a0td.config")
    finally:
        if original is not None:
            sys.modules["a0td.config"] = original
            sys.modules["a0td"].config = original  # type: ignore[attr-defined]


def test_config_reads_a0td_env_variables_through_the_shim(
    config_without_pydantic_settings: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = config_without_pydantic_settings
    monkeypatch.setenv("A0TD_MAX_PAGES", "7")
    monkeypatch.setenv("A0TD_METRICS", "true")
    monkeypatch.setenv("A0TD_DATA_DIR", "/x/y")
    monkeypatch.setenv("A0TD_STATIC_DIR", "   ")
    monkeypatch.setenv("A0TD_UNKNOWN_THING", "ignored")
    cfg = mod.AppConfig()
    assert type(cfg).__mro__[1].__module__ == "a0td._settings_shim"
    assert (cfg.max_pages, cfg.metrics, cfg.static_dir) == (7, True, None)
    assert cfg.data_dir == Path("/x/y")
    assert cfg.port == 8080
    assert mod.AppConfig(max_pages=3).max_pages == 3


def test_shim_validates_like_pydantic_settings(
    config_without_pydantic_settings: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("A0TD_PORT", "0")
    with pytest.raises(ValueError, match="port"):
        config_without_pydantic_settings.AppConfig()
