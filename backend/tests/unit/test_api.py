from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from a0td.api.app import create_app
from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode
from a0td.logging import JsonFormatter
from a0td.version import __version__


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    config = AppConfig(static_dir=None, data_dir=tmp_path / "data", tmp_dir=tmp_path / "tmp")
    with TestClient(create_app(config)) as c:
        yield c


def test_health(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json()["status"] == "ready"


def test_info(client: TestClient) -> None:
    data = client.get("/api/v1/info").json()
    assert data["version"] == __version__
    assert {c["id"] for c in data["converters"]} >= {"pdf", "raster"}
    assert data["limits"]["max_sheets"] == 500
    assert data["limits"]["max_upload_mb"] == 1024
    assert data["auth_mode"] == "none"


def test_papers(client: TestClient) -> None:
    papers = client.get("/api/v1/papers").json()
    assert len(papers) == 19
    a4 = next(p for p in papers if p["id"] == "A4")
    assert (a4["width_mm"], a4["height_mm"]) == (210, 297)


def test_problem_json_for_domain_error() -> None:
    app: FastAPI = create_app(AppConfig(static_dir=None))

    @app.get("/boom")
    def boom() -> None:
        raise DomainError(ErrorCode.TOO_MANY_SHEETS, "too many", {"max": 500})

    r = TestClient(app).get("/boom")
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "TOO_MANY_SHEETS"
    assert body["params"] == {"max": 500}
    assert body["status"] == 422
    assert body["detail"] == "too many"


def test_unknown_route_is_problem_json(client: TestClient) -> None:
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


def test_validation_error_is_problem_json() -> None:
    app = create_app(AppConfig(static_dir=None))

    @app.get("/need-int")
    def need_int(n: int) -> int:
        return n

    r = TestClient(app).get("/need-int", params={"n": "x"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_SETTINGS"
    assert body["errors"][0]["field"] == "query.n"


def test_spa_served_with_fallback(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>spa</html>")
    (tmp_path / "app.js").write_text("console.log(1)")
    c = TestClient(create_app(AppConfig(static_dir=tmp_path)))
    assert "spa" in c.get("/").text
    assert "spa" in c.get("/editor/123").text  # client-side route
    assert c.get("/app.js").text == "console.log(1)"
    assert c.get("/healthz").json() == {"status": "ok"}  # API not shadowed
    assert c.get("/api/v1/nope").status_code == 404


def test_missing_static_dir_is_ignored(tmp_path: Path) -> None:
    c = TestClient(create_app(AppConfig(static_dir=tmp_path / "missing")))
    assert c.get("/").status_code == 404


def test_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A0TD_MAX_SHEETS", "42")
    monkeypatch.setenv("A0TD_LOG_FORMAT", "text")
    cfg = AppConfig()
    assert cfg.max_sheets == 42
    assert cfg.log_format == "text"
    assert cfg.port == 8080
    assert str(cfg.data_dir).replace("\\", "/") == "/data"


def test_json_logging(capsys: pytest.CaptureFixture[str]) -> None:
    import json
    import logging

    from a0td.logging import configure_logging

    configure_logging("info", "json")
    logging.getLogger("a0td.test").info("hello", extra={"job": "j1"})
    line = capsys.readouterr().out.strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["message"] == "hello"
    assert rec["level"] == "info"
    assert rec["job"] == "j1"
    assert isinstance(JsonFormatter(), logging.Formatter)
