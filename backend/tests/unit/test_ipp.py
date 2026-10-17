"""IPP codec, capabilities parsing and the client against a fake printer."""

import struct
import sys
from pathlib import Path

import pymupdf
import pytest

from a0td.domain.errors import DomainError, ErrorCode
from a0td.printing import ipp
from a0td.printing.submit import submit_pdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from fake_ipp import FakeIppServer


def test_encode_request_layout() -> None:
    data = ipp.encode_request(
        ipp.PRINT_JOB,
        7,
        [
            (ipp.TAG_OPERATION, [ipp.Attr.of(ipp.T_CHARSET, "attributes-charset", "utf-8")]),
            (ipp.TAG_JOB, [ipp.Attr.of(ipp.T_INTEGER, "copies", 3)]),
        ],
        b"DOC",
    )
    assert data[:8] == struct.pack(">BBHI", 2, 0, ipp.PRINT_JOB, 7)
    msg, offset = ipp.parse_message(data)
    assert data[offset:] == b"DOC"
    assert msg.group(ipp.TAG_OPERATION)["attributes-charset"] == ["utf-8"]
    assert msg.group(ipp.TAG_JOB)["copies"] == [3]


def test_multi_value_and_range_roundtrip() -> None:
    attrs = ipp.job_template(
        media="iso_a4_210x297mm", copies=2, monochrome=True, pages=[1, 2, 3, 7]
    )
    msg, _ = ipp.parse_message(ipp.encode_request(ipp.PRINT_JOB, 1, [(ipp.TAG_JOB, attrs)]))
    job = msg.group(ipp.TAG_JOB)
    assert job["media"] == ["iso_a4_210x297mm"]
    assert job["print-scaling"] == ["none"]
    assert job["sides"] == ["one-sided"]
    assert job["print-color-mode"] == ["monochrome"]
    assert job["copies"] == [2]
    assert job["page-ranges"] == [(1, 3), (7, 7)]


def test_page_ranges_compaction() -> None:
    assert ipp.page_ranges([5, 3, 4, 9, 3]) == [(3, 5), (9, 9)]
    assert ipp.page_ranges([]) == []


def test_truncated_message_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"short|truncated"):
        ipp.decode_response(b"\x02\x00\x00\x00")


def test_normalize_uri() -> None:
    assert ipp.normalize_uri("ipp://printer.local/ipp/print") == (
        "http://printer.local:631/ipp/print",
        "ipp://printer.local:631/ipp/print",
    )
    http_url, printer_uri = ipp.normalize_uri("ipps://[fe80::1]:443/p?x=1")
    assert http_url == "https://[fe80::1]:443/p?x=1"
    assert printer_uri == "ipps://[fe80::1]:443/p"
    for bad in ("ftp://x/y", "ipp:///nohost", "printer.local", "file:///etc/passwd"):
        with pytest.raises(ipp.IppError):
            ipp.normalize_uri(bad)


def test_capabilities_from_fake_printer() -> None:
    with FakeIppServer() as srv:
        client = ipp.IppClient(srv.uri)
        attrs = client.get_printer_attributes(ipp.wanted_capability_attributes())
        caps = ipp.capabilities(attrs, client.printer_uri)
    assert caps["name"] == "Fake Inc. Model 1"
    assert caps["accepts_pdf"] is True
    assert caps["color"] is True
    papers = {p["paper_id"]: p for p in caps["papers"]}
    assert set(papers) == {"A4", "LETTER"}
    # the borderless entry (0) does not hide the real limit of the bordered one
    assert papers["A4"]["margins_mm"] == {"top": 4.23, "right": 4.23, "bottom": 4.23, "left": 4.23}
    assert papers["A4"]["media"] == "iso_a4_210x297mm"
    assert papers["LETTER"]["margins_mm"] == {
        "top": 4.23,
        "right": 3.0,
        "bottom": 12.0,
        "left": 3.0,
    }
    assert caps["default_margins_mm"] == {"top": 4.23, "right": 4.23, "bottom": 12.0, "left": 4.23}


def test_capabilities_fall_back_to_media_supported_names() -> None:
    attrs = {
        "media-supported": ["iso_a3_297x420mm", "na_letter_8.5x11in", "om_small-photo_100x150mm"]
    }
    caps = ipp.capabilities(attrs, "ipp://x/ipp")
    assert [p["paper_id"] for p in caps["papers"]] == ["A3", "LETTER"]
    assert all(p["margins_mm"] is None for p in caps["papers"])
    assert caps["default_margins_mm"] is None


def test_unreachable_printer() -> None:
    client = ipp.IppClient("ipp://127.0.0.1:9/ipp/print", timeout=1)
    with pytest.raises(ipp.IppError) as exc:
        client.get_printer_attributes()
    assert exc.value.reason == ipp.UNREACHABLE


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sheets.pdf"
    with pymupdf.open() as doc:
        for i in range(4):
            doc.new_page(width=595, height=842).insert_text((72, 72), f"sheet {i + 1}")
        doc.save(path)
    return path


def _submit(srv: FakeIppServer, pdf: Path, tmp_path: Path, **kw: object) -> object:
    client = ipp.IppClient(srv.uri, timeout=5)
    params: dict[str, object] = {
        "media": "iso_a4_210x297mm",
        "copies": 2,
        "monochrome": False,
        "pages": [2, 3],
        "job_name": "demo",
        "poll_interval_s": 0.01,
    }
    params.update(kw)
    return submit_pdf(client, pdf, scratch=tmp_path, **params)  # type: ignore[arg-type]


def test_submit_pdf_sends_attributes_and_waits(pdf: Path, tmp_path: Path) -> None:
    with FakeIppServer(state_sequence=[ipp.JOB_PROCESSING, ipp.JOB_COMPLETED]) as srv:
        outcome = _submit(srv, pdf, tmp_path)
        sent = next(r for r in srv.requests if r.operation == ipp.PRINT_JOB)
        polls = [r for r in srv.requests if r.operation == ipp.GET_JOB_ATTRIBUTES]
    assert outcome.state == "completed"  # type: ignore[attr-defined]
    assert outcome.job_id == 41  # type: ignore[attr-defined]
    job = sent.attrs(ipp.TAG_JOB)
    assert job["media"] == ["iso_a4_210x297mm"]
    assert job["print-scaling"] == ["none"]
    assert job["copies"] == [2]
    assert job["page-ranges"] == [(2, 3)]
    ops = sent.attrs(ipp.TAG_OPERATION)
    assert ops["document-format"] == ["application/pdf"]
    assert ops["job-name"] == ["demo"]
    assert sent.document == pdf.read_bytes()  # the printer handles page-ranges
    assert len(polls) >= 1


def test_submit_selects_pages_when_printer_lacks_page_ranges(pdf: Path, tmp_path: Path) -> None:
    with FakeIppServer(page_ranges=False) as srv:
        _submit(srv, pdf, tmp_path)
        sent = next(r for r in srv.requests if r.operation == ipp.PRINT_JOB)
    assert "page-ranges" not in sent.attrs(ipp.TAG_JOB)
    with pymupdf.open(stream=sent.document, filetype="pdf") as doc:
        assert doc.page_count == 2


def test_submit_converts_to_pwg_raster(pdf: Path, tmp_path: Path) -> None:
    with FakeIppServer(formats=("image/pwg-raster", "image/jpeg")) as srv:
        _submit(srv, pdf, tmp_path, monochrome=True)
        sent = next(r for r in srv.requests if r.operation == ipp.PRINT_JOB)
    assert sent.attrs(ipp.TAG_OPERATION)["document-format"] == ["image/pwg-raster"]
    assert sent.document.startswith(b"RaS2")  # PWG Raster sync word
    assert "page-ranges" not in sent.attrs(ipp.TAG_JOB)  # pages were selected before converting
    assert sent.attrs(ipp.TAG_JOB)["print-color-mode"] == ["monochrome"]


def test_submit_rejects_printer_without_usable_format(pdf: Path, tmp_path: Path) -> None:
    with FakeIppServer(formats=("image/jpeg",)) as srv, pytest.raises(DomainError) as exc:
        _submit(srv, pdf, tmp_path)
    assert exc.value.code is ErrorCode.PRINT_REJECTED


def test_submit_reports_aborted_job(pdf: Path, tmp_path: Path) -> None:
    with FakeIppServer(state_sequence=[ipp.JOB_ABORTED]) as srv, pytest.raises(DomainError) as exc:
        _submit(srv, pdf, tmp_path)
    assert exc.value.code is ErrorCode.PRINT_REJECTED


def test_submit_reports_print_job_error_status(pdf: Path, tmp_path: Path) -> None:
    with FakeIppServer(print_status=0x040A) as srv, pytest.raises(DomainError) as exc:
        _submit(srv, pdf, tmp_path)
    assert exc.value.code is ErrorCode.PRINT_REJECTED
    assert exc.value.params["ipp_status"] == 0x040A


def test_submit_unreachable(pdf: Path, tmp_path: Path) -> None:
    client = ipp.IppClient("ipp://127.0.0.1:9/ipp/print", timeout=1)
    with pytest.raises(DomainError) as exc:
        submit_pdf(
            client,
            pdf,
            scratch=tmp_path,
            media="iso_a4_210x297mm",
            copies=1,
            monochrome=False,
            pages=None,
            job_name="x",
        )
    assert exc.value.code is ErrorCode.PRINTER_UNREACHABLE


def test_submit_pending_job_times_out_as_unknown_status(pdf: Path, tmp_path: Path) -> None:
    with FakeIppServer(state_sequence=[ipp.JOB_PROCESSING]) as srv:
        outcome = _submit(srv, pdf, tmp_path, poll_timeout_s=0.1)
    assert outcome.state == "unknown"  # type: ignore[attr-defined]
    assert outcome.warnings[0]["code"] == "PRINT_STATUS_UNKNOWN"  # type: ignore[attr-defined]
