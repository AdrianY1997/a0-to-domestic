# Audit WP-08 (jobs, storage, api, tests/api, compose.dev.yml)
Not a git repo; read files directly. Skipped backend/src/a0td/render/.
Checked: subprocess lifecycle, SQLite writer, upload streaming/cap, path safety, SSE, headers, retention vs SPEC 9.5/10/11/14.
Memory store has no conventions for these subsystems; no recurring categories.

Findings
1. jobs/manager.py:583-613,559-565 _finish_* return silently when job row is gone (delete during run) -> no terminal event published; SSE subscribers hang on keepalives forever and hub._last leaks.
2. jobs/manager.py:447-452 _pump_stdout returns on oversized line; stdout no longer drained, worker blocks until timeout.
3. storage/retention.py:73 "everything" purge uses 60 s orphan grace; a slow upload (dir exists, row not yet) is wiped.
4. api/routers/system.py:75, documents.py:297-312, jobs.py:163-173: sync purge/rmtree/DB on event loop in async handlers (retention_loop uses to_thread).
5. api/routers/jobs.py:252: thumb cache written non-atomically; concurrent GET can serve partial file.
6. api/routers/documents.py:252-271: password retry check-then-act not atomic; two requests create two ingest jobs.
7. jobs/manager.py:206-207 / _recover: interrupted render keeps partial output dir; interrupted ingest keeps partial artifacts.
8. jobs/manager.py:352,354 and worker.py:188: raw exception text (paths) returned to clients as error detail.
9. api/security.py: 500s from ServerErrorMiddleware bypass headers (not covered by test).
