# Audit WP-19 (IPP, print job, hotfolder, roll planner, rotated portrait, metrics, CI)

Findings
1. backend/src/a0td/domain/planner.py _shrink_roll / _resolve_paper: oriented() sorts (w,h) for PORTRAIT, so a roll sheet shorter than the roll width is turned 90 degrees. Repro: roll 610 wide, page 1000x500 -> paper 510x610, 3 cols (expected 610x510, 2 cols); banner 20 mm high on 300 roll -> 40x300, 50 cols. Same for max_length < width. Existing test only asserts height >= 40, passes by accident.
2. planner.py _shrink_roll: overlap == shrunk content height (30 mm min) -> ZeroDivisionError in _axis (repro overlap 30, banner 20 mm high). step <= 0 otherwise silently falls back to unshrunk sheet.
3. SSRF: IppClient/normalize_uri and printers.py _check_ipp_uri accept any host/port; POST /printers/ipp-capabilities returns parsed response and distinct errors (HTTP status, connect failure). No loopback/link-local/metadata (169.254.169.254) policy. Only gated by A0TD_ENABLE_IPP.
4. /metrics (metrics.py install_metrics) is outside /api/v1 so AuthMiddleware does not guard it (api/auth.py API_PREFIX/DOCS_PATHS).
5. ipp.py _post: resp.read() unbounded; verify_tls default False; http(s) URI without port defaults to 631.
6. submit.py submit_pdf: read_bytes + encode_request concatenation = 3 copies of the PDF in memory; no streaming.
7. hotfolder.py _finish: if shutil.move fails, return leaves file in inbox -> reprocessed every poll (infinite loop, duplicate outputs).
8. hotfolder.py _import: path.open follows a symlink swapped in after the is_symlink check (TOCTOU), low risk.
9. submit.py _follow: IppError status param is job-state (7/8), not an IPP status.
10. CI: no issue found; mypy only covers domain+render.

Not audited: ingest/, frontend beyond sampling ipp.ts, Appendix C golden (not re-run).
