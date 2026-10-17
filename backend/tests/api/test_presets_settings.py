"""User presets CRUD + export/import (PRE-02) and app settings (PRE-05)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

SETTINGS = {"target": {"paper": "A3"}, "tiling": {"overlap_mm": 12}}


def _create(c: TestClient, name: str = "Mine", settings: dict[str, Any] | None = None) -> Any:
    r = c.post("/api/v1/presets", json={"name": name, "settings": settings or SETTINGS})
    assert r.status_code == 201, r.text
    return r.json()


def test_preset_crud(client: TestClient) -> None:
    p = _create(client, "  Mine  ")
    assert p["name"] == "Mine"
    assert not p["builtin"]
    assert p["id"].startswith("user-")
    listed = client.get("/api/v1/presets").json()
    assert any(x["id"] == p["id"] for x in listed)
    assert any(x["builtin"] for x in listed)
    assert client.get(f"/api/v1/presets/{p['id']}").json()["settings"] == SETTINGS

    r = client.put(f"/api/v1/presets/{p['id']}", json={"name": "Renamed", "description": "d"})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    assert r.json()["description"] == "d"
    assert r.json()["settings"] == SETTINGS  # untouched by a rename

    assert client.delete(f"/api/v1/presets/{p['id']}").status_code == 204
    assert client.get(f"/api/v1/presets/{p['id']}").status_code == 404
    assert client.delete(f"/api/v1/presets/{p['id']}").status_code == 404


def test_builtins_are_read_only(client: TestClient) -> None:
    builtin = next(x for x in client.get("/api/v1/presets").json() if x["builtin"])
    for r in (
        client.put(f"/api/v1/presets/{builtin['id']}", json={"name": "x"}),
        client.delete(f"/api/v1/presets/{builtin['id']}"),
    ):
        assert r.status_code == 403
        assert r.json()["code"] == "PRESET_READONLY"


@pytest.mark.parametrize(
    "settings",
    [{"nonsense": 1}, {"tiling": {"overlap_mm": -5}}, {"target": {"paper": "ZZ"}}],
)
def test_invalid_settings_rejected(client: TestClient, settings: dict[str, Any]) -> None:
    r = client.post("/api/v1/presets", json={"name": "bad", "settings": settings})
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_SETTINGS"
    assert client.post("/api/v1/presets", json={"name": " ", "settings": {}}).status_code == 422


def test_document_id_is_stripped(client: TestClient) -> None:
    p = _create(client, "x", {"document_id": "abc", "target": {"paper": "A3"}})
    assert "document_id" not in p["settings"]


def test_export_import_roundtrip(client: TestClient) -> None:
    p = _create(client, "Round trip")
    exp = client.get(f"/api/v1/presets/{p['id']}/export")
    assert exp.status_code == 200
    assert "attachment" in exp.headers["content-disposition"]
    data = exp.json()
    assert data["format"] == "a0td-preset"
    imported = client.post("/api/v1/presets/import", json=data)
    assert imported.status_code == 201, imported.text
    assert imported.json()["id"] != p["id"]
    assert imported.json()["settings"] == SETTINGS
    assert imported.json()["name"] == "Round trip"
    builtin_id = next(x["id"] for x in client.get("/api/v1/presets").json() if x["builtin"])
    assert client.get(f"/api/v1/presets/{builtin_id}/export").status_code == 200


def test_import_validates(client: TestClient) -> None:
    bad_format = {"format": "other", "version": 1, "name": "x", "settings": {}}
    assert client.post("/api/v1/presets/import", json=bad_format).status_code == 422
    bad_settings = {"format": "a0td-preset", "version": 1, "name": "x", "settings": {"foo": 1}}
    assert client.post("/api/v1/presets/import", json=bad_settings).status_code == 422
    assert client.post("/api/v1/presets/import", json={"name": "x"}).status_code == 201


def test_app_settings(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A0TD_RETENTION_HOURS", raising=False)
    s = client.get("/api/v1/settings").json()
    assert s["locale"] is None
    assert s["retention_locked"] is False
    r = client.put(
        "/api/v1/settings",
        json={
            "locale": "en",
            "units": "in",
            "default_printer_id": "builtin-laser",
            "default_paper": "LETTER",
            "retention_hours": 10,
        },
    )
    assert r.status_code == 200, r.text
    got = client.get("/api/v1/settings").json()
    assert (got["locale"], got["units"], got["default_paper"]) == ("en", "in", "LETTER")
    assert got["retention_hours"] == 10
    assert client.get("/api/v1/info").json()["limits"]["retention_hours"] == 10
    bad = client.put("/api/v1/settings", json={"default_printer_id": "nope"})
    assert bad.status_code == 422
    assert client.put("/api/v1/settings", json={"locale": "fr"}).status_code == 422


def test_retention_read_only_when_env_sets_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("A0TD_RETENTION_HOURS", "24")
    assert client.get("/api/v1/settings").json()["retention_locked"] is True
    r = client.put("/api/v1/settings", json={"retention_hours": 5})
    assert r.status_code == 422
    assert client.put("/api/v1/settings", json={"locale": "es"}).status_code == 200
