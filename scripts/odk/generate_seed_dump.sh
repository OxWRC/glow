#!/usr/bin/env bash
# generate_seed_dump.sh - Regenerate odk-central/postgres/seed/dev-seed.dump
#
# Boots an isolated scratch ODK Central stack (never touching a normal dev
# stack you might already have running), replays the same bootstrap +
# mock-data-seeding + timestamp-backdating pipeline dev-init.sh used to run
# on every fresh environment, then pg_dump's the result.
#
# Run this whenever the glow-dummies model, the ODK forms, or the
# timestamp backdating logic changes -- and whenever ODK_CENTRAL_TAG is
# bumped, since the dump is tied to that release's migration state.
#
# Prerequisite: data/glow_base.csv must exist -- this is the SLIMMED dataset
# (see scripts/odk/slim_mock_data.py), not glow-dummies' raw output directly.
# The unfiltered model produces ~9,263 students / 20 schools / ~60,918 ODK
# submissions once transformed -- at ODK's HTTP seeding rate (~2.9
# submissions/sec, throttled), that's ~5-6 hours per regeneration, discovered
# the hard way while building this task. slim_mock_data.py cuts that down to
# ~12k submissions while keeping every school's distinct test-scenario plan
# (transform_mock_data.py assigns each school a unique target_waves/phq_mode/
# v1-quirk combination -- dropping a school entirely would lose that
# combination, so this thins classes-per-school instead, never removes a
# school). Generate the raw CSV from a sibling checkout, then slim it:
#
#   cd ../glow-dummies
#   julia --project=. -e 'import Pkg; Pkg.instantiate()'
#   julia --project=. bin/glow_dummies --config examples/glow_model.toml --seed 42 \
#     > ../glow/data/glow_base_raw.csv
#   cd ../glow
#   python scripts/odk/slim_mock_data.py \
#     --input data/glow_base_raw.csv --output data/glow_base.csv
#
# Usage: scripts/odk/generate_seed_dump.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

export COMPOSE_PROJECT_NAME="glow-seed-gen"
COMPOSE="docker compose -f compose.yml -f scripts/odk/seed-gen.compose.yml"

ODK_ADMIN_EMAIL="admin@glow.local"
ODK_ADMIN_PASSWORD="devpassword"
ODK_API_EMAIL="api@glow.local"
ODK_API_PASSWORD="devpassword"

SEED_DIR="./data/mock_seed"
MANIFEST_PATH="${SEED_DIR}/manifest.csv"
OUTPUT_DUMP="odk-central/postgres/seed/dev-seed.dump"
MIN_DUMP_BYTES=1000000 # 1MB floor -- catches a truncated/empty/pointer-only dump

cleanup() {
  echo "Tearing down scratch ODK stack..."
  ${COMPOSE} down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ ! -f ./data/glow_base.csv ]]; then
  echo "❌ ./data/glow_base.csv not found. Generate it first:" >&2
  echo "" >&2
  echo "   cd ../glow-dummies" >&2
  echo "   julia --project=. -e 'import Pkg; Pkg.instantiate()'" >&2
  echo "   julia --project=. bin/glow_dummies --config examples/glow_model.toml --seed 42 \\" >&2
  echo "     > ../glow/data/glow_base_raw.csv" >&2
  echo "   cd ../glow" >&2
  echo "   python scripts/odk/slim_mock_data.py \\" >&2
  echo "     --input data/glow_base_raw.csv --output data/glow_base.csv" >&2
  exit 1
fi

echo "==> Tearing down any stale scratch stack from a previous failed run"
${COMPOSE} down -v --remove-orphans >/dev/null 2>&1 || true

echo "==> Transforming canonical mock data"
python ./scripts/odk/transform_mock_data.py \
  --input ./data/glow_base.csv \
  --output-dir "${SEED_DIR}" \
  --forms-dir ./odk-forms

echo "==> Starting scratch ODK stack (project: ${COMPOSE_PROJECT_NAME}, ports 18080/18443)"
${COMPOSE} up -d --build odk-service nginx pyxform

echo "==> Waiting for ODK Central to be ready"
max_wait=180
waited=0
while [[ $waited -lt $max_wait ]]; do
  if curl -sf -H "Host: odk.local" http://localhost:18080/ >/dev/null 2>&1 || \
     curl -s -H "Host: odk.local" http://localhost:18080/ 2>&1 | grep -q "30[0-9]\|421"; then
    break
  fi
  sleep 5
  waited=$((waited + 5))
done
if [[ $waited -ge $max_wait ]]; then
  echo "❌ ODK Central failed to start within ${max_wait}s" >&2
  ${COMPOSE} logs odk-service >&2
  exit 1
fi

echo "==> Creating ODK admin user: ${ODK_ADMIN_EMAIL}"
# Capture output instead of swallowing it (>/dev/null) -- a real auth
# failure downstream is much easier to diagnose with this visible than
# silently discarded. Not fatal if this reports "already exists" or similar;
# only a genuinely broken create should ever surface as a login failure
# later in this script.
CREATE_OUTPUT=$(echo "${ODK_ADMIN_PASSWORD}" | ${COMPOSE} exec -T odk-service \
  node /usr/odk/lib/bin/cli.js -u "${ODK_ADMIN_EMAIL}" user-create 2>&1) || true
echo "${CREATE_OUTPUT}"
${COMPOSE} exec -T odk-service \
  node /usr/odk/lib/bin/cli.js -u "${ODK_ADMIN_EMAIL}" user-promote 2>&1 || true

export ODK_API_BASE="https://localhost:18443/v1"
export ODK_HOST_HEADER="odk.local"
export ODK_CURL_INSECURE=1
source ./scripts/odk/odk-api-helper.sh

echo "==> Authenticating as admin"
ADMIN_TOKEN=$(odk_login "${ODK_ADMIN_EMAIL}" "${ODK_ADMIN_PASSWORD}")

echo "==> Creating project 'GLOW Development'"
PROJECT_ID=$(odk_create_project "GLOW Development" "${ADMIN_TOKEN}")

if [[ "${PROJECT_ID}" != "1" ]]; then
  echo "❌ Expected project id 1, got ${PROJECT_ID}. The baked seed hardcodes" >&2
  echo "   project id 1 (GLOW_ODK_PROJECT_ID) -- this shouldn't happen against" >&2
  echo "   a freshly torn-down scratch stack; check for leftover state." >&2
  exit 1
fi

echo "==> Creating API user: ${ODK_API_EMAIL}"
API_ACTOR_ID=$(odk_create_user "${ODK_API_EMAIL}" "${ODK_API_PASSWORD}" "${ADMIN_TOKEN}")

echo "==> Assigning manager role to API user"
odk_assign_role "${PROJECT_ID}" "${API_ACTOR_ID}" "1" "${ADMIN_TOKEN}"

echo "==> Seeding forms and submissions (this takes a while for ~12k rows)"
uv run ./scripts/odk/seed_odk_test_data.py \
  --seed-dir "${SEED_DIR}" \
  --manifest "${MANIFEST_PATH}" \
  --forms-dir ./odk-forms \
  --odk-url https://localhost:18443 \
  --email "${ODK_API_EMAIL}" \
  --password "${ODK_API_PASSWORD}" \
  --project-id "${PROJECT_ID}"

echo "==> Backdating submission timestamps"
python ./scripts/odk/rewrite_odk_submission_timestamps.py \
  --manifest "${MANIFEST_PATH}" \
  --db-service postgres14

echo "==> Dumping scratch database"
mkdir -p "$(dirname "${OUTPUT_DUMP}")"
${COMPOSE} exec -T postgres14 pg_dump -Fc -U odk -d odk > "${OUTPUT_DUMP}"

DUMP_SIZE=$(wc -c < "${OUTPUT_DUMP}")
if [[ "${DUMP_SIZE}" -lt "${MIN_DUMP_BYTES}" ]]; then
  echo "❌ ${OUTPUT_DUMP} is only ${DUMP_SIZE} bytes (expected at least ${MIN_DUMP_BYTES})." >&2
  echo "   Something went wrong upstream -- not leaving a truncated dump in place." >&2
  rm -f "${OUTPUT_DUMP}"
  exit 1
fi

echo "✅ Wrote ${OUTPUT_DUMP} (${DUMP_SIZE} bytes)"
echo "   Rebuild the dev image to pick it up: docker compose build postgres14"
