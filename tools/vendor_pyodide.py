#!/usr/bin/env python3
"""Download Pyodide and its wheels into ``frontend/public/py/pyodide/`` (SPEC 17bis).

The web local mode serves everything from its own origin (no third-party CDN at run time). This
script runs in CI and on developer machines; the downloaded binaries are git-ignored
(``frontend/.gitignore``).

What it fetches (all verified against the SHA-256 of ``pyodide-lock.json`` when it has one):

* the Pyodide core of the version in ``tools/build_web_bundle.py`` (``pyodide.js``, ``.mjs``,
  ``.asm.js``, ``.asm.wasm``, ``python_stdlib.zip``);
* the distributions of ``manifest.json``/``packages`` and their dependencies, from Pyodide's
  distribution (the lock file is rewritten so it lists only what is vendored);
* the pure-python extras of ``manifest.json``/``extras`` from PyPI (hash from the PyPI JSON API).

It also writes ``files.json`` (path, size, sha256 of every vendored file) for the service worker.
Files already present with the right hash are kept, so re-running is cheap.

Usage: ``python tools/vendor_pyodide.py [--out frontend/public/py] [--base URL]``.
Standard library only.
"""

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_web_bundle as bundle  # noqa: E402

CORE_FILES = [
    "pyodide.js",
    "pyodide.mjs",
    "pyodide.asm.js",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
]
CDN = "https://cdn.jsdelivr.net/pyodide/v{version}/full/"


def fetch(url: str, retries: int = 3) -> bytes:
    last: Exception | None = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return bytes(resp.read())
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
    raise SystemExit(f"download failed: {url}: {last}")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store(dest: Path, url: str, expected: str | None = None) -> bytes:
    """Download ``url`` to ``dest`` unless it is already there (and matches ``expected``)."""
    if dest.is_file():
        data = dest.read_bytes()
        if expected is None or sha256(data) == expected:
            return data
    data = fetch(url)
    if expected is not None and sha256(data) != expected:
        raise SystemExit(f"checksum mismatch for {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    print(f"  {dest.name} ({len(data) / 1024:.0f} kB)")
    return data


def closure(packages: dict[str, dict[str, object]], roots: list[str]) -> list[str]:
    seen: list[str] = []
    todo = [r.lower().replace("_", "-") for r in roots]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        if name not in packages:
            raise SystemExit(f"package {name!r} is not in pyodide-lock.json")
        seen.append(name)
        todo.extend(str(d).lower().replace("_", "-") for d in packages[name].get("depends", []))  # type: ignore[attr-defined]
    return sorted(seen)


def vendor_extras(out: Path, extras: list[dict[str, object]]) -> None:
    for extra in extras:
        name, version, wheel = str(extra["name"]), str(extra["version"]), str(extra["wheel"])
        meta = json.loads(fetch(f"https://pypi.org/pypi/{name}/{version}/json"))
        entry = next((u for u in meta["urls"] if u["filename"] == wheel), None)
        if entry is None:
            raise SystemExit(f"{wheel} is not on PyPI")
        store(out / wheel, entry["url"], entry["digests"]["sha256"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / "frontend" / "public" / "py")
    ap.add_argument("--base", default=None, help="URL of Pyodide's 'full' folder")
    args = ap.parse_args(argv)

    base = args.base or CDN.format(version=bundle.PYODIDE_VERSION)
    if not base.endswith("/"):
        base += "/"
    dest = args.out / "pyodide"
    dest.mkdir(parents=True, exist_ok=True)
    print(f"Pyodide {bundle.PYODIDE_VERSION} from {base}")

    lock = json.loads(fetch(base + "pyodide-lock.json"))
    if lock["info"].get("abi_version") != "2025_0":  # the wheels of Pyodide 0.29
        raise SystemExit(f"{base} is not a Pyodide 0.29 distribution: {lock['info']}")
    packages: dict[str, dict[str, object]] = lock["packages"]
    chosen = closure(packages, bundle.PYODIDE_PACKAGES)

    for name in CORE_FILES:
        store(dest / name, base + name)
    for name in chosen:
        file_name = str(packages[name]["file_name"])
        store(dest / file_name, base + file_name, str(packages[name].get("sha256") or "") or None)
    lock["packages"] = {name: packages[name] for name in chosen}
    (dest / "pyodide-lock.json").write_text(json.dumps(lock, separators=(",", ":")), "utf-8")
    vendor_extras(dest, [dict(e) for e in bundle.EXTRAS])

    files = [
        {"path": p.name, "size": p.stat().st_size, "sha256": sha256(p.read_bytes())}
        for p in sorted(dest.iterdir())
        if p.is_file() and p.name != "files.json"
    ]
    (dest / "files.json").write_text(json.dumps(files, indent=1) + "\n", encoding="utf-8")
    total = sum(f["size"] for f in files)  # type: ignore[misc]
    print(f"{len(files)} files, {total / 1048576:.1f} MB in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
