#!/usr/bin/env bash
# Integration test for the backup service. Brings up both DBs and the backup
# container against temp dirs, then asserts snapshot contents, status file and
# failure reporting. Needs docker; builds glow-backup and the ODK postgres base.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

T=$(mktemp -d)
export COMPOSE_PROJECT_NAME=glowbackuptest
export GLOW_STATE_DIR=$T/state GLOW_BACKUP_PATH=$T/bk GLOW_ENV_FILE=$T/glow.env
export GLOW_REGISTRY=glowtest GLOW_VERSION=test
export GLOW_BACKUP_KEY=testkey-0123456789abcdef
export POSTGRES_PASSWORD=pgpass ODK_POSTGRES_PASSWORD=odkpass
BUILD_ARGS=(--build-arg "HTTP_PROXY=${HTTP_PROXY:-}" --build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}"
  --build-arg "http_proxy=${http_proxy:-}" --build-arg "https_proxy=${https_proxy:-}")
dc() { docker compose -f compose.yml "$@"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
cleanup() {
  dc down -v --remove-orphans >/dev/null 2>&1 || true
  docker run --rm -v "$T:/t" busybox:1.36 rm -rf /t/state /t/bk /t/ro >/dev/null 2>&1 || true
  rm -rf "$T"
}
trap cleanup EXIT

mkdir -p "$GLOW_STATE_DIR"/{odk-secrets,api-audit-logs,vintages/20260101T000000Z,vintages/.tmp-x,glow-postgres,odk-postgres} "$GLOW_BACKUP_PATH"
echo s > "$GLOW_STATE_DIR/odk-secrets/enketo-secret"
echo 'GLOW_BACKUP_KEY=x' > "$GLOW_STATE_DIR/secrets.env"
echo '{}' > "$GLOW_STATE_DIR/odk-forms-state.json"
echo '{}' > "$GLOW_STATE_DIR/deployment.json"
echo 'GLOW_DOMAIN=example.org' > "$GLOW_ENV_FILE"
echo keep > "$GLOW_STATE_DIR/vintages/20260101T000000Z/meta.json"
echo unsuppressed > "$GLOW_STATE_DIR/vintages/.tmp-x/data.parquet"
echo l > "$GLOW_STATE_DIR/api-audit-logs/audit.jsonl"

dc build "${BUILD_ARGS[@]}" backup postgres14 >/dev/null
dc up -d --wait api-db postgres14 >/dev/null
dc exec -T api-db psql -q -U glow -d glow -c "create table t(v text); insert into t values ('glow-row');"
dc exec -T postgres14 psql -q -U odk -d odk -c "create table t(v text); insert into t values ('odk-row');"
dc up -d --no-deps backup >/dev/null
bk() { dc exec -T backup glow-backup.sh "$@"; }
status() { cat "$GLOW_STATE_DIR/backup-status.json"; }

bk backup || fail "backup exited non-zero"
snaps=$(dc exec -T backup restic snapshots --json)
n=$(echo "$snaps" | jq 'length'); [ "$n" = 1 ] || fail "want 1 snapshot, got $n"
echo "$snaps" | jq -e '.[0].tags | index("glow")' >/dev/null || fail "snapshot not tagged glow"
files=$(dc exec -T backup restic ls latest)
for want in glow.dump odk.dump /state/secrets.env /state/odk-secrets/enketo-secret /release/glow.env \
  /state/api-audit-logs/audit.jsonl /state/vintages/20260101T000000Z/meta.json \
  /state/odk-forms-state.json /state/deployment.json; do
  echo "$files" | grep -q -- "$want" || fail "snapshot lacks $want"
done
echo "$files" | grep -q '\.tmp-' && fail ".tmp- vintage dir was backed up"
status | jq -e '.last_backup_ok == true and (.last_backup_at | test("Z$")) and .last_backup_error == ""' >/dev/null || fail "bad ok status: $(status)"
bk verify || fail "verify exited non-zero"
status | jq -e '.last_verify_ok == true and (.last_verify_at | test("Z$")) and .last_backup_ok == true' >/dev/null || fail "bad verify status: $(status)"
echo "$(ls "$GLOW_STATE_DIR" | grep -c 'backup-status.json.')" | grep -qx 0 || fail "temp status file left behind"

# Failure path: repository mounted read-only.
RO=$T/ro; mkdir -p "$RO"
if GLOW_TEST_RO_DIR=$RO dc -f backup/test/ro.yml run --rm --no-deps -T backup glow-backup.sh backup; then fail "backup into read-only repo exited 0"; fi
status | jq -e '.last_backup_ok == false and (.last_backup_error | length > 0) and (.last_backup_at | test("Z$"))' >/dev/null || fail "bad failure status: $(status)"
echo "backup/test: OK"
