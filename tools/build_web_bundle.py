#!/usr/bin/env python3
"""Build the web local mode bundle (SPEC 17bis): ``a0td.zip`` + ``manifest.json``.

Both go to ``frontend/public/py``. ``a0td.zip`` holds the pure-python ``a0td`` package that runs
inside Pyodide: domain, ingest, render, imaging and the web bridge. The server-only packages (api,
jobs, storage, printing, automation, cli) and modules (metrics, logging) are left out.
The archive is reproducible (sorted entries, fixed timestamps).

``manifest.json`` tells the worker what to load:

* ``packages``: Pyodide distributions for ``pyodide.loadPackage`` (wheels vendored by
  ``tools/vendor_pyodide.py`` into ``frontend/public/py/pyodide/``);
* ``extras``: pure-python wheels installed with ``micropip.install(deps=False)`` from the same
  folder (``defusedxml``; ``ezdxf`` for DXF, which imports fine in Pyodide);
* ``bundle``: the zip, its sha256 and the import path of the bridge.

Usage: ``python tools/build_web_bundle.py [--out frontend/public/py] [--check]``.
No third-party dependencies. The Pyodide core and wheels are NOT built here and must not be
committed (``frontend/.gitignore``); run ``python tools/vendor_pyodide.py`` (CI does).
"""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"
PACKAGE = "a0td"

PYODIDE_VERSION = "0.29.5"
#: Excluded subpackages / modules of ``a0td`` (server only).
EXCLUDE_DIRS = frozenset({"api", "jobs", "storage", "printing", "automation", "cli", "__pycache__"})
EXCLUDE_FILES = frozenset({"metrics.py", "logging.py"})

#: Pyodide distributions (``loadPackage`` also pulls their dependencies from the lock file).
PYODIDE_PACKAGES = [
    "micropip",
    "pymupdf",
    "numpy",
    "pillow",
    "pillow-heif",
    "pydantic",
    "pydantic-core",
    # dependencies of the extras below (micropip installs them with deps=False)
    "pyparsing",
    "typing-extensions",
    "fonttools",
]
#: Pure-python wheels from PyPI, pinned to the versions of ``backend/uv.lock``.
EXTRAS = [
    {"name": "defusedxml", "version": "0.7.1", "wheel": "defusedxml-0.7.1-py2.py3-none-any.whl"},
    {
        "name": "ezdxf",
        "version": "1.4.5",
        "wheel": "ezdxf-1.4.5-py3-none-any.whl",
        "optional": True,
    },
]

_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def package_files() -> list[Path]:
    """``.py`` files of ``a0td`` that go into the bundle, sorted."""
    base = SRC / PACKAGE
    out: list[Path] = []
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(base)
        if rel.parts[0] in EXCLUDE_DIRS or any(p in EXCLUDE_DIRS for p in rel.parts[:-1]):
            continue
        if len(rel.parts) == 1 and rel.name in EXCLUDE_FILES:
            continue
        out.append(path)
    return out


def read_version() -> str:
    text = (SRC / PACKAGE / "version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0"


def build_zip(dest: Path) -> list[str]:
    """Write ``dest``; returns the archive member names."""
    names: list[str] = []
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in package_files():
            arcname = f"{PACKAGE}/{path.relative_to(SRC / PACKAGE).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
            names.append(arcname)
    return names


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(zip_path: Path, members: list[str]) -> dict[str, object]:
    return {
        "schema": 1,
        "app_version": read_version(),
        "pyodide": {"version": PYODIDE_VERSION, "path": "pyodide/", "index": "pyodide/pyodide.js"},
        "packages": PYODIDE_PACKAGES,
        "extras": EXTRAS,
        "bundle": {
            "file": zip_path.name,
            "sha256": sha256(zip_path),
            "size": zip_path.stat().st_size,
            "modules": len(members),
            "entry": "a0td.web.bridge",
            "root": "/a0td",
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / "frontend" / "public" / "py")
    ap.add_argument("--check", action="store_true", help="only verify that the file list is sane")
    args = ap.parse_args(argv)

    files = package_files()
    rels = {p.relative_to(SRC / PACKAGE).as_posix() for p in files}
    for required in (
        "web/bridge.py",
        "imaging/vips_compat.py",
        "domain/planner.py",
        "render/engine.py",
    ):
        if required not in rels:
            print(f"error: {required} is missing from the bundle", file=sys.stderr)
            return 1
    if args.check:
        print(f"{len(files)} modules")
        return 0
    zip_path = args.out / "a0td.zip"
    members = build_zip(zip_path)
    man = manifest(zip_path, members)
    (args.out / "manifest.json").write_text(
        json.dumps(man, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{zip_path}: {len(members)} modules, {zip_path.stat().st_size / 1024:.0f} kB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
