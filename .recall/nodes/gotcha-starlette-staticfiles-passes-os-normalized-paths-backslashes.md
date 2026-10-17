---
id: gotcha-starlette-staticfiles-passes-os-normalized-paths-backslashes
type: gotcha
scope: project
title: Starlette StaticFiles passes OS-normalized paths (backslashes on Windows) to get_response; normalize before prefix checks
triggers: ["staticfiles","spa","windows","ruta","path","fallback"]
anchors: [{"path":"backend/src/a0td/api/app.py","symbol":"SPAStaticFiles"}]
asserted: 2026-10-16
invalidated: null
superseded_by: null
confidence: medium
pin: false
source: audit
---
StaticFiles.get_path returns os.path.normpath(...), so on Windows "api/v1/x" arrives as "api\v1\x". The SPA fallback's startswith("api/") guard then fails and unknown API routes return index.html with 200. Always replace backslashes before comparing; tests run on the Windows dev host.
