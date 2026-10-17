"""Converter registry and the external-tool subprocess helper (SPEC 7.2)."""

import os
import sys
from pathlib import Path

import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest import ConverterRegistry, DocFormat, default_registry, ingest_document
from a0td.ingest.converters import run_tool, tool_available
from a0td.ingest.converters.base import ConvertContext
from a0td.ingest.models import CanonicalDocument


class FakeConverter:
    def __init__(self, cid: str, handles: set[DocFormat], *, available: bool) -> None:
        self.id = cid
        self.handles = frozenset(handles)
        self._available = available

    def available(self) -> bool:
        return self._available

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        raise AssertionError("not expected to run")


def test_default_registry_has_the_f1_to_f3_converters() -> None:
    reg = default_registry()
    assert {c.id for c in reg.converters} == {
        "pdf",
        "raster",
        "heif",
        "svg",
        "postscript",
        "xps",
        "office",
        "dxf",
        "dwg",
        "hpgl",
        "djvu",
    }
    assert DocFormat.PDF in reg.available_formats()
    assert {DocFormat.PNG, DocFormat.JPEG, DocFormat.TIFF, DocFormat.GIF, DocFormat.BMP} <= (
        reg.available_formats()
    )
    info = {i["id"]: i for i in reg.info()}
    assert info["pdf"]["available"] is True
    assert "tiff" in info["raster"]["formats"]


@pytest.mark.parametrize("fmt", [DocFormat.DXF, DocFormat.DWG, DocFormat.CDR, DocFormat.HPGL])
def test_unregistered_format_is_not_available_in_image(fmt: DocFormat) -> None:
    with pytest.raises(DomainError) as exc:
        ConverterRegistry([]).resolve(fmt)
    assert exc.value.code is ErrorCode.FORMAT_NOT_AVAILABLE_IN_IMAGE
    assert exc.value.params["format"] == fmt.value


def test_unavailable_converter_is_skipped() -> None:
    off = FakeConverter("office", {DocFormat.DOCX}, available=False)
    on = FakeConverter("office2", {DocFormat.DOCX}, available=True)
    reg = ConverterRegistry([off])
    with pytest.raises(DomainError):
        reg.resolve(DocFormat.DOCX)
    assert DocFormat.DOCX not in reg.available_formats()
    reg.register(on)
    assert reg.resolve(DocFormat.DOCX) is on
    assert [i["available"] for i in reg.info()] == [False, True]


def test_ingest_reports_unavailable_format(tmp_path: Path) -> None:
    src = tmp_path / "plan.dxf"
    src.write_bytes(b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n")
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "out", registry=ConverterRegistry([]))
    assert exc.value.code is ErrorCode.FORMAT_NOT_AVAILABLE_IN_IMAGE


def test_ingest_reports_unsupported_format(tmp_path: Path) -> None:
    src = tmp_path / "notes.txt"
    src.write_text("hello")
    with pytest.raises(DomainError) as exc:
        ingest_document(src, tmp_path / "out")
    assert exc.value.code is ErrorCode.UNSUPPORTED_FORMAT
    assert "pdf" in exc.value.params["supported"]


# ------------------------------------------------------------------------------ run_tool

posix_only = pytest.mark.skipif(os.name != "posix", reason="needs POSIX process groups / rlimits")


def test_run_tool_captures_output_and_isolates_home(tmp_path: Path) -> None:
    code = (
        "import os,sys; print(os.environ['HOME']); print(os.environ['TMPDIR']); "
        "sys.stderr.write('e')"
    )
    res = run_tool([sys.executable, "-c", code], scratch=tmp_path / "s", timeout_s=30)
    home, tmp, *_ = res.stdout.decode().split()
    assert Path(home) == tmp_path / "s" / "home"
    assert Path(tmp) == tmp_path / "s" / "tmp"
    assert res.stderr == b"e"


def test_run_tool_never_uses_a_shell(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    res = run_tool(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", f"x; touch {marker}"],
        scratch=tmp_path / "s",
        timeout_s=30,
    )
    assert f"touch {marker}" in res.stdout.decode()
    assert not marker.exists()


def test_run_tool_failure(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as exc:
        run_tool([sys.executable, "-c", "import sys; sys.exit(3)"], scratch=tmp_path, timeout_s=30)
    assert exc.value.code is ErrorCode.CONVERSION_FAILED
    ok = run_tool(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        scratch=tmp_path,
        timeout_s=30,
        ok_codes=(0, 3),
    )
    assert ok.returncode == 3


def test_run_tool_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as exc:
        run_tool(["definitely-not-a-binary-a0td"], scratch=tmp_path)
    assert exc.value.code is ErrorCode.CONVERSION_FAILED
    assert not tool_available("definitely-not-a-binary-a0td")


@posix_only
def test_run_tool_timeout_kills_the_group(tmp_path: Path) -> None:
    code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
    )
    with pytest.raises(DomainError) as exc:
        run_tool([sys.executable, "-c", code], scratch=tmp_path, timeout_s=1)
    assert exc.value.code is ErrorCode.CONVERSION_TIMEOUT


@posix_only
def test_run_tool_memory_limit(tmp_path: Path) -> None:
    code = "x = bytearray(900 * 1024 * 1024); print(len(x))"
    with pytest.raises(DomainError) as exc:
        run_tool([sys.executable, "-c", code], scratch=tmp_path, timeout_s=30, memory_mb=300)
    assert exc.value.code is ErrorCode.CONVERSION_FAILED


def test_context_reports_progress(tmp_path: Path) -> None:
    seen = []
    ctx = ConvertContext(config=AppConfig(), progress=seen.append)
    ctx.report("convert", 10, "ingest.page", k=1, n=2)
    assert seen[0].stage == "convert"
    assert seen[0].params == {"k": 1, "n": 2}
    assert ctx.scratch_dir(tmp_path).is_dir()


def test_run_tool_output_is_tail_bounded(tmp_path: Path) -> None:
    code = "import sys; sys.stdout.write('a' * 300000 + 'END')"
    res = run_tool([sys.executable, "-c", code], scratch=tmp_path, timeout_s=30)
    assert len(res.stdout) == 64 * 1024
    assert res.stdout.endswith(b"END")
