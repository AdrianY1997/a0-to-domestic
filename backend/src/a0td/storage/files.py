"""File layout under ``A0TD_DATA_DIR`` (SPEC 11.2).

::

    app.db
    documents/{doc_id}/original.<ext> · canonical.pdf | page-{n}.tif · pages/{n}/... · meta.json
    jobs/{job_id}/output/*.pdf|*.zip · log.jsonl
    {tmp}/{job_id}/        (temporary; deleted when the job ends)

Ids are always validated as UUIDs and file names inside a directory are validated as plain
names, so a client-supplied string can never steer a path (SPEC 11.2, 14).
"""

import re
import shutil
import uuid
from pathlib import Path

from a0td.config import AppConfig
from a0td.domain.errors import DomainError, ErrorCode

ORIGINAL_STEM = "original"
DOCUMENT_INFO = "document.json"  # {"id", "name"}: the display name, for output name templates
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
_NAME_FORBIDDEN = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def new_id() -> str:
    return str(uuid.uuid4())


def valid_id(value: str) -> str | None:
    """Canonical form of a UUID string, or ``None``."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


def safe_ext(filename: str | None) -> str:
    """Lowercase alphanumeric extension of ``filename`` (at most 8 chars) or ``bin``."""
    if not filename:
        return "bin"
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in base:
        return "bin"
    suffix = base.rpartition(".")[2].lower()
    return suffix if _EXT_RE.match(suffix) else "bin"


def display_name(raw: str | None, default: str = "document") -> str:
    """A client file name reduced to something harmless to show and to put in a header."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    return name[:200] or default


def safe_file_name(raw: str, default: str = "output") -> str:
    """A plain file name usable on Linux and Windows (no separators, no reserved characters)."""
    name = _NAME_FORBIDDEN.sub("_", raw.replace("\\", "/").rsplit("/", 1)[-1]).strip(" .")
    return name[:200] or default


STAGING_DIR = ".staging"  # CAD re-ingest renders here before replacing the document files


class Files:
    """Resolves every path the app uses. Constructing it touches nothing on disk."""

    def __init__(self, config: AppConfig) -> None:
        self.data_dir = Path(config.data_dir)
        self.tmp_dir = Path(config.tmp_dir)

    # -- roots
    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.documents_dir, self.jobs_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -- ids
    @staticmethod
    def _checked(item_id: str) -> str:
        canonical = valid_id(item_id)
        if canonical is None:
            raise DomainError(ErrorCode.NOT_FOUND, "not found")
        return canonical

    # -- documents
    def doc_dir(self, doc_id: str) -> Path:
        return self.documents_dir / self._checked(doc_id)

    def original_path(self, doc_id: str, ext: str) -> Path:
        ext = ext if _EXT_RE.match(ext) else "bin"
        return self.doc_dir(doc_id) / f"{ORIGINAL_STEM}.{ext}"

    def find_original(self, doc_id: str) -> Path | None:
        found = sorted(self.doc_dir(doc_id).glob(f"{ORIGINAL_STEM}.*"))
        return found[0] if found else None

    def page_dir(self, doc_id: str, index: int) -> Path:
        return self.doc_dir(doc_id) / "pages" / str(int(index))

    def reset_document_artifacts(self, doc_id: str) -> None:
        """Delete everything ingest produced (keeps the original upload)."""
        d = self.doc_dir(doc_id)
        for name in ("pages", ".work"):
            shutil.rmtree(d / name, ignore_errors=True)
        for p in [*d.glob("page-*.tif"), d / "canonical.pdf", d / "meta.json"]:
            p.unlink(missing_ok=True)

    def remove_document(self, doc_id: str) -> None:
        shutil.rmtree(self.doc_dir(doc_id), ignore_errors=True)

    # -- jobs
    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / self._checked(job_id)

    def output_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "output"

    def log_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "log.jsonl"

    def thumbs_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "thumbs"

    def job_tmp(self, job_id: str) -> Path:
        return self.tmp_dir / self._checked(job_id)

    def output_path(self, job_id: str, name: str) -> Path:
        """Path of an output file; ``name`` must be a plain name inside ``output/``."""
        out = self.output_dir(job_id)
        if safe_file_name(name) != name or name in {".", ".."}:
            raise DomainError(ErrorCode.NOT_FOUND, "output not found")
        path = out / name
        if path.resolve().parent != out.resolve():
            raise DomainError(ErrorCode.NOT_FOUND, "output not found")
        return path

    def remove_job(self, job_id: str) -> None:
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
        shutil.rmtree(self.job_tmp(job_id), ignore_errors=True)

    def clear_tmp(self) -> None:
        """Remove every ``{tmp}/{uuid}`` directory (startup: no job is running yet)."""
        if not self.tmp_dir.is_dir():
            return
        for child in self.tmp_dir.iterdir():
            if child.is_dir() and valid_id(child.name):
                shutil.rmtree(child, ignore_errors=True)

    # -- health
    def writable(self) -> bool:
        return self.data_dir.is_dir() and _can_write(self.data_dir)


def _can_write(directory: Path) -> bool:
    probe = directory / f".write-test-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


def like_pattern(text: str) -> str:
    """``%text%`` for a LIKE with ``escape="\\"``: user input never acts as a wildcard."""
    escaped = text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
