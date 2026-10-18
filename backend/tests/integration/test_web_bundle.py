"""The web bundle (``tools/build_web_bundle.py``) holds a closed, server-free set of modules.

The archive is imported with ``zipimport`` in a subprocess that cannot import pyvips,
pydantic-settings, FastAPI, SQLAlchemy... exactly what Pyodide lacks, then the bridge runs a vector
and a raster job end to end.
"""

import importlib.util
import json
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BLOCKED = (
    "pyvips pydantic_settings fastapi starlette sqlalchemy alembic uvicorn typer argon2 "
    "prometheus_client zeroconf python_multipart multipart httpx"
)


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    path = REPO_ROOT / "tools" / "build_web_bundle.py"
    if not path.is_file():
        pytest.skip("tools/build_web_bundle.py is not in the repo")
    spec = importlib.util.spec_from_file_location("build_web_bundle", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_web_bundle"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def built(tool: ModuleType, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("py")
    assert tool.main(["--out", str(out)]) == 0
    return out


def test_archive_has_the_web_modules_and_no_server_code(built: Path) -> None:
    with zipfile.ZipFile(built / "a0td.zip") as zf:
        names = set(zf.namelist())
    assert {
        "a0td/web/bridge.py",
        "a0td/imaging/vips_compat.py",
        "a0td/render/engine.py",
        "a0td/domain/planner.py",
        "a0td/ingest/pipeline.py",
        "a0td/io_models.py",
        "a0td/_settings_shim.py",
    } <= names
    server = ("api", "jobs", "storage", "printing", "automation", "cli")
    assert not [n for n in names if n.split("/")[1] in server]
    assert not names & {"a0td/metrics.py", "a0td/logging.py"}
    assert all(n.endswith(".py") for n in names)


def test_build_is_reproducible(tool: ModuleType, built: Path, tmp_path: Path) -> None:
    assert tool.main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / "a0td.zip").read_bytes() == (built / "a0td.zip").read_bytes()


def test_manifest_lists_packages_extras_and_the_bundle(built: Path) -> None:
    man = json.loads((built / "manifest.json").read_text(encoding="utf-8"))
    assert man["pyodide"]["version"] == "0.29.5"
    assert {"pymupdf", "numpy", "pillow", "pillow-heif", "pydantic", "pydantic-core"} <= set(
        man["packages"]
    )
    assert {e["name"] for e in man["extras"]} == {"defusedxml", "ezdxf"}
    assert man["bundle"]["file"] == "a0td.zip"
    assert len(man["bundle"]["sha256"]) == 64
    assert man["bundle"]["entry"] == "a0td.web.bridge"


_RUNNER = textwrap.dedent(
    """
    import importlib.abc, json, os, sys
    BLOCKED = set({blocked!r}.split())
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"blocked: {{name}}")
    sys.meta_path.insert(0, Block())
    sys.path.insert(0, {zip!r})
    os.environ["A0TD_WEB"] = "1"
    import a0td
    assert a0td.__file__.startswith({zip!r}), a0td.__file__
    from a0td.web import bridge
    from a0td.imaging import backend_name
    bridge.configure({root!r})
    out = {{"backend": backend_name()}}
    doc = json.loads(bridge.ingest({pdf!r}, "grid.pdf"))
    s = json.dumps({{"document_id": doc["id"]}})
    plan = json.loads(bridge.plan(s, 0, False, None))
    res = json.loads(bridge.render(s, None, False))
    out["vector"] = [doc["status"], plan["counts"]["printed"], res["outputs"][0]["pages"]]
    opts = {{"size_override": {{"dpi": 150}}}}
    img = json.loads(bridge.ingest({jpg!r}, "cmyk.jpg", options=opts))
    s = json.dumps({{"document_id": img["id"], "scale": {{"mode": "percent", "percent": 100}}}})
    res = json.loads(bridge.render(s, None, False))
    out["raster"] = [img["status"], res["outputs"][0]["pages"]]
    print("RESULT" + json.dumps(out))
    """
)


def test_bundle_runs_without_server_dependencies(
    built: Path, tmp_path: Path, fixtures_dir: Path
) -> None:
    code = _RUNNER.format(
        blocked=BLOCKED,
        zip=str(built / "a0td.zip"),
        root=str(tmp_path / "root"),
        pdf=str(fixtures_dir / "a0_vector_grid.pdf"),
        jpg=str(fixtures_dir / "cmyk.jpg"),
    )
    env = {"PATH": "", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}  # no tools, no A0TD_*
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=300
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT"))
    result = json.loads(line[len("RESULT") :])
    assert result["backend"] == "pillow"  # pyvips could not be imported: automatic fallback
    assert result["vector"] == ["ready", 21, 22]
    assert result["raster"][0] == "ready"
    assert result["raster"][1] >= 1
