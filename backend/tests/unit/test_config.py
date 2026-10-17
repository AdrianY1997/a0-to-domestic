import pytest

from a0td.config import AppConfig


def test_blank_static_dir_env_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A0TD_STATIC_DIR", "")
    assert AppConfig().static_dir is None


def test_whitespace_static_dir_env_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A0TD_STATIC_DIR", "   ")
    assert AppConfig().static_dir is None
