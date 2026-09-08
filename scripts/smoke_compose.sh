#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export GLOW_SECRET_KEY="${GLOW_SECRET_KEY:-smoke-test-secret}"

# Use canonical compose.yml + test overrides
docker compose -f compose.yml -f compose.test.yml down -v --remove-orphans
docker compose -f compose.yml -f compose.test.yml build api dashboard
docker compose -f compose.yml -f compose.test.yml up -d --wait

# Healthcheck now guarantees API is ready, so seed users immediately.
# The DB is bind-mounted on the host and survives `down -v`, so schools/users
# may already exist from a previous run - tolerate that and upsert instead.
COMPOSE="docker compose -f compose.yml -f compose.test.yml exec -T api glow-api"

$COMPOSE schools create "Focus School Academy" || true
$COMPOSE schools create "Neighbouring School" || true

if ! $COMPOSE users create --admin --password admin --schools 'Focus School Academy,Neighbouring School' admin; then
  $COMPOSE users update --password admin --schools 'Focus School Academy,Neighbouring School' --active admin
fi

if ! $COMPOSE users create --password alpha-user --schools 'Focus School Academy' alpha-user; then
  $COMPOSE users update --password alpha-user --schools 'Focus School Academy' --active alpha-user
fi

# compose.yml's default GLOW_ODK_API_URL (http://odk-service:8383, internal
# plain HTTP) is rejected by ODK Central for Basic Auth ("This authentication
# method is only available over HTTPS", 401.3) - must go through nginx's TLS
# termination instead, same as dev's .env does for local dev.
export GLOW_ODK_API_URL="https://nginx"
export GLOW_ODK_VERIFY_SSL="false"
export GLOW_ODK_API_EMAIL="api@glow.local"
export GLOW_ODK_API_PASSWORD="devpassword"
export GLOW_ODK_PROJECT_ID=1
docker compose -f compose.yml -f compose.test.yml up -d --wait api dashboard

# api's /health is liveness-only and doesn't reflect DataStore's background
# initial ODK fetch (data.py DataStore.startup() loads async in a daemon
# thread; ODK Central's OData submissions endpoint can be slow to
# materialize for a freshly-created form/project). Poll /dimensions instead
# of trusting the healthcheck for data readiness.
echo "Waiting for /dimensions to reflect seeded ODK data..."
for i in $(seq 1 60); do
  VARS=$(curl -s http://127.0.0.1:8000/dimensions | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('variables', [])))" 2>/dev/null || echo 0)
  if [ "$VARS" -gt 0 ] 2>/dev/null; then
    echo "/dimensions ready ($VARS variables) after ${i}s"
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "Timed out waiting for /dimensions to report seeded variables" >&2
    echo "--- api logs ---" >&2
    docker compose -f compose.yml -f compose.test.yml logs api >&2
    exit 1
  fi
  sleep 1
done

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
