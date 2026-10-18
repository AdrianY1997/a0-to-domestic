#!/bin/sh
# Web local mode (SPEC 17bis): build the bundle, vendor Pyodide and run the headless smoke test.
# Everything runs in Docker; on Windows/Git Bash MSYS_NO_PATHCONV=1 is set below.
#
#   scripts/web-smoke.sh [--skip-vendor]
#
# 1. a0td:dev builds frontend/public/py/{a0td.zip,manifest.json} and downloads Pyodide + wheels
#    into frontend/public/py/pyodide/ (git-ignored; needs network the first time).
# 2. The Playwright image loads them in Chromium and drives a0td.web.bridge (scripts/web-smoke.mjs).
set -eu
export MSYS_NO_PATHCONV=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then ROOT="$(cygpath -m "$ROOT")"; fi
PW_IMAGE="${PW_IMAGE:-mcr.microsoft.com/playwright:v1.55.0-noble}"
DEV_IMAGE="${DEV_IMAGE:-a0td:dev}"

mkdir -p "$ROOT/frontend/public/py"
docker run --rm --memory 2g \
  -v "$ROOT/tools:/repo/tools:ro" -v "$ROOT/backend:/repo/backend:ro" \
  -v "$ROOT/frontend/public/py:/out" \
  --entrypoint /opt/venv/bin/python "$DEV_IMAGE" /repo/tools/build_web_bundle.py --out /out
if [ "${1:-}" != "--skip-vendor" ]; then
  docker run --rm --memory 2g -v "$ROOT/tools:/repo/tools:ro" -v "$ROOT/frontend/public/py:/out" \
    --entrypoint /opt/venv/bin/python "$DEV_IMAGE" /repo/tools/vendor_pyodide.py --out /out
fi

docker run --rm --ipc=host --memory "${PW_MEMORY:-4g}" \
  -v "$ROOT/scripts:/repo/scripts:ro" -v "$ROOT/frontend/public/py:/repo/py:ro" \
  -v "$ROOT/fixtures:/repo/fixtures:ro" \
  "$PW_IMAGE" sh -c "mkdir -p /tmp/pw && cd /tmp/pw && npm init -y >/dev/null \
    && npm i --no-audit --no-fund playwright@1.55.0 >/dev/null 2>&1 \
    && PW_DIR=/tmp/pw node /repo/scripts/web-smoke.mjs --site /repo/py --fixtures /repo/fixtures ${WEB_SMOKE_ARGS:-}"
