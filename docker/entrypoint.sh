#!/bin/sh
# a0-to-domestic entrypoint (SPEC 12.1).
#   (no args)        -> apply migrations (if alembic.ini exists) and start Uvicorn on A0TD_PORT
#   a0td ...         -> run the CLI
#   <other command>  -> exec it as given
set -eu

if [ "$#" -eq 0 ]; then
  if [ -f /app/backend/alembic.ini ]; then
    (cd /app/backend && alembic upgrade head)
  fi
  # Forwarded headers (client IP, scheme) are trusted only from A0TD_TRUSTED_PROXIES.
  if [ -n "${A0TD_TRUSTED_PROXIES:-}" ]; then
    set -- --proxy-headers --forwarded-allow-ips "$A0TD_TRUSTED_PROXIES"
  else
    set -- --no-proxy-headers
  fi
  exec uvicorn a0td.api.app:create_app --factory \
    --host 0.0.0.0 --port "${A0TD_PORT:-8080}" --no-server-header "$@"
fi

exec "$@"
