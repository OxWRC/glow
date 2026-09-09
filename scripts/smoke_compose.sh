#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export GLOW_SECRET_KEY="${GLOW_SECRET_KEY:-smoke-test-secret}"

# Use canonical compose.yml + test overrides
docker compose -f compose.yml -f compose.test.yml down -v --remove-orphans
docker compose -f compose.yml -f compose.test.yml build api dashboard
docker compose -f compose.yml -f compose.test.yml up -d --wait

# The DB is bind-mounted on the host and survives `down -v`, so schools/users
# may already exist from a previous run - tolerate that and upsert instead.
COMPOSE="docker compose -f compose.yml -f compose.test.yml exec -T api glow-api"

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

# Link real School records to the ODK-seeded data (creates one School per
# distinct raw "school" value the submissions carry, keyed by odk_school_id).
$COMPOSE schools sync

# Focus School Academy / Neighbouring School are deliberately *not* linked to
# any ODK data (no --odk-school-id) - they exercise the "school onboarded in
# GLOW but not yet connected to ODK" path (empty query results, not an error).
$COMPOSE schools create "Focus School Academy" || true
$COMPOSE schools create "Neighbouring School" || true

# /me returns exactly a user's assigned schools (no implicit "admin sees
# everything" expansion), so admin needs the connected school assigned too
# for the dashboard's school picker to offer it.
CONNECTED_SCHOOL="${PLAYWRIGHT_SCOPED_SCHOOL:-Beahanberg High School}"
ADMIN_SCHOOLS="Focus School Academy,Neighbouring School,${CONNECTED_SCHOOL}"

if ! $COMPOSE users create --admin --password admin --schools "$ADMIN_SCHOOLS" admin; then
  $COMPOSE users update --password admin --schools "$ADMIN_SCHOOLS" --active admin
fi

# alpha-user is scoped to a real, ODK-connected school so its query smoke
# test exercises actual data, not just auth plumbing.
if ! $COMPOSE users create --password alpha-user --schools "$CONNECTED_SCHOOL" alpha-user; then
  $COMPOSE users update --password alpha-user --schools "$CONNECTED_SCHOOL" --active alpha-user
fi

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
