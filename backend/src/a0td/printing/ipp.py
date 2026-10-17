"""Minimal IPP client (SPEC IMP-03, PAP-07): RFC 8010 encoding + RFC 8011 operations.

Design decision: a small pure-Python encoder / decoder instead of ``ipptool`` (cups-ipp-utils).
It adds no package to the image, works the same on amd64 and arm64, needs no subprocess and
parses collections (``media-col-database``) directly. Only the operations the app needs are
implemented: Print-Job, Get-Printer-Attributes, Get-Job-Attributes and Cancel-Job.
"""

import http.client
import logging
import os
import re
import socket
import ssl
import struct
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from a0td.domain.papers import PAPERS, PaperId, match_standard
from a0td.printing.netpolicy import HostNotAllowedError, resolve_allowed

log = logging.getLogger(__name__)

IPP_VERSION = (2, 0)
DEFAULT_PORTS = {"ipp": 631, "ipps": 631, "http": 80, "https": 443}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
STREAM_CHUNK = 256 * 1024
INSECURE_TLS_ENV = "A0TD_IPP_INSECURE_TLS"

# Operations
PRINT_JOB = 0x0002
CANCEL_JOB = 0x0008
GET_JOB_ATTRIBUTES = 0x0009
GET_PRINTER_ATTRIBUTES = 0x000B

# Delimiter tags
TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03
TAG_PRINTER = 0x04
TAG_UNSUPPORTED = 0x05

# Value tags
T_INTEGER = 0x21
T_BOOLEAN = 0x22
T_ENUM = 0x23
T_OCTET = 0x30
T_RESOLUTION = 0x32
T_RANGE = 0x33
T_BEG_COLLECTION = 0x34
T_TEXT_LANG = 0x35
T_NAME_LANG = 0x36
T_END_COLLECTION = 0x37
T_TEXT = 0x41
T_NAME = 0x42
T_KEYWORD = 0x44
T_URI = 0x45
T_URI_SCHEME = 0x46
T_CHARSET = 0x47
T_LANGUAGE = 0x48
T_MIME = 0x49
T_MEMBER_NAME = 0x4A

_STRING_TAGS = {T_TEXT, T_NAME, T_KEYWORD, T_URI, T_URI_SCHEME, T_CHARSET, T_LANGUAGE, T_MIME}

# job-state enum values
JOB_PENDING, JOB_HELD, JOB_PROCESSING, JOB_STOPPED = 3, 4, 5, 6
JOB_CANCELED, JOB_ABORTED, JOB_COMPLETED = 7, 8, 9
JOB_STATE_NAMES = {
    3: "pending",
    4: "pending-held",
    5: "processing",
    6: "processing-stopped",
    7: "canceled",
    8: "aborted",
    9: "completed",
}

# Reasons for IppError.reason
UNREACHABLE = "unreachable"
REJECTED = "rejected"


class IppError(Exception):
    """Transport failure (``reason == "unreachable"``) or a negative IPP status (``rejected``)."""

    def __init__(
        self,
        reason: str,
        detail: str,
        status: int | None = None,
        job_state: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail  # for the logs: never show it to clients
        self.status = status  # IPP status code of the response
        self.job_state = job_state  # job-state enum when the job ended badly


# ------------------------------------------------------------------------------ encoding


@dataclass(frozen=True, slots=True)
class Attr:
    """One attribute to send: a value tag, a name and one or more values."""

    tag: int
    name: str
    values: tuple[Any, ...]

    @classmethod
    def of(cls, tag: int, name: str, *values: Any) -> "Attr":
        return cls(tag, name, values)


def _encode_value(tag: int, value: Any) -> bytes:
    if tag in (T_INTEGER, T_ENUM):
        return struct.pack(">i", int(value))
    if tag == T_BOOLEAN:
        return b"\x01" if value else b"\x00"
    if tag == T_RANGE:
        lo, hi = value
        return struct.pack(">ii", int(lo), int(hi))
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


def encode_attrs(attrs: Iterable[Attr]) -> bytes:
    out = bytearray()
    for attr in attrs:
        name = attr.name.encode("ascii")
        for i, value in enumerate(attr.values):
            payload = _encode_value(attr.tag, value)
            out += struct.pack(">B", attr.tag)
            out += struct.pack(">H", len(name) if i == 0 else 0) + (name if i == 0 else b"")
            out += struct.pack(">H", len(payload)) + payload
    return bytes(out)


def encode_header(
    operation: int,
    request_id: int,
    groups: Sequence[tuple[int, Sequence[Attr]]],
) -> bytes:
    """Version, operation, attribute groups and end tag (without the document data)."""
    out = bytearray(struct.pack(">BBHI", *IPP_VERSION, operation, request_id))
    for tag, attrs in groups:
        out += struct.pack(">B", tag) + encode_attrs(attrs)
    out += struct.pack(">B", TAG_END)
    return bytes(out)


def encode_request(
    operation: int,
    request_id: int,
    groups: Sequence[tuple[int, Sequence[Attr]]],
    payload: bytes = b"",
) -> bytes:
    """Header + attribute groups + end tag (+ the document data for Print-Job)."""
    return encode_header(operation, request_id, groups) + payload


# ------------------------------------------------------------------------------ decoding

Collection = dict[str, list[Any]]


@dataclass(slots=True)
class IppResponse:
    status: int
    request_id: int
    groups: list[tuple[int, dict[str, list[Any]]]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status < 0x0100

    def group(self, tag: int) -> dict[str, list[Any]]:
        """First group with ``tag`` merged (empty when absent)."""
        merged: dict[str, list[Any]] = {}
        for t, attrs in self.groups:
            if t == tag:
                merged.update(attrs)
        return merged

    def all_groups(self, tag: int) -> list[dict[str, list[Any]]]:
        return [attrs for t, attrs in self.groups if t == tag]


class _Reader:
    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError("truncated IPP message")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int(struct.unpack(">H", self.take(2))[0])

    def peek(self) -> int:
        if self.pos >= len(self.data):
            raise ValueError("truncated IPP message")
        return self.data[self.pos]


def _decode_value(tag: int, raw: bytes) -> Any:
    if tag in (T_INTEGER, T_ENUM) and len(raw) == 4:
        return int(struct.unpack(">i", raw)[0])
    if tag == T_BOOLEAN and len(raw) == 1:
        return raw != b"\x00"
    if tag == T_RANGE and len(raw) == 8:
        lo, hi = struct.unpack(">ii", raw)
        return (int(lo), int(hi))
    if tag == T_RESOLUTION and len(raw) == 9:
        x, y, unit = struct.unpack(">iib", raw)
        return (int(x), int(y), int(unit))
    if tag in _STRING_TAGS or tag in (T_TEXT_LANG, T_NAME_LANG):
        return raw.decode("utf-8", "replace")
    return raw


def _read_collection(r: _Reader) -> Collection:
    """Members of a collection; the begCollection value has already been consumed."""
    members: Collection = {}
    current: str | None = None
    while True:
        tag = r.u8()
        name_len = r.u16()
        name = r.take(name_len)
        raw = r.take(r.u16())
        if tag == T_END_COLLECTION:
            return members
        if tag == T_MEMBER_NAME:
            current = raw.decode("utf-8", "replace")
            members.setdefault(current, [])
            continue
        if current is None or name:
            raise ValueError("malformed IPP collection")
        if tag == T_BEG_COLLECTION:
            members[current].append(_read_collection(r))
        else:
            members[current].append(_decode_value(tag, raw))


def decode_response(data: bytes) -> IppResponse:
    """Parse an IPP response. Raises ``ValueError`` on a malformed message."""
    return parse_message(data)[0]


def parse_message(data: bytes) -> tuple[IppResponse, int]:
    """Parse any IPP message; returns it and the offset where the document data starts.

    For a request ``status`` holds the operation id.
    """
    if len(data) < 9:
        raise ValueError("IPP response too short")
    _, _, status, request_id = struct.unpack(">BBHI", data[:8])
    resp = IppResponse(status, request_id)
    r = _Reader(data, 8)
    attrs: dict[str, list[Any]] | None = None
    last: str | None = None
    while True:
        tag = r.u8()
        if tag == TAG_END:
            break
        if tag < 0x10:  # group delimiter
            attrs = {}
            resp.groups.append((tag, attrs))
            last = None
            continue
        if attrs is None:
            raise ValueError("attribute outside a group")
        name = r.take(r.u16()).decode("utf-8", "replace")
        raw = r.take(r.u16())
        if name:
            last = name
            attrs.setdefault(name, [])
        elif last is None:
            raise ValueError("additional value without an attribute")
        target = attrs[last or name]
        if tag == T_BEG_COLLECTION:
            target.append(_read_collection(r))
        else:
            target.append(_decode_value(tag, raw))
    return resp, r.pos


# --------------------------------------------------------------------------------- client


def normalize_uri(uri: str) -> tuple[str, str]:
    """``(http(s) url, ipp(s) printer-uri)`` of an ipp, ipps, http or https URI."""
    parts = urlsplit(uri.strip())
    scheme = parts.scheme.lower()
    if scheme not in {"ipp", "ipps", "http", "https"} or not parts.hostname:
        raise IppError(REJECTED, f"unsupported printer URI: {uri!r}")
    secure = scheme in {"ipps", "https"}
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise IppError(REJECTED, f"invalid port in {uri!r}") from exc
    host = parts.hostname
    host_part = f"[{host}]" if ":" in host else host
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    http_url = f"{'https' if secure else 'http'}://{host_part}:{port}{path}{query}"
    printer_uri = f"{'ipps' if secure else 'ipp'}://{host_part}:{port}{path}"
    return http_url, printer_uri


def insecure_tls_requested() -> bool:
    return os.environ.get(INSECURE_TLS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


class IppClient:
    """Sends IPP requests to one printer URI (synchronous; run it in a worker or a thread).

    The host is resolved once, here, through the SSRF policy (:mod:`a0td.printing.netpolicy`);
    every connection goes to that pinned IP. TLS is verified unless ``verify_tls=False``.
    """

    def __init__(
        self,
        uri: str,
        *,
        timeout: float = 15.0,
        verify_tls: bool = True,
        user: str = "a0td",
    ) -> None:
        self.http_url, self.printer_uri = normalize_uri(uri)
        self.timeout = timeout
        self.verify_tls = verify_tls and not insecure_tls_requested()
        self.user = user
        self._request_id = 0
        parts = urlsplit(self.http_url)
        assert parts.hostname is not None
        assert parts.port is not None
        try:
            self.pinned_ip = resolve_allowed(parts.hostname, parts.port)
        except HostNotAllowedError as exc:
            log.warning("printer host refused: %s", exc.detail)
            raise IppError(UNREACHABLE, exc.detail) from exc

    # -- plumbing
    def _operation_attrs(self, *extra: Attr) -> list[Attr]:
        return [
            Attr.of(T_CHARSET, "attributes-charset", "utf-8"),
            Attr.of(T_LANGUAGE, "attributes-natural-language", "en"),
            Attr.of(T_URI, "printer-uri", self.printer_uri),
            Attr.of(T_NAME, "requesting-user-name", self.user),
            *extra,
        ]

    def send(
        self,
        operation: int,
        operation_attrs: Sequence[Attr],
        job_attrs: Sequence[Attr] = (),
        payload: bytes | Path = b"",
    ) -> IppResponse:
        """``payload`` is the document: bytes, or a file that is streamed without loading it."""
        self._request_id += 1
        groups: list[tuple[int, Sequence[Attr]]] = [(TAG_OPERATION, operation_attrs)]
        if job_attrs:
            groups.append((TAG_JOB, job_attrs))
        header = encode_header(operation, self._request_id, groups)
        data = self._post(header, payload)
        try:
            resp = decode_response(data)
        except (ValueError, struct.error) as exc:
            raise IppError(UNREACHABLE, f"invalid IPP response: {exc}") from exc
        return resp

    def _connection(self) -> http.client.HTTPConnection:
        parts = urlsplit(self.http_url)
        assert parts.hostname is not None
        pinned = (self.pinned_ip, parts.port)

        def connect_pinned(
            _address: tuple[str, int], timeout: float, source_address: Any = None
        ) -> socket.socket:
            # ignore the name: connect to the IP the policy approved (no second resolution)
            return socket.create_connection(pinned, timeout, source_address)

        conn: http.client.HTTPConnection
        if parts.scheme == "https":
            ctx = ssl.create_default_context()
            if not self.verify_tls:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(
                parts.hostname, parts.port, timeout=self.timeout, context=ctx
            )
        else:
            conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=self.timeout)
        conn._create_connection = connect_pinned  # type: ignore[attr-defined]
        return conn

    def _post(self, header: bytes, payload: bytes | Path = b"") -> bytes:
        parts = urlsplit(self.http_url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        conn = self._connection()
        try:
            body: bytes | Iterator[bytes]
            if isinstance(payload, Path):
                size = payload.stat().st_size
                body = _stream(header, payload)
            else:
                size = len(payload)
                body = header + payload
            conn.request(
                "POST",
                target,
                body=body,
                headers={
                    "Content-Type": "application/ipp",
                    "Accept": "application/ipp",
                    "Content-Length": str(len(header) + size),
                },
            )
            resp = conn.getresponse()
            if resp.status != 200:
                raise IppError(UNREACHABLE, f"the printer answered HTTP {resp.status}")
            data = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise IppError(UNREACHABLE, "the printer response is too large")
            return data
        except (OSError, http.client.HTTPException) as exc:
            raise IppError(UNREACHABLE, f"cannot reach the printer: {exc}") from exc
        finally:
            conn.close()

    @staticmethod
    def check(resp: IppResponse, what: str) -> IppResponse:
        if not resp.ok:
            raise IppError(REJECTED, f"{what} failed (IPP status 0x{resp.status:04x})", resp.status)
        return resp

    # -- operations
    def get_printer_attributes(
        self, requested: Sequence[str] | None = None
    ) -> dict[str, list[Any]]:
        extra = [Attr.of(T_KEYWORD, "requested-attributes", *requested)] if requested else []
        resp = self.check(
            self.send(GET_PRINTER_ATTRIBUTES, self._operation_attrs(*extra)),
            "Get-Printer-Attributes",
        )
        return resp.group(TAG_PRINTER)

    def print_job(
        self,
        data: bytes | Path,
        *,
        document_format: str,
        job_name: str,
        job_attrs: Sequence[Attr],
    ) -> "PrintResult":
        ops = self._operation_attrs(
            Attr.of(T_NAME, "job-name", job_name),
            Attr.of(T_MIME, "document-format", document_format),
        )
        resp = self.check(self.send(PRINT_JOB, ops, job_attrs, data), "Print-Job")
        job = resp.group(TAG_JOB)
        unsupported = sorted(resp.group(TAG_UNSUPPORTED))
        job_id = _first(job, "job-id")
        if job_id is None:
            raise IppError(REJECTED, "the printer did not return a job id", resp.status)
        return PrintResult(
            job_id=int(job_id),
            job_uri=_first(job, "job-uri"),
            state=_first(job, "job-state"),
            unsupported=unsupported,
        )

    def job_state(self, job_id: int) -> tuple[int | None, list[str]]:
        """``(job-state, job-state-reasons)`` of a job."""
        ops = self._operation_attrs(
            Attr.of(T_INTEGER, "job-id", job_id),
            Attr.of(T_KEYWORD, "requested-attributes", "job-state", "job-state-reasons"),
        )
        resp = self.check(self.send(GET_JOB_ATTRIBUTES, ops), "Get-Job-Attributes")
        job = resp.group(TAG_JOB)
        return _first(job, "job-state"), [str(v) for v in job.get("job-state-reasons", [])]

    def cancel_job(self, job_id: int) -> None:
        ops = self._operation_attrs(Attr.of(T_INTEGER, "job-id", job_id))
        self.check(self.send(CANCEL_JOB, ops), "Cancel-Job")


def _stream(header: bytes, path: Path) -> Iterator[bytes]:
    yield header
    with path.open("rb") as fh:
        while chunk := fh.read(STREAM_CHUNK):
            yield chunk


@dataclass(frozen=True, slots=True)
class PrintResult:
    job_id: int
    job_uri: str | None
    state: int | None
    unsupported: list[str]


def _first(attrs: Mapping[str, Sequence[Any]], name: str) -> Any:
    values = attrs.get(name)
    return values[0] if values else None


# ------------------------------------------------------------------------- job attributes


def page_ranges(pages: Sequence[int]) -> list[tuple[int, int]]:
    """Sorted 1-based page numbers as IPP ``rangeOfInteger`` values."""
    out: list[tuple[int, int]] = []
    for n in sorted(set(pages)):
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def job_template(
    *,
    media: str,
    copies: int,
    monochrome: bool,
    pages: Sequence[int] | None,
) -> list[Attr]:
    """IMP-03 job attributes: no scaling, one-sided, PWG media name, colour, copies, ranges."""
    attrs = [
        Attr.of(T_KEYWORD, "media", media),
        Attr.of(T_KEYWORD, "print-scaling", "none"),
        Attr.of(T_KEYWORD, "sides", "one-sided"),
        Attr.of(T_KEYWORD, "print-color-mode", "monochrome" if monochrome else "color"),
        Attr.of(T_INTEGER, "copies", max(1, int(copies))),
    ]
    if pages:
        attrs.append(Attr.of(T_RANGE, "page-ranges", *page_ranges(pages)))
    return attrs


# ------------------------------------------------------------------------- capabilities

_SIZE_NAME = re.compile(r"_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|in)$")
_HUNDREDTHS_MM = 100.0
_INCH_MM = 25.4


@dataclass(slots=True)
class MediaEntry:
    width_mm: float
    height_mm: float
    margins_mm: dict[str, float] | None = None  # top / right / bottom / left (None = unknown)
    name: str | None = None


def _dimension(value: Any) -> float | None:
    """x/y-dimension: an integer, or a range (roll media: the maximum)."""
    if isinstance(value, tuple) and len(value) == 2:
        return float(value[1])
    if isinstance(value, int):
        return float(value)
    return None


def _media_from_col(col: Collection) -> MediaEntry | None:
    sizes = col.get("media-size") or []
    if not sizes or not isinstance(sizes[0], dict):
        return None
    size = sizes[0]
    x = _dimension((size.get("x-dimension") or [None])[0])
    y = _dimension((size.get("y-dimension") or [None])[0])
    if x is None or y is None:
        return None
    margins: dict[str, float] | None = None
    keys = {
        "top": "media-top-margin",
        "right": "media-right-margin",
        "bottom": "media-bottom-margin",
        "left": "media-left-margin",
    }
    found = {side: col.get(key) for side, key in keys.items()}
    if all(v and isinstance(v[0], int) for v in found.values()):
        margins = {side: v[0] / _HUNDREDTHS_MM for side, v in found.items() if v}
    return MediaEntry(x / _HUNDREDTHS_MM, y / _HUNDREDTHS_MM, margins)


def _media_from_name(name: str) -> MediaEntry | None:
    m = _SIZE_NAME.search(name)
    if not m:
        return None
    k = _INCH_MM if m.group(3) == "in" else 1.0
    return MediaEntry(float(m.group(1)) * k, float(m.group(2)) * k, None, name)


def media_entries(attrs: Mapping[str, Sequence[Any]]) -> list[MediaEntry]:
    """Sizes (and margins) the printer reports: ``media-col-database``, else ``media-supported``."""
    entries = [
        e
        for col in attrs.get("media-col-database", [])
        if isinstance(col, dict) and (e := _media_from_col(col)) is not None
    ]
    if not entries:
        entries = [
            e
            for name in attrs.get("media-supported", [])
            if isinstance(name, str) and (e := _media_from_name(name)) is not None
        ]
    return entries


def capabilities(attrs: Mapping[str, Sequence[Any]], uri: str) -> dict[str, Any]:
    """Printer profile prefill (PAP-07) from Get-Printer-Attributes.

    Papers are matched against the catalog; for each one the largest margins over all the
    ``media-col-database`` entries of that size are kept (borderless entries have margin 0,
    bordered ones the real limit).
    """
    papers: dict[str, dict[str, Any]] = {}
    for entry in media_entries(attrs):
        match = match_standard(entry.width_mm, entry.height_mm, list(PAPERS.values()))
        if match is None:
            continue
        slot = papers.setdefault(
            match.standard.id,
            {
                "paper_id": match.standard.id,
                "width_mm": match.standard.width_mm,
                "height_mm": match.standard.height_mm,
                "media": match.standard.pwg_name,
                "margins_mm": None,
            },
        )
        if entry.margins_mm is not None:
            cur = slot["margins_mm"]
            slot["margins_mm"] = (
                dict(entry.margins_mm)
                if cur is None
                else {k: max(cur[k], v) for k, v in entry.margins_mm.items()}
            )
    ordered = [papers[p.value] for p in PaperId if p.value in papers]
    known = [p["margins_mm"] for p in ordered if p["margins_mm"]]
    default_margins = None
    if known:
        default_margins = {
            side: round(max(m[side] for m in known), 2)
            for side in ("top", "right", "bottom", "left")
        }
    for p in ordered:
        if p["margins_mm"]:
            p["margins_mm"] = {k: round(v, 2) for k, v in p["margins_mm"].items()}
    formats = [str(v) for v in attrs.get("document-format-supported", [])]
    color_modes = [str(v) for v in attrs.get("print-color-mode-supported", [])]
    color_supported = attrs.get("color-supported", [None])[0]
    if color_supported is None:
        color_supported = (not color_modes) or "color" in color_modes
    name = _first(attrs, "printer-info") or _first(attrs, "printer-name")
    return {
        "uri": uri,
        "name": _first(attrs, "printer-make-and-model") or name,
        "papers": ordered,
        "default_margins_mm": default_margins,
        "color": bool(color_supported),
        "document_formats": formats,
        "accepts_pdf": "application/pdf" in formats if formats else None,
        "accepts_pwg_raster": "image/pwg-raster" in formats if formats else None,
        "page_ranges": bool(_first(attrs, "page-ranges-supported")),
    }


def wanted_capability_attributes() -> list[str]:
    return [
        "printer-name",
        "printer-info",
        "printer-make-and-model",
        "media-col-database",
        "media-supported",
        "media-default",
        "document-format-supported",
        "color-supported",
        "print-color-mode-supported",
        "page-ranges-supported",
        "pwg-raster-document-resolution-supported",
        "printer-resolution-default",
        "sides-supported",
        "print-scaling-supported",
    ]
