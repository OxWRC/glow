#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export GLOW_SECRET_KEY="${GLOW_SECRET_KEY:-smoke-test-secret}"

# Use canonical compose.yml + test overrides
docker compose -f compose.yml -f compose.test.yml down -v --remove-orphans
docker compose -f compose.yml -f compose.test.yml build api dashboard glow-init
# glow-init creates the admin/alpha-user and syncs schools once api is
# healthy (compose.glow-init.yml); dashboard depends_on its successful
# completion, so --wait blocks here until seeding is done.
docker compose -f compose.yml -f compose.test.yml up -d --wait

python3 - <<'PY'
import json
import urllib.parse
import urllib.request

base = "http://127.0.0.1:8000"
login_req = urllib.request.Request(
    base + "/auth/login",
    data=urllib.parse.urlencode({"username": "admin", "password": "admin"}).encode(),
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    method="POST",
)
with urllib.request.urlopen(login_req) as response:
    token = json.loads(response.read().decode())["access_token"]

schools_req = urllib.request.Request(
    base + "/schools",
    headers={"Authorization": f"Bearer {token}"},
    method="GET",
)
with urllib.request.urlopen(schools_req) as response:
    print(response.read().decode())
PY
