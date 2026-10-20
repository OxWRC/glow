#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export GLOW_SECRET_KEY="${GLOW_SECRET_KEY:-smoke-test-secret}"

# Never touch the developer's ./docker-mount-data: all state lives in a
# throwaway dir. Containers write root-owned files into it, so delete via a
# container.
STATE_DIR="$(mktemp -d)"
export GLOW_STATE_DIR="$STATE_DIR"
CERT_DIR="$STATE_DIR/certs"
mkdir -p "$CERT_DIR"
export GLOW_TLS_CERT_DIR="$CERT_DIR"
export GLOW_DOMAIN=glow.test
export GLOW_TLS_MODE=files

COMPOSE=(docker compose -f compose.yml -f compose.test.yml)

# Restricted build environments may need the proxy passed into builds.
BUILD_ARGS=()
for var in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; do
  if [ -n "${!var:-}" ]; then
    BUILD_ARGS+=(--build-arg "$var=${!var}")
  fi
done

# A failure anywhere below is useless without knowing which container
# actually broke -- an HTTPError from the api container, say, is often caused
# by odk-service or nginx underneath it. Dump every service's logs on any
# non-zero exit so CI output names the culprit instead of just the
# client-side traceback.
# GLOW_SMOKE_KEEP_UP=1 (CI) leaves a passing stack and its state dir in place
# for the browser tests that follow.
cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo "::group::smoke_compose.sh failed (exit $status) -- container logs" >&2
    for service in $("${COMPOSE[@]}" config --services 2>/dev/null); do
      echo "----- logs: $service -----" >&2
      "${COMPOSE[@]}" logs --no-color --tail=200 "$service" >&2 || true
    done
    echo "::endgroup::" >&2
  fi
  if [ "$status" -ne 0 ] || [ "${GLOW_SMOKE_KEEP_UP:-}" != "1" ]; then
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    docker run --rm -v "$STATE_DIR:/d" busybox:1.36 sh -c 'rm -rf /d/* /d/.[!.]*' >/dev/null 2>&1 || true
    rm -rf "$STATE_DIR"
  fi
  exit "$status"
}
trap cleanup EXIT

# Self-signed cert for the proxy's `files` TLS mode.
openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
  -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/fullchain.pem" \
  -subj "/CN=glow.test" \
  -addext "subjectAltName=DNS:glow.test,DNS:api.glow.test,DNS:odk.glow.test" 2>/dev/null

# Base file only: Caddy must be the only thing publishing host ports.
docker compose -f compose.yml config --format json | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
published = sorted(n for n, s in services.items() if s.get("ports"))
assert published == ["proxy"], f"services publishing ports: {published}"
'
if docker compose -f compose.yml port nginx 80 >/dev/null 2>&1; then
  echo "nginx publishes a host port in compose.yml" >&2
  exit 1
fi

"${COMPOSE[@]}" down -v --remove-orphans
"${COMPOSE[@]}" build "${BUILD_ARGS[@]}" api dashboard glow-init
# glow-init creates the admin@example.org/alpha-user@example.org and syncs schools once api is
# healthy (compose.glow-init.yml); dashboard depends_on its successful
# completion, so --wait blocks here until seeding is done.
"${COMPOSE[@]}" up -d --wait

# Through the proxy, by hostname (no DNS for glow.test: pin to localhost).
via_proxy() {
  local host="$1"; shift
  curl -sS --noproxy '*' --resolve "$host:443:127.0.0.1" \
    --cacert "$CERT_DIR/fullchain.pem" "$@"
}

body=$(mktemp)
status=$(via_proxy glow.test -o "$body" -w '%{http_code}' https://glow.test/en/login)
[ "$status" = 200 ] && grep -qi '<html' "$body" \
  || { echo "dashboard via proxy: HTTP $status" >&2; exit 1; }

status=$(via_proxy api.glow.test -o /dev/null -w '%{http_code}' https://api.glow.test/health)
[ "$status" = 200 ] || { echo "api /health via proxy: HTTP $status" >&2; exit 1; }

status=$(via_proxy odk.glow.test -o "$body" -w '%{http_code}' https://odk.glow.test/)
[ "$status" = 200 ] && grep -qi 'ODK Central' "$body" \
  || { echo "ODK via proxy: HTTP $status (421 = wrong Host)" >&2; head -c 500 "$body" >&2; exit 1; }
rm -f "$body"

# `files` mode must never try to obtain certificates from an ACME CA.
if "${COMPOSE[@]}" logs --no-color proxy 2>&1 | grep -i acme; then
  echo "proxy attempted ACME in files mode" >&2
  exit 1
fi

python3 - <<'PY'
import json
import os
import sys
import socket
import ssl
import urllib.error
import urllib.request

# The proxy serves *.glow.test with a self-signed cert; pin those names to
# localhost and trust that cert.
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **k: _getaddrinfo(
    "127.0.0.1" if str(host).endswith("glow.test") else host, *a, **k
)
_ctx = ssl.create_default_context(cafile=os.environ["GLOW_TLS_CERT_DIR"] + "/fullchain.pem")
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ctx), urllib.request.ProxyHandler({}))
)

base = "https://api.glow.test"


def request(req):
    try:
        with urllib.request.urlopen(req) as response:
            return response.read().decode()
    except urllib.error.HTTPError as e:
        # Bare HTTPError gives no clue which service actually failed
        # upstream of api, or why. Surface the response body (FastAPI's
        # error detail / traceback) so CI shows the real cause, not just
        # "HTTP Error 500: Internal Server Error".
        body = e.read().decode(errors="replace")
        print(f"request to {req.full_url} failed: HTTP {e.code}", file=sys.stderr)
        print(body, file=sys.stderr)
        raise


# Password-based /auth/login was removed with the move to OIDC (Task 2); this
# stack has no real identity provider, so use the demo-mode login instead
# (compose.test.yml sets GLOW_DEMO_MODE for exactly this).
login_req = urllib.request.Request(
    base + "/demo/login",
    data=json.dumps({"role": "admin"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
token = json.loads(request(login_req))["access_token"]

schools_req = urllib.request.Request(
    base + "/schools",
    headers={"Authorization": f"Bearer {token}"},
    method="GET",
)
print(request(schools_req))

# /query and /export now read a pinned vintage instead of live data (see
# "pin /query and /export to periodic data vintages") - without one, they
# 503 and the dashboard never renders a chart. Cut one now that seeding
# has populated the data.
vintage_req = urllib.request.Request(
    base + "/admin/vintages",
    data=b"{}",
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
    method="POST",
)
print(request(vintage_req))
PY
