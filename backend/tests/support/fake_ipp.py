"""A fake IPP printer for tests: ``http.server`` speaking just enough IPP/2.0 (no real printers).

``FakeIppServer`` answers Get-Printer-Attributes, Print-Job, Get-Job-Attributes and Cancel-Job.
Everything it receives is kept in ``requests`` (operation, parsed groups, document bytes).
"""

import struct
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from a0td.printing import ipp

OK = 0x0000
CLIENT_ERROR_DOC_FORMAT = 0x040A


def _attr(tag: int, name: str, payload: bytes, first: bool = True) -> bytes:
    n = name.encode() if first else b""
    return struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(payload)) + payload


def _int(name: str, value: int, tag: int = ipp.T_INTEGER) -> bytes:
    return _attr(tag, name, struct.pack(">i", value))


def _text(tag: int, name: str, *values: str) -> bytes:
    return b"".join(_attr(tag, name, v.encode(), i == 0) for i, v in enumerate(values))


def _member(name: str, inner: bytes) -> bytes:
    return _attr(ipp.T_MEMBER_NAME, "", name.encode()) + inner


def _val_int(value: int) -> bytes:
    return _attr(ipp.T_INTEGER, "", struct.pack(">i", value), first=False)


def media_col(
    x_mm: float, y_mm: float, margins_mm: tuple[float, float, float, float] | None
) -> bytes:
    """One ``media-col-database`` value (additional value of a 1setOf collection)."""
    size = (
        _attr(ipp.T_BEG_COLLECTION, "", b"", first=False)
        + _member("x-dimension", _val_int(round(x_mm * 100)))
        + _member("y-dimension", _val_int(round(y_mm * 100)))
        + _attr(ipp.T_END_COLLECTION, "", b"", first=False)
    )
    body = _attr(ipp.T_BEG_COLLECTION, "", b"", first=False) + _member("media-size", size)
    if margins_mm is not None:
        top, right, bottom, left = margins_mm
        for key, v in (
            ("media-bottom-margin", bottom),
            ("media-left-margin", left),
            ("media-right-margin", right),
            ("media-top-margin", top),
        ):
            body += _member(key, _val_int(round(v * 100)))
    return body + _attr(ipp.T_END_COLLECTION, "", b"", first=False)


@dataclass
class Received:
    operation: int
    groups: list[tuple[int, dict[str, list[Any]]]]
    document: bytes

    def attrs(self, tag: int) -> dict[str, list[Any]]:
        merged: dict[str, list[Any]] = {}
        for t, a in self.groups:
            if t == tag:
                merged.update(a)
        return merged


@dataclass
class FakeIppServer:
    formats: tuple[str, ...] = ("application/pdf", "image/pwg-raster", "image/jpeg")
    state_sequence: list[int] = field(default_factory=lambda: [ipp.JOB_COMPLETED])
    page_ranges: bool = True
    job_id: int = 41
    print_status: int = OK
    media: list[tuple[float, float, tuple[float, float, float, float] | None]] = field(
        default_factory=lambda: [
            (210, 297, (4.23, 4.23, 4.23, 4.23)),
            (210, 297, (0, 0, 0, 0)),  # borderless entry of the same size
            (215.9, 279.4, (4.23, 3.0, 12.0, 3.0)),
        ]
    )
    requests: list[Received] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None
    _polls: int = 0

    # -- lifecycle
    @property
    def uri(self) -> str:
        assert self._server is not None
        return f"ipp://127.0.0.1:{self._server.server_address[1]}/ipp/print"

    def __enter__(self) -> "FakeIppServer":
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                reply = fake.handle(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/ipp")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()

    # -- protocol
    def handle(self, body: bytes) -> bytes:
        msg, offset = ipp.parse_message(body)
        self.requests.append(Received(msg.status, msg.groups, body[offset:]))
        rid = msg.request_id
        op = msg.status
        if op == ipp.GET_PRINTER_ATTRIBUTES:
            return self._reply(rid, OK, ipp.TAG_PRINTER, self._printer_attributes())
        if op == ipp.PRINT_JOB:
            status = self.print_status
            job = _int("job-id", self.job_id) + _text(ipp.T_URI, "job-uri", "ipp://x/jobs/41")
            job += _int("job-state", ipp.JOB_PENDING, ipp.T_ENUM)
            return self._reply(rid, status, ipp.TAG_JOB, job if status < 0x100 else b"")
        if op == ipp.GET_JOB_ATTRIBUTES:
            seq = self.state_sequence
            state = seq[min(self._polls, len(seq) - 1)]
            self._polls += 1
            job = _int("job-state", state, ipp.T_ENUM)
            job += _text(ipp.T_KEYWORD, "job-state-reasons", "none")
            return self._reply(rid, OK, ipp.TAG_JOB, job)
        if op == ipp.CANCEL_JOB:
            return self._reply(rid, OK, ipp.TAG_JOB, b"")
        return self._reply(rid, 0x0501, ipp.TAG_JOB, b"")  # server-error-operation-not-supported

    def _printer_attributes(self) -> bytes:
        out = _text(ipp.T_NAME, "printer-name", "Fake Printer")
        out += _text(ipp.T_TEXT, "printer-make-and-model", "Fake Inc. Model 1")
        out += _text(ipp.T_MIME, "document-format-supported", *self.formats)
        out += _attr(ipp.T_BOOLEAN, "color-supported", b"\x01")
        out += _text(ipp.T_KEYWORD, "print-color-mode-supported", "color", "monochrome")
        out += _attr(
            ipp.T_BOOLEAN, "page-ranges-supported", b"\x01" if self.page_ranges else b"\x00"
        )
        for i, (x, y, margins) in enumerate(self.media):
            value = media_col(x, y, margins)
            if i == 0:
                out += _attr(ipp.T_BEG_COLLECTION, "media-col-database", b"")
                # value of the first collection: re-use the helper without its begin tag
                out += value[len(_attr(ipp.T_BEG_COLLECTION, "", b"", first=False)) :]
            else:
                out += value
        out += _attr(
            ipp.T_RESOLUTION,
            "pwg-raster-document-resolution-supported",
            struct.pack(">iib", 300, 300, 3),
        )
        return out

    @staticmethod
    def _reply(request_id: int, status: int, group: int, attrs: bytes) -> bytes:
        head = struct.pack(">BBHI", 2, 0, status, request_id)
        ops = (
            bytes([ipp.TAG_OPERATION])
            + _text(ipp.T_CHARSET, "attributes-charset", "utf-8")
            + _text(ipp.T_LANGUAGE, "attributes-natural-language", "en")
        )
        return head + ops + bytes([group]) + attrs + bytes([ipp.TAG_END])
