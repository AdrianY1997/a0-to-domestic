"""Helpers shared by the API tests."""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

Settings = dict[str, Any]


def upload(
    client: TestClient,
    path: Path | None = None,
    *,
    content: bytes | None = None,
    name: str | None = None,
    password: str | None = None,
) -> Any:
    data = content if content is not None else (path.read_bytes() if path else b"")
    filename = name or (path.name if path else "file.bin")
    form = {"password": password} if password is not None else None
    return client.post("/api/v1/documents", files={"file": (filename, data)}, data=form)


def wait_for(
    fetch: Callable[[], dict[str, Any]],
    done: Callable[[dict[str, Any]], bool],
    timeout: float = 90.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = fetch()
        if done(last):
            return last
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting; last state: {json.dumps(last)[:500]}")


def wait_document(
    client: TestClient,
    doc_id: str,
    statuses: tuple[str, ...] = ("ready", "failed", "password_required"),
) -> dict[str, Any]:
    return wait_for(
        lambda: client.get(f"/api/v1/documents/{doc_id}").json(), lambda d: d["status"] in statuses
    )


def wait_job(
    client: TestClient,
    job_id: str,
    statuses: tuple[str, ...] = ("succeeded", "failed", "cancelled"),
    timeout: float = 90.0,
) -> dict[str, Any]:
    return wait_for(
        lambda: client.get(f"/api/v1/jobs/{job_id}").json(),
        lambda j: j["status"] in statuses,
        timeout,
    )


def ingest(client: TestClient, path: Path | None = None, **kw: Any) -> dict[str, Any]:
    r = upload(client, path, **kw)
    assert r.status_code == 202, r.text
    return wait_document(client, r.json()["id"])


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name is not None and data is not None:
            events.append((name, data))
    return events


def job_body(doc_id: str, **overrides: Any) -> Settings:
    settings: Settings = {
        "document_id": doc_id,
        "target": {"paper": "A4"},
        "tiling": {"overlap_mm": 10},
    }
    settings.update(overrides)
    return settings
