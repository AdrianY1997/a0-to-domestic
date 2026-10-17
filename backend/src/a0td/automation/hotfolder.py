"""Watched folder (SPEC CLI-02), enabled with ``A0TD_HOTFOLDER=true``.

::

    /data/inbox/<preset>/file.pdf   ->  /data/outbox/<preset>/<rendered outputs>
    /data/inbox/file.pdf            ->  /data/outbox/<rendered outputs>   (default settings)
    originals                       ->  <folder>/.done/  or  <folder>/.error/  (+ "<name>.log")

``<preset>`` is a preset id (``builtin-real-a4``) or a preset name, case-insensitive. The folder is
**polled** every ``A0TD_HOTFOLDER_INTERVAL_S`` seconds (inotify does not cross Docker Desktop bind
mounts on Windows). A file is picked up when its size and mtime did not change between two
consecutive polls, so files that are still being copied are left alone.

Each file goes through the normal pipeline (ingest job, render job) of the job manager, so the
limits, the worker isolation and the history are the same as for uploads.
"""

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from a0td.api.deps import Services
from a0td.domain.errors import ErrorCode
from a0td.domain.settings import JobSettings, Preset, merge_settings
from a0td.jobs.exact_scale import is_exact_scale
from a0td.storage import models as m
from a0td.storage import repo
from a0td.storage.files import display_name, new_id, safe_ext, safe_file_name

log = logging.getLogger(__name__)

INBOX = "inbox"
OUTBOX = "outbox"
DONE = ".done"
ERROR = ".error"
POLL_JOB_S = 0.25
_RESERVED = {DONE, ERROR}
MAX_MOVE_ATTEMPTS = 3


class HotFolderError(Exception):
    """A file that cannot be processed; ``code`` is an :class:`ErrorCode` value or a tag."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class _Stuck:
    """A processed file whose archive move failed: never reprocessed, the move is retried."""

    sig: tuple[int, int]
    error: tuple[str, str] | None
    delivered: list[str]
    attempts: int = 1
    gave_up: bool = False


@dataclass(frozen=True, slots=True)
class Candidate:
    path: Path
    folder: Path  # the inbox folder it was found in
    preset: str | None  # folder name, ``None`` for the inbox root


def _unique(directory: Path, name: str) -> Path:
    """``directory/name``, or ``name (2).ext`` ... when it exists."""
    target = directory / name
    stem, suffix = target.stem, target.suffix
    n = 1
    while target.exists():
        n += 1
        target = directory / f"{stem} ({n}){suffix}"
    return target


class HotFolder:
    def __init__(
        self,
        services: Services,
        *,
        interval_s: float | None = None,
        job_timeout_s: float | None = None,
    ) -> None:
        self.svc = services
        cfg = services.config
        self.interval_s = interval_s if interval_s is not None else cfg.hotfolder_interval_s
        self.job_timeout_s = job_timeout_s if job_timeout_s is not None else cfg.job_timeout_s * 2
        self.inbox = Path(cfg.data_dir) / INBOX
        self.outbox = Path(cfg.data_dir) / OUTBOX
        self._seen: dict[Path, tuple[int, int]] = {}
        self._busy: set[Path] = set()
        self._stuck: dict[Path, _Stuck] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._slots = asyncio.Semaphore(max(1, cfg.workers))

    # ------------------------------------------------------------------ polling

    async def run(self) -> None:
        """Poll forever (cancel to stop)."""
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.outbox.mkdir(parents=True, exist_ok=True)
        log.info("hot folder watching %s every %.1f s", self.inbox, self.interval_s)
        try:
            while True:
                try:
                    await self.poll()
                except Exception:
                    log.exception("hot folder poll failed")
                await asyncio.sleep(self.interval_s)
        finally:
            for task in list(self._tasks):
                task.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)

    async def poll(self) -> list[asyncio.Task[None]]:
        """One poll: start a task for every file that is stable since the previous poll."""
        ready = await asyncio.to_thread(self._stable_files)
        started: list[asyncio.Task[None]] = []
        for cand in ready:
            self._busy.add(cand.path)
            task = asyncio.create_task(self._guarded(cand), name=f"a0td-hotfolder-{cand.path.name}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            started.append(task)
        return started

    def _stable_files(self) -> list[Candidate]:
        current: dict[Path, tuple[int, int]] = {}
        ready: list[Candidate] = []
        for cand in self._candidates():
            try:
                st = cand.path.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime_ns)
            current[cand.path] = sig
            if cand.path in self._busy:
                continue
            stuck = self._stuck.get(cand.path)
            if stuck is not None:
                if stuck.sig == sig:
                    self._retry_move(cand, stuck)
                    continue
                del self._stuck[cand.path]  # a different file with the same name
            if self._seen.get(cand.path) == sig and st.st_size > 0:
                ready.append(cand)
        self._seen = current  # forgets files that vanished or were moved
        for gone in [p for p in self._stuck if p not in current]:
            del self._stuck[gone]
        return ready

    def _candidates(self) -> list[Candidate]:
        out: list[Candidate] = []
        try:
            entries = sorted(self.inbox.iterdir())
        except OSError:
            return out
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_file():
                out.append(Candidate(entry, self.inbox, None))
            elif entry.is_dir() and entry.name not in _RESERVED:
                try:
                    inner = sorted(entry.iterdir())
                except OSError:
                    continue
                out.extend(
                    Candidate(f, entry, entry.name)
                    for f in inner
                    if f.is_file() and not f.name.startswith(".") and not f.is_symlink()
                )
        return out

    # --------------------------------------------------------------- one file

    async def _guarded(self, cand: Candidate) -> None:
        try:
            async with self._slots:
                await self._process(cand)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("hot folder: unexpected failure on %s", cand.path)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self._finish, cand, None, ("INTERNAL_ERROR", "unexpected error")
                )
        finally:
            self._busy.discard(cand.path)

    async def _process(self, cand: Candidate) -> None:
        try:
            outputs = await self._convert(cand)
        except HotFolderError as exc:
            log.warning("hot folder: %s failed: %s %s", cand.path.name, exc.code, exc.detail)
            await asyncio.to_thread(self._finish, cand, None, (exc.code, exc.detail))
            return
        await asyncio.to_thread(self._finish, cand, outputs, None)

    def _preset(self, name: str | None) -> tuple[Preset | None, str | None]:
        if name is None:
            return None, None
        with self.svc.db.session() as s:
            found = repo.get_preset(s, name)
            if found is None:
                found = next(
                    (p for p in repo.list_presets(s) if p.name.casefold() == name.casefold()), None
                )
        if found is None:
            raise HotFolderError("UNKNOWN_PRESET", f"no preset named or identified as {name!r}")
        return found, found.id

    async def _convert(self, cand: Candidate) -> list[Path]:
        preset, preset_id = await asyncio.to_thread(self._preset, cand.preset)
        doc_id = await asyncio.to_thread(self._import, cand.path)
        ingest_id = await asyncio.to_thread(self._create_ingest, doc_id, cand.path.name)
        await self._run_job(ingest_id)
        base = preset.apply() if preset is not None else JobSettings()
        settings = merge_settings({"document_id": doc_id}, base)
        render_id = new_id()
        with self.svc.db.session() as s:
            repo.create_job(
                s,
                self.svc.config,
                job_id=render_id,
                document_id=doc_id,
                kind=m.KIND_RENDER,
                settings=settings.model_dump(mode="json"),
                options={
                    "test_sheet_only": False,
                    "exact_scale": is_exact_scale(settings, preset_id),
                    "preset_id": preset_id,
                    "source": "hotfolder",
                },
            )
        job = await self._run_job(render_id)
        outputs = json.loads(job.outputs_json or "[]")
        return [self.svc.files.output_path(render_id, str(o["name"])) for o in outputs]

    def _import(self, path: Path) -> str:
        """Copy the file into a new document directory (never trusts the file name)."""
        cfg = self.svc.config
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise HotFolderError("NOT_A_FILE", "not a regular file")
        size = before.st_size
        if size > cfg.max_upload_mb * 1024 * 1024:
            raise HotFolderError(
                ErrorCode.FILE_TOO_LARGE.value, f"larger than {cfg.max_upload_mb} MB"
            )
        doc_id = new_id()
        self.svc.files.ensure_dirs()
        doc_dir = self.svc.files.doc_dir(doc_id)
        doc_dir.mkdir(parents=True)
        name = display_name(path.name)
        sha = hashlib.sha256()
        try:
            with (
                _open_regular(path, before) as src,
                self.svc.files.original_path(doc_id, safe_ext(name)).open("wb") as dst,
            ):
                while chunk := src.read(1024 * 1024):
                    sha.update(chunk)
                    dst.write(chunk)
            (doc_dir / "document.json").write_text(
                json.dumps({"id": doc_id, "name": name}, ensure_ascii=False), encoding="utf-8"
            )
            with self.svc.db.session() as s:
                repo.create_document(
                    s, cfg, doc_id=doc_id, name=name, size_bytes=size, sha256=sha.hexdigest()
                )
        except BaseException:
            shutil.rmtree(doc_dir, ignore_errors=True)
            raise
        return doc_id

    def _create_ingest(self, doc_id: str, filename: str) -> str:
        job_id = new_id()
        with self.svc.db.session() as s:
            repo.create_job(
                s, self.svc.config, job_id=job_id, document_id=doc_id, kind=m.KIND_INGEST
            )
        return job_id

    async def _run_job(self, job_id: str) -> m.Job:
        """Queue ``job_id`` and wait for its final status. Raises on failure."""
        self.svc.manager.enqueue(job_id)
        deadline = asyncio.get_running_loop().time() + self.job_timeout_s
        while True:
            job = await asyncio.to_thread(self._job, job_id)
            if job.status in m.FINISHED_JOB_STATUSES:
                break
            if asyncio.get_running_loop().time() > deadline:
                await self.svc.manager.cancel(job_id)
                raise HotFolderError(ErrorCode.CONVERSION_TIMEOUT.value, "the job took too long")
            await asyncio.sleep(POLL_JOB_S)
        if job.status != m.JOB_SUCCEEDED:
            raise HotFolderError(
                job.error_code or ErrorCode.CONVERSION_FAILED.value,
                job.error_detail or f"job {job.status}",
            )
        return job

    def _job(self, job_id: str) -> m.Job:
        with self.svc.db.session() as s:
            job = s.get(m.Job, job_id)
            if job is None:
                raise HotFolderError(ErrorCode.NOT_FOUND.value, "the job disappeared")
            return job

    # ------------------------------------------------------------------ results

    def _finish(
        self,
        cand: Candidate,
        outputs: list[Path] | None,
        error: tuple[str, str] | None,
    ) -> None:
        """Deliver the outputs and move the original to ``.done`` or ``.error`` (+ log)."""
        delivered: list[str] = []
        if error is None and outputs is not None:
            dest_dir = self.outbox / safe_file_name(cand.preset) if cand.preset else self.outbox
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                for out in outputs:
                    target = _unique(dest_dir, safe_file_name(out.name))
                    shutil.copy2(out, target)
                    delivered.append(str(target.relative_to(self.outbox)))
            except OSError as exc:
                error = ("OUTBOX_WRITE_FAILED", str(exc))
        self._archive(cand, error, delivered)

    def _archive(
        self, cand: Candidate, error: tuple[str, str] | None, delivered: list[str]
    ) -> bool:
        """Move the original to ``.done`` / ``.error`` and write its log; False if it stays."""
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        folder = cand.folder / (DONE if error is None else ERROR)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = _unique(folder, cand.path.name)
            shutil.move(os.fspath(cand.path), os.fspath(target))
        except OSError:
            stuck = self._stuck.get(cand.path)
            if stuck is None:
                log.exception("hot folder: cannot move %s (will retry)", cand.path)
                with contextlib.suppress(OSError):
                    st = cand.path.stat()
                    self._stuck[cand.path] = _Stuck((st.st_size, st.st_mtime_ns), error, delivered)
            return False
        self._stuck.pop(cand.path, None)
        lines = [f"{stamp} {cand.path.name}"]
        if error is None:
            lines.append("status: done")
            lines.extend(f"output: {d}" for d in delivered)
        else:
            lines.append("status: error")
            lines.append(f"code: {error[0]}")
            lines.append(f"detail: {error[1]}")
        with contextlib.suppress(OSError):
            target.with_name(target.name + ".log").write_text("\n".join(lines) + "\n", "utf-8")
        log.info("hot folder: %s -> %s", cand.path.name, "error" if error else "done")
        return True

    def _retry_move(self, cand: Candidate, stuck: _Stuck) -> None:
        """Bounded retries of a failed move; after the last one it is logged once and left."""
        if stuck.gave_up:
            return
        stuck.attempts += 1
        if self._archive(cand, stuck.error, stuck.delivered):
            return
        if stuck.attempts >= MAX_MOVE_ATTEMPTS:
            stuck.gave_up = True
            log.error(
                "hot folder: giving up moving %s after %d attempts; it stays in the inbox "
                "and will not be processed again",
                cand.path,
                stuck.attempts,
            )


def _open_regular(path: Path, before: os.stat_result) -> "io.BufferedReader":
    """Open without following symlinks and check it is the file that was inspected (TOCTOU)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        after = os.fstat(fd)
        same = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        if not stat.S_ISREG(after.st_mode) or not same:
            raise HotFolderError("FILE_CHANGED", "the file changed while it was being imported")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
