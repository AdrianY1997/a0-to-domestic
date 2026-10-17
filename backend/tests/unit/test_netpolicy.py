"""SSRF policy of printer hosts, pinned connection, response cap, streaming, job-state errors."""

import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from a0td.domain.errors import DomainError, ErrorCode
from a0td.printing import ipp, netpolicy
from a0td.printing.submit import submit_pdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from fake_ipp import FakeIppServer


@pytest.fixture(autouse=True)
def _strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A0TD_IPP_ALLOW_HOSTS", raising=False)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.5.5.5",
        "::1",
        "169.254.169.254",
        "169.254.1.1",
        "fe80::1",
        "fd00:ec2::254",
        "0.0.0.0",
        "::",
        "224.0.0.1",
        "ff02::1",
        "255.255.255.255",
        "::ffff:127.0.0.1",
    ],
)
def test_forbidden_addresses_are_rejected(host: str) -> None:
    with pytest.raises(netpolicy.HostNotAllowedError):
        netpolicy.resolve_allowed(host, 631)


@pytest.mark.parametrize(
    "host", ["10.1.2.3", "192.168.1.50", "172.16.0.9", "fd12:3456::1", "8.8.8.8"]
)
def test_lan_and_public_addresses_are_allowed(host: str) -> None:
    assert netpolicy.resolve_allowed(host, 631) == host


def test_allow_list_overrides_by_ip_and_by_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A0TD_IPP_ALLOW_HOSTS", " 127.0.0.1 , localhost")
    assert netpolicy.resolve_allowed("127.0.0.1", 631) == "127.0.0.1"
    assert netpolicy.resolve_allowed("localhost", 631) in {"127.0.0.1", "::1"}
    with pytest.raises(netpolicy.HostNotAllowedError):
        netpolicy.resolve_allowed("169.254.169.254", 631)


def test_name_resolving_to_a_forbidden_ip_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(host: str, port: int, **_: Any) -> list[Any]:
        return [
            (socket.AF_INET, 0, 0, "", ("10.0.0.5", port)),
            (socket.AF_INET, 0, 0, "", ("169.254.169.254", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(netpolicy.HostNotAllowedError):
        netpolicy.resolve_allowed("printer.example", 631)


def test_unresolvable_host_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any, **__: Any) -> list[Any]:
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(netpolicy.HostNotAllowedError) as exc:
        netpolicy.resolve_allowed("gone.example", 631)
    assert exc.value.unresolved


def test_default_ports_by_scheme() -> None:
    for uri, port in [
        ("ipp://p.example/x", 631),
        ("ipps://p.example/x", 631),
        ("http://p.example/x", 80),
        ("https://p.example/x", 443),
    ]:
        assert f":{port}/" in ipp.normalize_uri(uri)[0]
    with pytest.raises(ipp.IppError):
        ipp.normalize_uri("file:///etc/passwd")
    with pytest.raises(ipp.IppError):
        ipp.normalize_uri("ftp://p.example/")


def test_client_refuses_metadata_host() -> None:
    with pytest.raises(ipp.IppError) as exc:
        ipp.IppClient("http://169.254.169.254/latest/meta-data")
    assert exc.value.reason == ipp.UNREACHABLE


def test_connection_is_pinned_to_the_checked_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    real_lookup = socket.getaddrinfo

    def first(host: str, port: int, **_: Any) -> list[Any]:
        return [(socket.AF_INET, 0, 0, "", ("127.0.0.1", port))]

    monkeypatch.setenv("A0TD_IPP_ALLOW_HOSTS", "127.0.0.1")
    monkeypatch.setattr(socket, "getaddrinfo", first)
    client = ipp.IppClient("ipp://rebind.example:9/ipp/print", timeout=1)
    assert client.pinned_ip == "127.0.0.1"

    def no_second_lookup(host: str, *a: Any, **k: Any) -> list[Any]:
        assert host == "127.0.0.1", "the host name was resolved twice"  # numeric: no DNS
        return real_lookup(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", no_second_lookup)
    seen: list[Any] = []
    real = socket.create_connection

    def spy(addr: tuple[str, int], *a: Any, **k: Any) -> socket.socket:
        seen.append(addr)
        return real(addr, *a, **k)

    monkeypatch.setattr(socket, "create_connection", spy)
    with pytest.raises(ipp.IppError):
        client.get_printer_attributes()
    assert seen == [("127.0.0.1", 9)]


def test_tls_is_verified_by_default_with_env_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A0TD_IPP_ALLOW_HOSTS", "127.0.0.1")
    assert ipp.IppClient("ipps://127.0.0.1/x").verify_tls is True
    monkeypatch.setenv("A0TD_IPP_INSECURE_TLS", "true")
    assert ipp.IppClient("ipps://127.0.0.1/x").verify_tls is False


class _Server:
    def __init__(self, reply: bytes) -> None:
        outer = self
        self.bodies: list[bytes] = []
        self.lengths: list[str | None] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                n = int(self.headers.get("Content-Length", "0"))
                outer.lengths.append(self.headers.get("Content-Length"))
                outer.bodies.append(self.rfile.read(n))
                self.send_response(200)
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *a: object) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def uri(self) -> str:
        return f"ipp://127.0.0.1:{self.srv.server_address[1]}/"

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def test_oversized_response_is_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A0TD_IPP_ALLOW_HOSTS", "127.0.0.1")
    monkeypatch.setattr(ipp, "MAX_RESPONSE_BYTES", 1000)
    s = _Server(b"\x00" * 5000)
    try:
        with pytest.raises(ipp.IppError, match="too large"):
            ipp.IppClient(s.uri).get_printer_attributes()
    finally:
        s.close()


def test_file_payload_is_streamed_with_content_length(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("A0TD_IPP_ALLOW_HOSTS", "127.0.0.1")
    monkeypatch.setattr(ipp, "STREAM_CHUNK", 1000)
    doc = tmp_path / "d.pdf"
    doc.write_bytes(b"%PDF" + bytes(range(256)) * 40)
    reply = ipp.encode_request(0, 1, [(ipp.TAG_JOB, [ipp.Attr.of(ipp.T_INTEGER, "job-id", 5)])])
    s = _Server(reply)
    try:
        res = ipp.IppClient(s.uri).print_job(
            doc, document_format="application/pdf", job_name="j", job_attrs=[]
        )
    finally:
        s.close()
    assert res.job_id == 5
    msg, off = ipp.parse_message(s.bodies[0])
    assert msg.status == ipp.PRINT_JOB
    assert s.bodies[0][off:] == doc.read_bytes()
    assert s.lengths[0] == str(len(s.bodies[0]))


def test_aborted_job_reports_job_state_not_ipp_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("A0TD_IPP_ALLOW_HOSTS", "127.0.0.1")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with FakeIppServer(state_sequence=[ipp.JOB_ABORTED]) as srv, pytest.raises(DomainError) as e:
        submit_pdf(
            ipp.IppClient(srv.uri),
            pdf,
            scratch=tmp_path,
            media="iso_a4_210x297mm",
            copies=1,
            monochrome=False,
            pages=None,
            job_name="j",
            poll_interval_s=0.01,
        )
    assert e.value.code is ErrorCode.PRINT_REJECTED
    assert e.value.params["job_state"] == ipp.JOB_ABORTED
    assert e.value.params["ipp_status"] is None
