#!/usr/bin/env bash
# Container smoke test (SPEC 15.7): slim/full/dev build, `a0td formats` per target, healthcheck,
# UID 10001, hardened run (--read-only, tmpfs /tmp, --cap-drop ALL) and /api/v1/info version.
#   REBUILD=0 reuses existing a0td:<target> images (default builds; Docker layer cache makes it cheap).
#   SMOKE_VERSION sets A0TD_VERSION at build time and is expected from /api/v1/info.
set -uo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

VERSION="${SMOKE_VERSION:-0.1.0}"
fails=0
failed_builds=" "
ok()   { echo "  ok   $*"; }
fail() { echo "  FAIL $*"; fails=$((fails + 1)); }
cleanup() { docker rm -f a0td-smoke-slim a0td-smoke-full >/dev/null 2>&1 || true; }
trap cleanup EXIT

for t in slim full dev; do
  echo "== build $t"
  if [ "${REBUILD:-1}" = "0" ] && docker image inspect "a0td:$t" >/dev/null 2>&1; then
    echo "  reuse a0td:$t"
  elif docker build -q -f docker/Dockerfile --target "$t" --build-arg "A0TD_VERSION=$VERSION" -t "a0td:$t" . >/dev/null; then
    ok "a0td:$t builds"
  else
    fail "a0td:$t build"
    failed_builds="$failed_builds$t "
  fi
done

echo "== a0td formats"
# Converter ids that must exist and be available per target (the office one needs LibreOffice).
COMMON_CONVERTERS="pdf raster svg postscript xps heif dxf"  # dxf: ezdxf is a Python dependency
# F3 converters that need external tools: available in full / dev only (cad: dwg = LibreDWG).
FULL_ONLY_CONVERTERS="office dwg hpgl djvu"
PARSE='import json, sys
for c in json.load(sys.stdin):
    print(c["id"], "available" if c["available"] else "unavailable")'
for t in slim full dev; do
  case "$failed_builds" in *" $t "*) echo "  skip $t (build failed)"; continue;; esac
  states=$(docker run --rm -e PARSE="$PARSE" "a0td:$t" sh -c 'a0td formats --json | python -c "$PARSE"' 2>&1)     || { fail "$t: a0td formats failed: $states"; continue; }
  for id in $COMMON_CONVERTERS; do
    echo "$states" | grep -qx "$id available" && ok "$t: converter $id available" || fail "$t: converter $id not available"
  done
  for id in $FULL_ONLY_CONVERTERS; do
    if [ "$t" = "slim" ]; then
      echo "$states" | grep -qx "$id unavailable" && ok "slim: $id converter unavailable" || fail "slim: $id converter must exist and be unavailable"
    else
      echo "$states" | grep -qx "$id available" && ok "$t: converter $id available" || fail "$t: converter $id not available"
    fi
  done
  # External tools the converters rely on.
  for tool in soffice inkscape dwg2dxf ddjvu rsvg-convert qpdf gs; do
    if docker run --rm "a0td:$t" sh -c "command -v $tool" >/dev/null 2>&1; then present=1; else present=0; fi
    if [ "$t" = "slim" ]; then
      case "$tool" in soffice|inkscape|dwg2dxf|ddjvu) [ $present = 0 ] && ok "slim: no $tool" || fail "slim has $tool";; *) [ $present = 1 ] && ok "slim: $tool present" || fail "slim: $tool missing";; esac
    else
      [ $present = 1 ] && ok "$t: $tool present" || fail "$t: $tool missing"
    fi
  done
  # Inkscape's HPGL import needs system python3 packages (lxml, numpy...): convert a plot for real.
  if [ "$t" != "slim" ]; then
    out=$(docker run --rm "a0td:$t" sh -c 'printf "IN;SP1;PU0,0;PD10160,0,10160,7620,0,7620,0,0;SP0;" > /tmp/plot.hpgl && a0td info /tmp/plot.hpgl --json' 2>&1)
    echo "$out" | grep -q '"format": "hpgl"' && ok "$t: HPGL plot converts (Inkscape extension works)" || fail "$t: HPGL conversion failed: ${out:0:200}"
  fi
done

echo "== runtime (read-only, tmpfs /tmp, cap-drop ALL)"
for t in slim full; do
  case "$failed_builds" in *" $t "*) echo "  skip $t (build failed)"; continue;; esac
  name="a0td-smoke-$t"
  docker rm -f "$name" >/dev/null 2>&1
  docker run -d --name "$name" --read-only --tmpfs /tmp:size=256m --cap-drop ALL \
    --security-opt no-new-privileges:true -v "$name-data:/data" "a0td:$t" >/dev/null \
    || { fail "$t: cannot start"; continue; }
  status=starting
  for _ in $(seq 1 60); do
    status=$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || echo gone)
    [ "$status" = healthy ] || [ "$status" = unhealthy ] || [ "$status" = gone ] && break
    sleep 2
  done
  [ "$status" = healthy ] && ok "$t: healthcheck passes" || { fail "$t: health=$status"; docker logs --tail 20 "$name"; }
  uid=$(docker exec "$name" id -u 2>/dev/null)
  [ "$uid" = 10001 ] && ok "$t: UID 10001" || fail "$t: uid=$uid"
  info=$(docker exec "$name" python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8080/api/v1/info').read().decode())" 2>&1)
  echo "$info" | grep -Eq "\"version\": ?\"$VERSION\"" && ok "$t: /api/v1/info version $VERSION" || fail "$t: /api/v1/info: ${info:0:200}"
  docker rm -f "$name" >/dev/null 2>&1; docker volume rm "$name-data" >/dev/null 2>&1
done

echo
[ "$fails" = 0 ] && echo "container smoke: PASS" || echo "container smoke: $fails FAILED"
exit $((fails > 0))
