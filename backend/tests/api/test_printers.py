from typing import Any

from fastapi.testclient import TestClient

from a0td.domain.settings import JobSettings
from a0td.storage import repo

BASE = "/api/v1/printers"


def _body(**kw: object) -> dict[str, object]:
    return {
        "name": "Mi Epson",
        "kind": "inkjet",
        "default_margins": {"top": 3, "right": 3, "bottom": 5, "left": 3},
        "paper_margins": {"LETTER": {"top": 4, "right": 4, "bottom": 6, "left": 4}},
        "supported_papers": ["A4", "LETTER"],
        "borderless": True,
        "color": False,
        **kw,
    }


def _cal(**kw: object) -> dict[str, object]:
    return {
        "paper": "A4",
        "measured_x_mm": 179.1,
        "measured_y_mm": 260.0,
        "nominal_x_mm": 180,
        "nominal_y_mm": 260,
        "margins_mm": {"top": 3, "right": 3.5, "bottom": 4, "left": 3},
        **kw,
    }


def test_crud_roundtrip(client: TestClient) -> None:
    res = client.post(BASE, json=_body())
    assert res.status_code == 201, res.text
    p = res.json()
    assert p["id"].startswith("user-")
    assert p["builtin"] is False
    assert p["borderless"] is True
    assert p["color"] is False
    assert p["supported_papers"] == ["A4", "LETTER"]
    assert p["paper_margins"]["LETTER"]["bottom"] == 6

    assert p["id"] in {x["id"] for x in client.get(BASE).json()}
    assert client.get(f"{BASE}/{p['id']}").json() == p

    upd = client.put(f"{BASE}/{p['id']}", json=_body(name="Renombrada", borderless=False))
    assert upd.status_code == 200
    assert upd.json()["name"] == "Renombrada"
    assert upd.json()["borderless"] is False

    assert client.delete(f"{BASE}/{p['id']}").status_code == 204
    assert client.get(f"{BASE}/{p['id']}").status_code == 404
    assert client.delete(f"{BASE}/{p['id']}").status_code == 404


def test_builtin_is_read_only(client: TestClient) -> None:
    for res in (
        client.put(f"{BASE}/builtin-laser", json=_body()),
        client.delete(f"{BASE}/builtin-laser"),
    ):
        assert res.status_code == 403
        assert res.json()["code"] == "PRINTER_READONLY"
    assert client.get(f"{BASE}/builtin-laser").status_code == 200


def test_validation(client: TestClient) -> None:
    assert client.post(BASE, json=_body(name="")).status_code == 422
    assert client.post(BASE, json=_body(scale_kx=1.5)).status_code == 422


def test_calibration_pdf(client: TestClient) -> None:
    res = client.get(f"{BASE}/builtin-generic/calibration.pdf", params={"paper": "LETTER"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    assert client.get(f"{BASE}/nope/calibration.pdf").status_code == 404
    assert client.get(f"{BASE}/builtin-generic/calibration.pdf?paper=ZZ").status_code == 422
    assert client.get(f"{BASE}/builtin-generic/calibration.pdf?paper=CUSTOM").status_code == 422


def test_calibration_saved(client: TestClient) -> None:
    pid = client.post(BASE, json=_body(supported_papers=None)).json()["id"]
    res = client.post(f"{BASE}/{pid}/calibration", json=_cal())
    assert res.status_code == 200, res.text
    p = res.json()
    assert abs(p["scale_kx"] - 179.1 / 180) < 1e-4
    assert p["scale_ky"] == 1.0
    assert p["paper_margins"]["A4"] == {"top": 3.5, "right": 4.0, "bottom": 4.5, "left": 3.5}
    assert p["paper_margins"]["LETTER"]["top"] == 4  # other papers untouched
    again = client.get(f"{BASE}/{pid}").json()
    assert again == p
    assert len(again["supported_papers"]) > 2  # calibrating did not narrow the paper list


def test_calibration_out_of_range_not_saved(client: TestClient) -> None:
    pid = client.post(BASE, json=_body()).json()["id"]
    res = client.post(f"{BASE}/{pid}/calibration", json=_cal(measured_x_mm=170.0))
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "CALIBRATION_OUT_OF_RANGE"
    assert body["params"]["min"] == 0.97
    assert client.get(f"{BASE}/{pid}").json()["scale_kx"] == 1.0


def test_calibration_nominal_must_match_ruler(client: TestClient) -> None:
    pid = client.post(BASE, json=_body()).json()["id"]
    res = client.post(
        f"{BASE}/{pid}/calibration",
        json=_cal(measured_x_mm=179.1, nominal_x_mm=179.1),
    )
    assert res.status_code == 422
    assert res.json()["code"] == "INVALID_SETTINGS"
    assert client.get(f"{BASE}/{pid}").json()["scale_kx"] == 1.0


def test_calibration_of_builtin_rejected(client: TestClient) -> None:
    res = client.post(f"{BASE}/builtin-laser/calibration", json=_cal())
    assert res.status_code == 403


def test_job_spec_carries_the_stored_profile(client: TestClient) -> None:
    """jobs -> worker spec -> render_job(printer=...): the calibrated user profile is passed."""
    pid = client.post(BASE, json=_body()).json()["id"]
    client.post(f"{BASE}/{pid}/calibration", json=_cal())
    svc: Any = client.app.state.services  # type: ignore[attr-defined]
    settings = JobSettings.model_validate({"target": {"printer_id": pid}})
    with svc.db.session() as s:
        repo.create_document(
            s, svc.config, doc_id="a" * 32, name="x.pdf", size_bytes=1, sha256="0" * 64
        )
        s.flush()
        job = repo.create_job(
            s,
            svc.config,
            job_id="b" * 32,
            document_id="a" * 32,
            kind="render",
            settings=settings.model_dump(mode="json"),
        )
        spec = svc.manager._build_spec(job, s)
    assert spec["printer"]["id"] == pid
    assert abs(spec["printer"]["scale_kx"] - 179.1 / 180) < 1e-4
    assert spec["printer"]["paper_margins"]["A4"]["top"] == 3.5
