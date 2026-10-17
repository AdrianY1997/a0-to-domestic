"""Direct printing API (IMP-03, PAP-07) against a fake IPP printer."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from apihelpers import ingest, job_body, wait_job
from fake_ipp import FakeIppServer
from fastapi.testclient import TestClient

from a0td.api.app import create_app
from a0td.config import AppConfig
from a0td.printing import ipp

SETTINGS_NO_ASSEMBLY = {"marks": {"assembly_page": "none"}}


@pytest.fixture
def ipp_client(make_config: Callable[..., AppConfig], fake_engine: None) -> Iterator[TestClient]:
    with TestClient(create_app(make_config(enable_ipp=True))) as c:
        yield c


def _render(client: TestClient, fixtures_dir: Path, **extra: Any) -> dict[str, Any]:
    doc = ingest(client, fixtures_dir / "a0_vector_grid.pdf")
    body = job_body(doc["id"], **{**SETTINGS_NO_ASSEMBLY, **extra})
    r = client.post("/api/v1/jobs", json={"settings": body, "outputs": ["pdf"]})
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    return job


def _printer(client: TestClient, uri: str | None, **extra: Any) -> str:
    body = {"name": "Fake", "ipp_uri": uri, **extra}
    r = client.post("/api/v1/printers", json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _sent(srv: FakeIppServer) -> Any:
    return next(r for r in srv.requests if r.operation == ipp.PRINT_JOB)


def test_print_job_sends_pdf_with_job_template(ipp_client: TestClient, fixtures_dir: Path) -> None:
    render = _render(ipp_client, fixtures_dir)
    with FakeIppServer() as srv:
        printer_id = _printer(ipp_client, srv.uri)
        r = ipp_client.post(
            f"/api/v1/jobs/{render['id']}/print", json={"printer_id": printer_id, "copies": 2}
        )
        assert r.status_code == 202, r.text
        job = wait_job(ipp_client, r.json()["id"])
        sent = _sent(srv)
    assert job["kind"] == "print"
    assert job["status"] == "succeeded", job
    assert job["printing"]["printer_job_id"] == 41
    assert job["printing"]["printer_job_state"] == "completed"
    assert job["printing"]["render_job_id"] == render["id"]
    attrs = sent.attrs(ipp.TAG_JOB)
    # A0 on A4 -> landscape sheets of the A4 catalog paper
    assert attrs["media"] == ["iso_a4_210x297mm"]
    assert attrs["print-scaling"] == ["none"]
    assert attrs["sides"] == ["one-sided"]
    assert attrs["print-color-mode"] == ["color"]
    assert attrs["copies"] == [2]
    assert "page-ranges" not in attrs
    assert sent.document.startswith(b"%PDF")
    # print jobs are not listed with the render jobs
    assert [j["id"] for j in ipp_client.get("/api/v1/jobs").json()] == [render["id"]]
    assert ipp_client.get("/api/v1/jobs?kind=print").json()[0]["id"] == job["id"]


def test_print_selected_sheets_become_page_ranges(
    ipp_client: TestClient, fixtures_dir: Path
) -> None:
    render = _render(ipp_client, fixtures_dir)
    sheets = [s for s in render["plans"][0]["sheets"] if s["print_index"] is not None]
    ids = [sheets[1]["id"], sheets[2]["id"], sheets[5]["id"]]
    with FakeIppServer() as srv:
        printer_id = _printer(ipp_client, srv.uri)
        r = ipp_client.post(
            f"/api/v1/jobs/{render['id']}/print", json={"printer_id": printer_id, "sheets": ids}
        )
        assert r.status_code == 202, r.text
        job = wait_job(ipp_client, r.json()["id"])
        sent = _sent(srv)
    assert job["status"] == "succeeded", job
    assert sent.attrs(ipp.TAG_JOB)["page-ranges"] == [(2, 3), (6, 6)]
    assert job["printing"]["pages"] == [2, 3, 6]


def test_print_unknown_sheet_is_rejected(ipp_client: TestClient, fixtures_dir: Path) -> None:
    render = _render(ipp_client, fixtures_dir)
    with FakeIppServer() as srv:
        printer_id = _printer(ipp_client, srv.uri)
        r = ipp_client.post(
            f"/api/v1/jobs/{render['id']}/print",
            json={"printer_id": printer_id, "sheets": ["Z99"]},
        )
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_SETTINGS"


def test_print_requires_a_printer_with_uri(ipp_client: TestClient, fixtures_dir: Path) -> None:
    render = _render(ipp_client, fixtures_dir)
    url = f"/api/v1/jobs/{render['id']}/print"
    no_uri = _printer(ipp_client, None)
    assert ipp_client.post(url, json={"printer_id": no_uri}).status_code == 422
    missing = ipp_client.post(url, json={"printer_id": "nope"})
    assert missing.status_code == 404
    builtin = ipp_client.post(url, json={"printer_id": "builtin-generic"})
    assert builtin.status_code == 422


def test_print_unreachable_printer_fails_the_job(
    ipp_client: TestClient, fixtures_dir: Path
) -> None:
    render = _render(ipp_client, fixtures_dir)
    printer_id = _printer(ipp_client, "ipp://127.0.0.1:9/ipp/print")
    r = ipp_client.post(f"/api/v1/jobs/{render['id']}/print", json={"printer_id": printer_id})
    job = wait_job(ipp_client, r.json()["id"])
    assert job["status"] == "failed"
    assert job["error_code"] == "PRINTER_UNREACHABLE"


def test_print_rejected_by_printer(ipp_client: TestClient, fixtures_dir: Path) -> None:
    render = _render(ipp_client, fixtures_dir)
    with FakeIppServer(print_status=0x040A) as srv:
        printer_id = _printer(ipp_client, srv.uri)
        r = ipp_client.post(f"/api/v1/jobs/{render['id']}/print", json={"printer_id": printer_id})
        job = wait_job(ipp_client, r.json()["id"])
    assert job["status"] == "failed"
    assert job["error_code"] == "PRINT_REJECTED"


def test_only_finished_render_jobs_can_be_printed(
    ipp_client: TestClient, fixtures_dir: Path
) -> None:
    doc = ingest(ipp_client, fixtures_dir / "a0_vector_grid.pdf")
    ingest_job = ipp_client.get(f"/api/v1/documents/{doc['id']}").json()["job_id"]
    printer_id = _printer(ipp_client, "ipp://127.0.0.1:9/x")
    r = ipp_client.post(f"/api/v1/jobs/{ingest_job}/print", json={"printer_id": printer_id})
    assert r.status_code == 409


def test_printer_uri_scheme_is_validated(ipp_client: TestClient) -> None:
    r = ipp_client.post("/api/v1/printers", json={"name": "x", "ipp_uri": "file:///etc/passwd"})
    assert r.status_code == 422


def test_capabilities_endpoint(ipp_client: TestClient) -> None:
    with FakeIppServer() as srv:
        r = ipp_client.post("/api/v1/printers/ipp-capabilities", json={"uri": srv.uri})
    assert r.status_code == 200, r.text
    caps = r.json()
    assert {p["paper_id"] for p in caps["papers"]} == {"A4", "LETTER"}
    assert caps["accepts_pdf"] is True
    assert caps["default_margins_mm"]["bottom"] == 12.0


def test_capabilities_errors(ipp_client: TestClient) -> None:
    down = ipp_client.post(
        "/api/v1/printers/ipp-capabilities", json={"uri": "ipp://127.0.0.1:9/ipp/print"}
    )
    assert down.status_code == 502
    assert down.json()["code"] == "PRINTER_UNREACHABLE"
    bad = ipp_client.post("/api/v1/printers/ipp-capabilities", json={"uri": "gopher://x"})
    assert bad.status_code == 422


def test_discovery_without_multicast_is_501_or_a_list(ipp_client: TestClient) -> None:
    r = ipp_client.get("/api/v1/printers/discover")
    # In CI there is no mDNS: either the package is missing / the socket fails (501 with an
    # explanation) or the browse simply finds nothing.
    if r.status_code == 501:
        assert r.json()["code"] == "DISCOVERY_UNAVAILABLE"
        assert "host" in r.json()["detail"]
    else:
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_discovery_route_is_not_shadowed_by_printer_id(ipp_client: TestClient) -> None:
    assert ipp_client.get("/api/v1/printers/discover").status_code != 404


def test_everything_is_gated_by_enable_ipp(client: TestClient) -> None:
    caps = client.post("/api/v1/printers/ipp-capabilities", json={"uri": "ipp://x/y"})
    assert caps.status_code == 501
    assert caps.json()["code"] == "IPP_DISABLED"
    assert client.get("/api/v1/printers/discover").status_code == 501
    job = client.post(
        "/api/v1/jobs/00000000-0000-0000-0000-000000000000/print", json={"printer_id": "x"}
    )
    assert job.status_code == 501


def test_capabilities_errors_are_generic(ipp_client: TestClient) -> None:
    bodies = []
    for uri in [
        "ipp://127.0.0.1:9/ipp/print",  # connection refused (loopback is allowed by the test env)
        "http://169.254.169.254/latest/meta-data",  # blocked by the SSRF policy
    ]:
        r = ipp_client.post("/api/v1/printers/ipp-capabilities", json={"uri": uri})
        assert r.status_code >= 400
        bodies.append(r.json())
    assert bodies[0]["code"] == bodies[1]["code"] == "PRINTER_UNREACHABLE"
    assert bodies[0]["detail"] == bodies[1]["detail"]


def test_saving_a_printer_with_a_metadata_uri_is_rejected(ipp_client: TestClient) -> None:
    r = ipp_client.post(
        "/api/v1/printers", json={"name": "x", "ipp_uri": "http://169.254.169.254/latest"}
    )
    assert r.status_code == 422
    r = ipp_client.post(
        "/api/v1/printers", json={"name": "x", "ipp_uri": "http://192.168.1.50/ipp"}
    )
    assert r.status_code == 201
