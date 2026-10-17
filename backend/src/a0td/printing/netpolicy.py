"""SSRF policy for user-supplied printer hosts (resolve, check, pin the IP).

Allowed: private LAN (RFC 1918, IPv6 ULA) and public addresses. Rejected: loopback, link-local
(169.254/16, fe80::/10), cloud metadata (169.254.169.254, fd00:ec2::254), unspecified, multicast,
broadcast and reserved ranges, unless the host or the IP is listed in ``A0TD_IPP_ALLOW_HOSTS``
(comma separated; used by the tests for 127.0.0.1).

The caller connects to the **returned IP** (never re-resolving the name), so a DNS answer that
changes between check and connect (DNS rebinding) cannot reach a forbidden address.
"""

import ipaddress
import os
import socket

ALLOW_ENV = "A0TD_IPP_ALLOW_HOSTS"
_IP = ipaddress.IPv4Address | ipaddress.IPv6Address
_METADATA = (ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254"))
_THIS_NET = ipaddress.ip_network("0.0.0.0/8")
_RESERVED = ipaddress.ip_network("240.0.0.0/4")  # includes the broadcast address


class HostNotAllowedError(Exception):
    """The host is forbidden or cannot be resolved; ``detail`` is for the logs only."""

    def __init__(self, detail: str, *, unresolved: bool = False) -> None:
        super().__init__(detail)
        self.detail = detail
        self.unresolved = unresolved  # DNS failure, as opposed to a forbidden address


def allowed_hosts() -> frozenset[str]:
    raw = os.environ.get(ALLOW_ENV, "")
    return frozenset(h.strip().strip("[]").lower() for h in raw.split(",") if h.strip())


def _parse(text: str) -> _IP | None:
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_forbidden(ip: _IP) -> bool:
    if ip in _METADATA:
        return True
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return True
    return isinstance(ip, ipaddress.IPv4Address) and (ip in _THIS_NET or ip in _RESERVED)


def resolve_allowed(host: str, port: int) -> str:
    """The IP to connect to for ``host``; raises :class:`HostNotAllowedError`."""
    name = host.strip().strip("[]").lower()
    allow = allowed_hosts()
    literal = _parse(name)
    if literal is not None:
        candidates: list[_IP] = [literal]
    else:
        try:
            infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError) as exc:
            raise HostNotAllowedError(f"cannot resolve {host!r}: {exc}", unresolved=True) from exc
        candidates = []
        for info in infos:
            ip = _parse(str(info[4][0]))
            if ip is not None and ip not in candidates:
                candidates.append(ip)
        if not candidates:
            raise HostNotAllowedError(f"{host!r} has no usable address")
    host_listed = name in allow
    # every answer must pass: a name that also points to a forbidden IP is rejected
    for ip in candidates:
        if is_forbidden(ip) and not host_listed and str(ip) not in allow:
            raise HostNotAllowedError(f"{host!r} resolves to the forbidden address {ip}")
    return str(candidates[0])
