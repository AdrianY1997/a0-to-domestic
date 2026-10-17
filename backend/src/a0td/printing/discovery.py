"""mDNS discovery of IPP printers (SPEC IMP-04), best effort.

Needs the optional ``zeroconf`` package and a network where multicast reaches the container
(Docker ``network_mode: host`` on Linux). Docker Desktop does not forward mDNS: there the printer
URI is typed by hand.
"""

import importlib.util
import time
from typing import Any

SERVICE_TYPES = ("_ipp._tcp.local.", "_ipps._tcp.local.")


class DiscoveryUnavailableError(Exception):
    """``zeroconf`` is missing or the multicast socket cannot be opened."""


def available() -> bool:
    return importlib.util.find_spec("zeroconf") is not None


def _uri(host: str, port: int, secure: bool, txt: dict[str, str]) -> str:
    path = "/" + txt.get("rp", "ipp/print").lstrip("/")
    return f"{'ipps' if secure else 'ipp'}://{host}:{port}{path}"


def discover(timeout_s: float = 3.0) -> list[dict[str, Any]]:
    """Browse ``_ipp._tcp`` / ``_ipps._tcp`` for ``timeout_s`` seconds."""
    if not available():
        raise DiscoveryUnavailableError("the 'zeroconf' package is not installed")
    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

    found: dict[tuple[str, str], None] = {}

    class _Listener(ServiceListener):
        def add_service(self, zc: Any, type_: str, name: str) -> None:
            found[(type_, name)] = None

        def update_service(self, zc: Any, type_: str, name: str) -> None:
            found[(type_, name)] = None

        def remove_service(self, zc: Any, type_: str, name: str) -> None:
            found.pop((type_, name), None)

    try:
        zc = Zeroconf()
    except OSError as exc:
        raise DiscoveryUnavailableError(f"cannot open the mDNS socket: {exc}") from exc
    printers: list[dict[str, Any]] = []
    try:
        browser = ServiceBrowser(zc, list(SERVICE_TYPES), _Listener())
        time.sleep(timeout_s)
        for type_, name in list(found):
            info = zc.get_service_info(type_, name, timeout=1500)
            if info is None:
                continue
            txt = {
                k.decode("utf-8", "replace"): (v or b"").decode("utf-8", "replace")
                for k, v in info.properties.items()
            }
            addresses = info.parsed_addresses()
            host = addresses[0] if addresses else (info.server or "").rstrip(".")
            if not host or not info.port:
                continue
            secure = type_.startswith("_ipps")
            printers.append(
                {
                    "name": name.removesuffix("." + type_),
                    "host": host,
                    "port": info.port,
                    "uri": _uri(f"[{host}]" if ":" in host else host, info.port, secure, txt),
                    "model": txt.get("ty") or txt.get("product") or None,
                    "secure": secure,
                }
            )
        browser.cancel()
    finally:
        zc.close()
    seen: set[str] = set()
    unique = []
    for p in sorted(printers, key=lambda p: p["name"]):
        if p["uri"] not in seen:
            seen.add(p["uri"])
            unique.append(p)
    return unique
