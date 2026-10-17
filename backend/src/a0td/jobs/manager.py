"""Job manager (SPEC 9.5): asyncio scheduler over subprocess workers.

* Concurrency: at most ``A0TD_WORKERS`` workers at once (semaphore).
* Each job runs ``python -m a0td.jobs.worker --job <id>`` with ``RLIMIT_AS``, ``nice 10`` (set by
  the worker itself), a private temp dir and its own process group. The job spec is sent on stdin.
* The worker speaks JSON lines on stdout; this manager is the **only SQLite writer** for job state
  and fans progress out to SSE subscribers (:class:`EventHub`).
* Cancel = SIGTERM to the process group, SIGKILL after 5 s.
* Startup recovery: ``running`` jobs become ``failed`` (``INTERRUPTED``), ``queued`` ones are
  queued again.

:class:`JobQueue` is the abstract interface, in case a Redis/RQ backend is ever wanted.
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select

from a0td.config import AppConfig
from a0td.domain.errors import ErrorCode
from a0td.domain.settings import JobSettings
from a0td.jobs.events import (
    KEEPALIVE,
    EventHub,
    JobEvent,
    done_event,
    error_event,
    progress_event,
)
from a0td.jobs.limits import child_env, python_executable
from a0td.storage import models as m
from a0td.storage import repo
from a0td.storage.db import Database
from a0td.storage.files import STAGING_DIR, Files
from a0td.storage.models import dumps, loads, utcnow

log = logging.getLogger(__name__)

KILL_GRACE_S = 5.0
KEEPALIVE_S = 15.0
STDOUT_LIMIT = 256 * 1024 * 1024  # a result line can carry the plans of every page
LOG_CAP_BYTES = 2 * 1024 * 1024
_PRINT_OPTIONS = ("ipp_uri", "media", "copies", "monochrome", "pages", "job_name")
PASSWORD_CODES = {ErrorCode.PASSWORD_REQUIRED.value, ErrorCode.PASSWORD_INVALID.value}


class JobQueue(Protocol):
    """What the API needs from a job backend."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def enqueue(self, job_id: str, *, password: str | None = None) -> None: ...

    async def cancel(self, job_id: str, *, wait: bool = False) -> bool: ...

    def events(self, job_id: str) -> AsyncIterator[JobEvent | None]: ...


class JobObserver(Protocol):
    """Receives every finished job (Prometheus metrics, ADM-04)."""

    def observe_job(self, kind: str, status: str, seconds: float | None) -> None: ...


@dataclass(slots=True)
class _Run:
    """Mutable state of one running job."""

    job_id: str
    kind: str
    document_id: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    stage: str = ""
    last_write: float = 0.0
    log_bytes: int = 0
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=20))
    timed_out: bool = False


def terminal_event(job: m.Job) -> JobEvent | None:
    """The terminal SSE event of a finished job (``None`` while queued or running)."""
    if job.status == m.JOB_SUCCEEDED:
        return done_event("succeeded")
    if job.status == m.JOB_CANCELLED:
        return error_event(ErrorCode.JOB_CANCELLED.value, "job cancelled")
    if job.status == m.JOB_FAILED:
        return error_event(
            job.error_code or ErrorCode.CONVERSION_FAILED.value,
            job.error_detail or "job failed",
            loads(job.error_params_json, {}),
        )
    return None


def _signal_group(worker: "_Worker", *, force: bool) -> None:
    """Signal the whole process group of a worker (best effort)."""
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows hosts only
            if force:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(worker.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                worker.popen.terminate()
        else:
            os.killpg(worker.pid, signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass


class _Worker:
    """A worker subprocess whose reaping we control.

    ``asyncio`` subprocesses reap the leader as soon as it exits, after which its pid (= the
    process group id) may be recycled and a late ``killpg`` could hit an unrelated group. Here the
    exit is only *observed* (``waitid`` with ``WNOWAIT``: the leader stays a zombie, so the pgid
    cannot be reused) and the group is always killed *before* the leader is reaped.
    """

    def __init__(self, popen: "subprocess.Popen[bytes]") -> None:
        self.popen = popen
        self.pid = popen.pid
        self.stdout: asyncio.StreamReader | None = None
        self.stderr: asyncio.StreamReader | None = None
        self._transports: list[asyncio.BaseTransport] = []
        self._exit: asyncio.Future[None] | None = None
        self._reap: asyncio.Future[int] | None = None

    async def attach(self, limit: int) -> None:
        """Connect the pipes to asyncio streams and start watching for the exit."""
        loop = asyncio.get_running_loop()
        assert self.popen.stdout is not None
        assert self.popen.stderr is not None
        readers: list[asyncio.StreamReader] = []
        for pipe in (self.popen.stdout, self.popen.stderr):
            reader = asyncio.StreamReader(limit=limit)
            transport, _ = await loop.connect_read_pipe(
                lambda reader=reader: asyncio.StreamReaderProtocol(reader), pipe
            )
            self._transports.append(transport)
            readers.append(reader)
        self.stdout, self.stderr = readers
        self._exit = asyncio.ensure_future(asyncio.to_thread(self._block_until_exit))

    def _block_until_exit(self) -> None:
        try:
            if os.name == "nt":  # pragma: no cover
                self.popen.wait()
            else:
                os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOWAIT)
        except (ChildProcessError, OSError):
            pass

    @property
    def returncode(self) -> int | None:
        return self.popen.returncode

    @property
    def exited(self) -> bool:
        return self._exit is not None and self._exit.done()

    async def wait_exit(self) -> None:
        """Wait until the leader has exited (it is not reaped yet)."""
        if self._exit is not None:
            await asyncio.shield(self._exit)

    def terminate(self) -> None:
        """SIGTERM to the group, unless the leader was already reaped."""
        if self._reap is None:
            _signal_group(self, force=False)

    def kill_and_reap(self) -> "asyncio.Future[int]":
        """SIGKILL the group, then reap the leader. Idempotent; the kill always comes first."""
        if self._reap is None:
            _signal_group(self, force=True)
            self._reap = asyncio.ensure_future(asyncio.to_thread(self.popen.wait))
        return asyncio.shield(self._reap)

    def close_pipes(self) -> None:
        for transport in self._transports:
            transport.close()
        self._transports.clear()


def _feed_stdin(popen: "subprocess.Popen[bytes]", data: bytes) -> None:
    try:
        assert popen.stdin is not None
        popen.stdin.write(data)
        popen.stdin.close()
    except (OSError, ValueError):
        pass


class JobManager:
    """asyncio job scheduler. Create once per app; ``start()`` inside the running loop."""

    def __init__(
        self,
        config: AppConfig,
        db: Database,
        files: Files,
        *,
        worker_module: str = "a0td.jobs.worker",
    ) -> None:
        self.config = config
        self.db = db
        self.files = files
        self.hub = EventHub()
        self._worker_module = worker_module
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[str] | None = None
        self._sem: asyncio.Semaphore | None = None
        self._dispatcher: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._procs: dict[str, _Worker] = {}
        self._cancel_requested: set[str] = set()
        self._secrets: dict[str, str] = {}
        self._finished: dict[str, asyncio.Event] = {}
        self._pending: list[str] = []
        self.metrics: JobObserver | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._sem = asyncio.Semaphore(self.config.workers)
        queued = self._recover()
        for job_id in [*queued, *self._pending]:
            self._queue.put_nowait(job_id)
        self._pending.clear()
        self._dispatcher = asyncio.create_task(self._dispatch(), name="a0td-dispatcher")

    async def stop(self) -> None:
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatcher
            self._dispatcher = None
        for proc in list(self._procs.values()):
            proc.kill_and_reap()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _recover(self) -> list[str]:
        """Fail interrupted jobs, return the ids of queued ones in creation order."""
        now = utcnow()
        with self.db.session() as s:
            for job in s.scalars(select(m.Job).where(m.Job.status == m.JOB_RUNNING)):
                job.status = m.JOB_FAILED
                job.error_code = ErrorCode.INTERRUPTED.value
                job.error_detail = "the server restarted while the job was running"
                job.finished_at = now
                if job.kind == m.KIND_INGEST:
                    self._fail_ingest(s, job, ErrorCode.INTERRUPTED.value, job.error_detail, {})
                else:
                    self._drop_partial_outputs(job.id)
            s.flush()
            queued = list(
                s.scalars(
                    select(m.Job.id)
                    .where(m.Job.status == m.JOB_QUEUED)
                    .order_by(m.Job.created_at, m.Job.id)
                )
            )
            # A "processing" document whose ingest job is gone can never finish.
            queued_docs = set(
                s.scalars(
                    select(m.Job.document_id).where(
                        m.Job.status == m.JOB_QUEUED, m.Job.kind == m.KIND_INGEST
                    )
                )
            )
            for doc in s.scalars(select(m.Document).where(m.Document.status == m.DOC_PROCESSING)):
                if doc.id not in queued_docs:
                    self._fail_document(
                        s, doc.id, ErrorCode.INTERRUPTED.value, "ingest was interrupted", {}
                    )
                    self.files.reset_document_artifacts(doc.id)
        self.files.clear_tmp()
        return queued

    # ---------------------------------------------------------------------- API

    def enqueue(self, job_id: str, *, password: str | None = None) -> None:
        """Queue a job. Thread-safe (endpoints may run in the thread pool)."""
        if password:
            self._secrets[job_id] = password
        if self._loop is None or self._queue is None:
            self._pending.append(job_id)
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, job_id)

    async def cancel(self, job_id: str, *, wait: bool = False) -> bool:
        """Cancel a queued or running job. Returns ``False`` if it is unknown or finished."""
        with self.db.session() as s:
            job = s.get(m.Job, job_id)
            if job is None or job.status not in m.ACTIVE_JOB_STATUSES:
                return False
            if job.status == m.JOB_QUEUED:
                self._secrets.pop(job_id, None)
                job.status = m.JOB_CANCELLED
                job.finished_at = utcnow()
                job.error_code = ErrorCode.JOB_CANCELLED.value
                job.error_detail = "job cancelled"
                if job.kind == m.KIND_INGEST:
                    self._fail_ingest(
                        s, job, ErrorCode.JOB_CANCELLED.value, "ingest cancelled", {}, reset=False
                    )
                cancelled_queued = True
            else:
                cancelled_queued = False
        if cancelled_queued:
            self.hub.publish(job_id, terminal_event_cancelled())
            return True
        self._cancel_requested.add(job_id)
        proc = self._procs.get(job_id)
        if proc is not None:
            task = asyncio.create_task(self._terminate(proc))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        if wait:
            event = self._finished.get(job_id)
            if event is not None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(event.wait(), KILL_GRACE_S * 3)
        return True

    async def events(self, job_id: str) -> AsyncIterator[JobEvent | None]:
        """Events of a job until its terminal one. ``None`` items are keep-alive ticks."""
        queue = self.hub.subscribe(job_id)
        try:
            with self.db.session() as s:
                job = s.get(m.Job, job_id)
                terminal = terminal_event(job) if job is not None else None
            if job is None:
                yield error_event(ErrorCode.NOT_FOUND.value, "job not found")
                return
            if terminal is not None:
                yield terminal
                return
            last = self.hub.last(job_id)
            if last is not None:
                yield last
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
                except TimeoutError:
                    yield None
                    continue
                yield event
                if event.terminal:
                    return
        finally:
            self.hub.unsubscribe(job_id, queue)

    # ----------------------------------------------------------------- scheduling

    async def _dispatch(self) -> None:
        assert self._queue is not None
        assert self._sem is not None
        while True:
            await self._sem.acquire()
            try:
                job_id = await self._queue.get()
            except BaseException:
                self._sem.release()
                raise
            task = asyncio.create_task(self._run_guarded(job_id), name=f"a0td-job-{job_id}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_guarded(self, job_id: str) -> None:
        assert self._sem is not None
        try:
            await self._run(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("job crashed in the manager", extra={"job_id": job_id})
            with contextlib.suppress(Exception):
                self._finish_failed(job_id, ErrorCode.CONVERSION_FAILED.value, "internal error", {})
        finally:
            self._sem.release()

    # ------------------------------------------------------------------ one job

    def _build_spec(self, job: m.Job, session: Any) -> dict[str, Any]:
        spec: dict[str, Any] = {"job_id": job.id, "document_id": job.document_id, "kind": job.kind}
        spec["doc_dir"] = str(self.files.doc_dir(job.document_id))
        if job.kind == m.KIND_INGEST:
            doc = session.get(m.Document, job.document_id)
            original = self.files.find_original(job.document_id)
            if doc is None or original is None:
                raise FileNotFoundError("the uploaded file is gone")
            spec["source"] = str(original)
            spec["filename"] = doc.name
            spec["password"] = self._secrets.pop(job.id, None)
            # DXF / DWG options (layout, units, scale, colour) chosen at upload or re-ingest.
            options = loads(job.options_json, {}) or {}
            spec["cad"] = options.get("cad")
            spec["reingest"] = bool(options.get("reingest"))
        elif job.kind == m.KIND_PRINT:
            options = loads(job.options_json, {}) or {}
            outputs = loads(session.get(m.Job, options["render_job_id"]).outputs_json, [])
            spec["pdf"] = str(self.files.output_path(options["render_job_id"], options["output"]))
            spec.update({k: options.get(k) for k in _PRINT_OPTIONS})
            if not outputs:
                raise FileNotFoundError("the rendered output is gone")
        else:
            settings = JobSettings.model_validate(loads(job.settings_json, {}))
            options = loads(job.options_json, {}) or {}
            printer = repo.get_printer(session, settings.target.printer_id)
            spec["settings"] = settings.model_dump(mode="json")
            spec["printer"] = printer.model_dump(mode="json") if printer else None
            spec["out_dir"] = str(self.files.output_dir(job.id))
            spec["exact_scale"] = bool(options.get("exact_scale"))
            spec["test_sheet_only"] = bool(options.get("test_sheet_only"))
        return spec

    async def _run(self, job_id: str) -> None:
        finished = self._finished.setdefault(job_id, asyncio.Event())
        try:
            with self.db.session() as s:
                job = s.get(m.Job, job_id)
                if job is None or job.status != m.JOB_QUEUED:
                    return
                job.status = m.JOB_RUNNING
                job.started_at = utcnow()
                job.stage = "starting"
                job.progress = 0.0
                run = _Run(job_id, job.kind, job.document_id)
                try:
                    spec = self._build_spec(job, s)
                except Exception:
                    log.exception("cannot build the job spec", extra={"job_id": job_id})
                    spec = None
            if spec is None:
                self._finish_failed(
                    job_id, ErrorCode.CORRUPT_FILE.value, "the job could not be prepared", {}
                )
                return
            self.hub.publish(job_id, progress_event("starting", 0))
            await self._execute(run, spec)
        finally:
            self._cleanup(job_id)
            finished.set()
            self._finished.pop(job_id, None)

    async def _execute(self, run: _Run, spec: dict[str, Any]) -> None:
        job_id = run.job_id
        job_dir = self.files.job_dir(job_id)
        tmp = self.files.job_tmp(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        tmp.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {}
        if os.name == "nt":  # pragma: no cover
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            popen = subprocess.Popen(
                [python_executable(), "-m", self._worker_module, "--job", job_id],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env(self.config, tmp),
                cwd=str(tmp),
                bufsize=0,
                **kwargs,
            )
        except OSError:
            log.exception("cannot start the worker", extra={"job_id": job_id})
            self._finish_failed(
                job_id, ErrorCode.CONVERSION_FAILED.value, "the worker could not be started", {}
            )
            return
        proc = _Worker(popen)
        self._procs[job_id] = proc
        pumps: list[asyncio.Task[None]] = []
        try:
            await proc.attach(STDOUT_LIMIT)
            assert proc.stdout
            assert proc.stderr
            data = json.dumps(spec, ensure_ascii=False).encode() + b"\n"
            feed = asyncio.create_task(asyncio.to_thread(_feed_stdin, popen, data))
            self._tasks.add(feed)
            feed.add_done_callback(self._tasks.discard)
            if job_id in self._cancel_requested:
                await self._terminate(proc)
            pumps = [
                asyncio.create_task(self._pump_stdout(run, proc)),
                asyncio.create_task(self._pump_stderr(run, proc)),
            ]
            try:
                async with asyncio.timeout(self.config.job_timeout_s):
                    await proc.wait_exit()
            except TimeoutError:
                run.timed_out = True
                await self._terminate(proc)
            # Drain what the worker wrote before exiting; orphans holding the pipes are killed.
            _, pending = await asyncio.wait(pumps, timeout=5)
            for task in pending:
                task.cancel()
            # Kill the group while the leader is still a zombie (its pgid cannot be reused yet),
            # and only then reap it.
            await proc.kill_and_reap()
        except asyncio.CancelledError:
            proc.kill_and_reap()
            for task in pumps:
                task.cancel()
            raise
        finally:
            self._procs.pop(job_id, None)
            proc.close_pipes()
        self._conclude(run, proc.returncode)

    # ------------------------------------------------------------------ pumps

    def _log_line(self, run: _Run, text: str) -> None:
        if run.log_bytes > LOG_CAP_BYTES:
            return
        run.log_bytes += len(text) + 1
        try:
            with self.files.log_path(run.job_id).open("a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass

    async def _pump_stdout(self, run: _Run, proc: _Worker) -> None:
        assert proc.stdout
        while True:
            try:
                raw = await proc.stdout.readline()
            except ValueError:  # line longer than the limit: protocol violation
                if run.error is None:
                    run.error = {
                        "code": ErrorCode.CONVERSION_FAILED.value,
                        "detail": "oversized output",
                    }
                continue  # keep draining (discarding) so the worker never blocks on a full pipe
            if not raw:
                return
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                run.stderr_tail.append(line[:300])
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "progress":
                self._log_line(run, line)
                self._on_progress(run, msg)
            elif kind == "result":
                run.result = msg
                self._log_line(run, json.dumps({"type": "result"}))
            elif kind == "error":
                run.error = msg
                self._log_line(run, line)

    async def _pump_stderr(self, run: _Run, proc: _Worker) -> None:
        assert proc.stderr
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                return
            text = raw.decode("utf-8", "replace").rstrip()
            if text:
                run.stderr_tail.append(text[:300])
                self._log_line(run, json.dumps({"type": "stderr", "line": text[:500]}))

    def _on_progress(self, run: _Run, msg: dict[str, Any]) -> None:
        try:
            stage = str(msg.get("stage", ""))
            pct = max(0.0, min(100.0, float(msg.get("pct", 0))))
        except (TypeError, ValueError):
            return
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        self.hub.publish(run.job_id, progress_event(stage, pct, msg.get("message_key"), params))
        now = time.monotonic()
        if stage != run.stage or now - run.last_write >= 0.25:
            run.stage, run.last_write = stage, now
            with self.db.session() as s:
                job = s.get(m.Job, run.job_id)
                if job is not None and job.status == m.JOB_RUNNING:
                    job.stage, job.progress = stage, pct

    # --------------------------------------------------------------- termination

    async def _terminate(self, proc: _Worker) -> None:
        """SIGTERM the process group, SIGKILL after the grace period, then reap."""
        if not proc.exited:
            proc.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait_exit(), KILL_GRACE_S)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.kill_and_reap(), KILL_GRACE_S)

    # ------------------------------------------------------------------ outcome

    def _conclude(self, run: _Run, returncode: int | None) -> None:
        job_id = run.job_id
        if run.timed_out:
            self._finish_failed(
                job_id,
                ErrorCode.CONVERSION_TIMEOUT.value,
                f"the job exceeded {self.config.job_timeout_s} s",
                {"timeout_s": self.config.job_timeout_s},
            )
        elif run.result is not None and returncode == 0 and job_id not in self._cancel_requested:
            self._finish_succeeded(run)
        elif job_id in self._cancel_requested:
            self._finish_cancelled(run)
        elif run.error is not None:
            err = run.error
            self._finish_failed(
                job_id,
                str(err.get("code") or ErrorCode.CONVERSION_FAILED.value),
                str(err.get("detail") or "job failed"),
                dict(err["params"]) if isinstance(err.get("params"), dict) else {},
            )
        else:
            tail = run.stderr_tail[-1] if run.stderr_tail else ""
            log.error(
                "worker exited without a result",
                extra={"job_id": job_id, "returncode": returncode, "stderr": tail},
            )
            self._finish_failed(
                job_id,
                ErrorCode.CONVERSION_FAILED.value,
                f"the worker exited unexpectedly (code {returncode})",
                {"returncode": returncode},
            )

    def _observe(self, job: m.Job) -> None:
        """Feed the optional metrics observer (ADM-04) when a job reaches a final status."""
        if self.metrics is None:
            return
        seconds = None
        if job.started_at is not None and job.finished_at is not None:
            seconds = max(0.0, (job.finished_at - job.started_at).total_seconds())
        try:
            self.metrics.observe_job(job.kind, job.status, seconds)
        except Exception:  # metrics must never break a job
            log.exception("metrics observer failed")

    def _fail_ingest(
        self,
        s: Any,
        job: m.Job,
        code: str,
        detail: str,
        params: dict[str, Any],
        *,
        reset: bool = True,
    ) -> None:
        """An ingest job ended without a result.

        A CAD re-ingest (``reingest`` in the job options) renders into a staging directory, so
        the previous canonical files are intact: the document stays READY and the error lives on
        the job only. Any other ingest fails the document and removes what it produced.
        """
        if (loads(job.options_json, {}) or {}).get("reingest"):
            shutil.rmtree(self.files.doc_dir(job.document_id) / STAGING_DIR, ignore_errors=True)
            doc = s.get(m.Document, job.document_id)
            if doc is not None and doc.status == m.DOC_PROCESSING:
                doc.status = m.DOC_READY
                doc.error_code = doc.error_detail = doc.error_params_json = None
            return
        self._fail_document(s, job.document_id, code, detail, params)
        if reset:
            self.files.reset_document_artifacts(job.document_id)

    def _fail_document(
        self, s: Any, doc_id: str, code: str, detail: str, params: dict[str, Any]
    ) -> None:
        doc = s.get(m.Document, doc_id)
        if doc is None:
            return
        doc.status = m.DOC_PASSWORD_REQUIRED if code in PASSWORD_CODES else m.DOC_FAILED
        if code in PASSWORD_CODES:
            doc.was_encrypted = True
        doc.error_code, doc.error_detail = code, detail
        doc.error_params_json = dumps(params) if params else None

    def _finish_succeeded(self, run: _Run) -> None:
        assert run.result is not None
        res = run.result
        with self.db.session() as s:
            job = s.get(m.Job, run.job_id)
            if job is None:
                self._publish_gone(run.job_id)
                return
            job.status, job.stage, job.progress = m.JOB_SUCCEEDED, "done", 100.0
            job.finished_at = utcnow()
            job.error_code = job.error_detail = job.error_params_json = None
            self._observe(job)
            if run.kind == m.KIND_INGEST:
                doc = s.get(m.Document, run.document_id)
                if doc is not None:
                    doc.status = m.DOC_READY
                    doc.format, doc.mime = res.get("format"), res.get("mime")
                    doc.pages_json = dumps(res.get("pages", []))
                    doc.warnings_json = dumps(res.get("warnings", []))
                    doc.error_code = doc.error_detail = doc.error_params_json = None
            else:
                job.outputs_json = dumps(res.get("outputs", []))
                job.plans_json = dumps(res.get("plans", []))
                job.warnings_json = dumps(res.get("warnings", []))
                if run.kind == m.KIND_RENDER and res.get("ink"):  # COL-07
                    opts = loads(job.options_json, {}) or {}
                    opts["ink"] = res["ink"]
                    job.options_json = dumps(opts)
                if run.kind == m.KIND_PRINT:  # what the printer reported, for GET /jobs/{id}
                    opts = loads(job.options_json, {}) or {}
                    opts["printer_job_id"] = res.get("printer_job_id")
                    opts["printer_job_state"] = res.get("printer_job_state")
                    job.options_json = dumps(opts)
        self.hub.publish(run.job_id, done_event("succeeded"))

    def _finish_failed(self, job_id: str, code: str, detail: str, params: dict[str, Any]) -> None:
        with self.db.session() as s:
            job = s.get(m.Job, job_id)
            if job is None:
                self._publish_gone(job_id)
                return
            job.status, job.finished_at = m.JOB_FAILED, utcnow()
            job.error_code, job.error_detail = code, detail
            self._observe(job)
            job.error_params_json = dumps(params) if params else None
            if job.kind == m.KIND_INGEST:
                self._fail_ingest(s, job, code, detail, params)
            else:
                self._drop_partial_outputs(job_id)
        self.hub.publish(job_id, error_event(code, detail, params))

    def _finish_cancelled(self, run: _Run) -> None:
        with self.db.session() as s:
            job = s.get(m.Job, run.job_id)
            if job is None:
                self._publish_gone(run.job_id)
                return
            job.status, job.finished_at = m.JOB_CANCELLED, utcnow()
            job.error_code = ErrorCode.JOB_CANCELLED.value
            job.error_detail = "job cancelled"
            self._observe(job)
            if job.kind == m.KIND_INGEST:
                self._fail_ingest(s, job, ErrorCode.JOB_CANCELLED.value, "ingest cancelled", {})
            else:
                self._drop_partial_outputs(run.job_id)
        self.hub.publish(run.job_id, terminal_event_cancelled())

    def _publish_gone(self, job_id: str) -> None:
        """The row vanished while the job ran (deleted): still end SSE streams, free the hub."""
        self.hub.publish(job_id, terminal_event_cancelled())

    def _drop_partial_outputs(self, job_id: str) -> None:
        shutil.rmtree(self.files.output_dir(job_id), ignore_errors=True)

    def _cleanup(self, job_id: str) -> None:
        shutil.rmtree(self.files.job_tmp(job_id), ignore_errors=True)
        self._cancel_requested.discard(job_id)
        self._secrets.pop(job_id, None)


def terminal_event_cancelled() -> JobEvent:
    return error_event(ErrorCode.JOB_CANCELLED.value, "job cancelled")


__all__ = ["KEEPALIVE", "JobManager", "JobObserver", "JobQueue", "terminal_event"]
