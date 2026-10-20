#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export GLOW_SECRET_KEY="${GLOW_SECRET_KEY:-smoke-test-secret}"

COMPOSE=(docker compose -f compose.yml -f compose.test.yml)

# A failure anywhere below (compose or the Python smoke checks) is useless
# without knowing which container actually broke -- an HTTPError from the
# api container, say, is often caused by odk-service or nginx underneath
# it. Dump every service's logs on any non-zero exit so CI output names
# the culprit instead of just the client-side traceback.
dump_logs_on_failure() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo "::group::smoke_compose.sh failed (exit $status) -- container logs" >&2
    for service in $("${COMPOSE[@]}" config --services 2>/dev/null); do
      echo "----- logs: $service -----" >&2
      "${COMPOSE[@]}" logs --no-color --tail=200 "$service" >&2 || true
    done
    echo "::endgroup::" >&2
  fi
  exit "$status"
}
trap dump_logs_on_failure EXIT

# Use canonical compose.yml + test overrides
"${COMPOSE[@]}" down -v --remove-orphans
"${COMPOSE[@]}" build api dashboard glow-init
# glow-init creates the admin@example.org/alpha-user@example.org and syncs schools once api is
# healthy (compose.glow-init.yml); dashboard depends_on its successful
# completion, so --wait blocks here until seeding is done.
"${COMPOSE[@]}" up -d --wait

python3 - <<'PY'
import json
import sys
import urllib.error
import urllib.request

base = "http://127.0.0.1:8000"


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
