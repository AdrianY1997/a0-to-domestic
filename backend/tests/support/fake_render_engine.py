"""Stand-in for ``a0td.render.engine`` used by the API tests (selected with A0TD_RENDER_ENGINE).

Behaviour is chosen with the ``FAKE_RENDER_MODE`` environment variable:

* ``ok`` (default): real plans from the planner, a small real PDF (and a zip when requested).
* ``slow``: reports progress for a minute and leaves a grandchild process (cancel / timeout tests).
* ``fail``: raises ``DomainError(CONTENT_AREA_TOO_SMALL)``.
* ``crash``: exits abruptly without a message.
* ``bigline``: emits many progress lines far longer than the manager's stdout limit.
* ``orphan``: leaves a long-lived grandchild behind and then finishes normally.
* ``weird_name``: reports an output name with path separators.
"""

import io
import os
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image

from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.planner import plan as make_plan
from a0td.domain.settings import JobSettings, OutputFormat, PrinterProfile, parse_page_ranges
from a0td.ingest import IngestResult


@dataclass
class RenderResult:
    outputs: list[dict[str, Any]] = field(default_factory=list)
    plans: list[Any] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


def render_job(
    doc_dir: Path,
    settings: JobSettings,
    out_dir: Path,
    *,
    printer: PrinterProfile | None = None,
    exact_scale: bool = False,
    test_sheet_only: bool = False,
    progress: Any = None,
) -> RenderResult:
    mode = os.environ.get("FAKE_RENDER_MODE", "ok")
    if mode == "crash":
        os._exit(3)
    if mode == "fail":
        raise DomainError(ErrorCode.CONTENT_AREA_TOO_SMALL, "fake failure", params={"x": 1})
    if mode == "bigline" and progress:
        for i in range(30):
            progress("render", i, "x" * 200_000, {})
    if mode == "orphan":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        pid_file = os.environ.get("FAKE_PID_FILE")
        if pid_file:
            Path(pid_file).write_text(str(child.pid))
    if mode == "slow":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        pid_file = os.environ.get("FAKE_PID_FILE")
        if pid_file:
            Path(pid_file).write_text(str(child.pid))
        for i in range(600):
            if progress:
                progress("render", i / 6, "render.sheet", {"k": i, "n": 600})
            time.sleep(0.1)

    meta = IngestResult.load(doc_dir)
    numbers = parse_page_ranges(settings.source.pages, len(meta.pages))
    plans = [
        make_plan(
            meta.pages[n - 1].to_geometry(len(meta.pages)),
            settings,
            printer=printer,
            exact_scale=exact_scale,
        )
        for n in numbers
    ]
    printed = sum(p.counts.printed for p in plans)
    if test_sheet_only:
        printed = min(printed, 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = "tiles ñ.pdf"
    with pymupdf.open() as pdf:
        for i in range(max(1, printed)):
            page = pdf.new_page(width=595, height=842)
            page.insert_text((72, 72), f"sheet {i + 1}")
        pdf.save(out_dir / name)
    outputs: list[dict[str, Any]] = [
        {"name": "../evil/x.pdf" if mode == "weird_name" else name, "kind": "pdf", "pages": printed}
    ]
    if mode == "weird_name":
        (out_dir / "x.pdf").write_bytes((out_dir / name).read_bytes())
    if OutputFormat.PNG_ZIP in settings.output.formats:
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
        with zipfile.ZipFile(out_dir / "sheets.zip", "w") as zf:
            zf.writestr("sheet-1.png", buf.getvalue())
        outputs.append({"name": "sheets.zip", "kind": "zip", "pages": None})
    if progress:
        progress("render", 100, "render.done", {})
    return RenderResult(
        outputs=outputs,
        plans=plans,
        warnings=[
            {
                "code": "FAKE",
                "exact_scale": exact_scale,
                "test_sheet_only": test_sheet_only,
                "printer": printer.id if printer else None,
            }
        ],
    )


def render_sheet_preview(
    doc_dir: Path,
    settings: JobSettings,
    page_index: int,
    sheet_id: str,
    max_px: int,
    **_: object,
) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (max_px // 8 or 8, 8), "white").save(buf, format="PNG")
    return buf.getvalue()
