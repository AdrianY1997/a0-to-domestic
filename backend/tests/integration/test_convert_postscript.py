"""PostScript / EPS / Illustrator converter: Ghostscript with -dSAFER (SPEC 7.1, 14, 15.6)."""

import contextlib
from pathlib import Path

import pymupdf
import pytest

from a0td.config import AppConfig
from a0td.domain.errors import DomainError
from a0td.ingest import ingest_document
from a0td.ingest.converters import postscript as ps_mod
from a0td.ingest.converters.base import tool_available
from convert_support import assert_not_blank, assert_size, convert

needs_gs = pytest.mark.skipif(not tool_available("gs"), reason="Ghostscript not installed")

PWNED = Path("/tmp/a0td_pwned")


@needs_gs
@pytest.mark.parametrize(
    ("name", "fmt"), [("eps_a4.eps", "eps"), ("ps_a4.ps", "ps"), ("ai_postscript.ai", "ai")]
)
def test_postscript_family_converts_to_a4(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig, name: str, fmt: str
) -> None:
    result, work = convert(fixtures_dir, name, tmp_path, cfg)
    assert result.format.value == fmt
    assert result.kind == "pdf"
    assert len(result.pages) == 1
    assert_size(result, 209.9, 297)
    assert_not_blank(work)


@needs_gs
def test_pdf_compatible_illustrator_takes_the_pdf_path(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_gs(*args: object, **kw: object) -> None:
        raise AssertionError("Ghostscript must not run for a PDF-compatible .ai")

    monkeypatch.setattr(ps_mod, "run_tool", no_gs)
    result, work = convert(fixtures_dir, "ai_pdf.ai", tmp_path, cfg)
    assert result.format.value == "ai"
    assert_size(result, 297, 420)
    assert_not_blank(work)


@needs_gs
def test_ghostscript_always_runs_with_safer(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    real = ps_mod.run_tool

    def spy(argv, **kw):  # type: ignore[no-untyped-def]
        seen.append([str(a) for a in argv])
        return real(argv, **kw)

    monkeypatch.setattr(ps_mod, "run_tool", spy)
    convert(fixtures_dir, "eps_a4.eps", tmp_path, cfg)
    assert len(seen) == 1
    assert "-dSAFER" in seen[0]
    assert "-dEPSCrop" in seen[0]
    assert "-dNOSAFER" not in seen[0]
    assert not any(a.startswith("--permit") for a in seen[0])


@needs_gs
def test_postscript_cannot_read_or_write_files(
    fixtures_dir: Path, tmp_path: Path, cfg: AppConfig
) -> None:
    PWNED.unlink(missing_ok=True)
    try:
        with contextlib.suppress(DomainError):  # gs normally refuses the file access and fails
            ingest_document(
                fixtures_dir / "malicious" / "ps_read_passwd.ps", tmp_path / "doc", config=cfg
            )
            # if gs recovered, nothing from /etc/passwd may be in the output
            with pymupdf.open(tmp_path / "doc" / "canonical.pdf") as doc:
                assert "root:" not in "".join(p.get_text() for p in doc)
        assert not PWNED.exists(), "PostScript wrote outside the job directory"
    finally:
        PWNED.unlink(missing_ok=True)
