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
export GLOW_BACKUP_FIRST_DELAY=1 GLOW_BACKUP_INTERVAL=3600
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

mkdir -p "$GLOW_STATE_DIR"/{backup-status,odk-secrets,api-audit-logs,vintages/20260101T000000Z,vintages/.tmp-x,glow-postgres,odk-postgres} "$GLOW_BACKUP_PATH"
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
STATUS_FILE=$GLOW_STATE_DIR/backup-status/backup-status.json
status() { cat "$STATUS_FILE"; }
# The loop's first iteration (delay 1s) does a backup and a verify.
for _ in $(seq 1 60); do
  [ -s "$STATUS_FILE" ] && jq -e '.last_verify_ok == true' "$STATUS_FILE" >/dev/null 2>&1 && break
  sleep 1
done
status | jq -e '.last_backup_ok == true and .last_verify_ok == true' >/dev/null || fail "loop did not complete: $(status 2>&1)"
# C2: the loop is now asleep; a manual run must not wait for it.
start=$(date +%s)
timeout 120 docker compose -f compose.yml exec -T backup glow-backup.sh backup || fail "manual backup failed or blocked"
[ $(( $(date +%s) - start )) -lt 60 ] || fail "manual backup waited on the loop's lock"

snaps=$(dc exec -T backup restic snapshots --json)
n=$(echo "$snaps" | jq 'length'); [ "$n" = 2 ] || fail "want 2 snapshots (loop + manual), got $n"
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
echo "$(ls -A "$GLOW_STATE_DIR/backup-status" | grep -c 'backup-status.json.')" | grep -qx 0 || fail "temp status file left behind"

# Failure path: repository mounted read-only.
RO=$T/ro; mkdir -p "$RO"
if GLOW_TEST_RO_DIR=$RO dc -f backup/test/ro.yml run --rm --no-deps -T backup glow-backup.sh backup; then fail "backup into read-only repo exited 0"; fi
status | jq -e '.last_backup_ok == false and (.last_backup_error | length > 0) and (.last_backup_at | test("Z$"))' >/dev/null || fail "bad failure status: $(status)"
# C1: a failing dump must not be recorded as success.
if dc run --rm --no-deps -T -e ODK_POSTGRES_PASSWORD=wrong backup glow-backup.sh backup; then fail "backup with wrong ODK password exited 0"; fi
status | jq -e '.last_backup_ok == false and (.last_backup_error | length > 0)' >/dev/null || fail "dump failure recorded as success: $(status)"
# I2: state is mounted read-only apart from the status dir.
dc exec -T backup sh -c 'touch /state/x 2>/dev/null' && fail "/state is writable"
# I1: dumps live on tmpfs and are gone after a run.
dc exec -T backup sh -c 'test ! -e /tmp/glow-dumps && grep -q " /tmp tmpfs" /proc/mounts' || fail "/tmp not tmpfs or dumps left behind"
# I4: prune is reported separately and a backup records it.
status | jq -e 'has("last_prune_ok")' >/dev/null || fail "no prune status"
# Status write failure must fail the run loudly, not log ok.
if GLOW_TEST_RO_DIR=$GLOW_STATE_DIR/backup-status dc -f backup/test/ro-status.yml run --rm --no-deps -T backup glow-backup.sh backup 2>"$T/err"; then
  fail "backup exited 0 though the status file was unwritable"
fi
grep -q "status file" "$T/err" || fail "status write failure not logged: $(cat "$T/err")"
# Bounded tmpfs, restic cache off tmpfs.
dc exec -T backup sh -c 'grep " /tmp tmpfs" /proc/mounts | grep -q size=2097152k && ! echo "$RESTIC_CACHE_DIR" | grep -q ^/tmp' || fail "/tmp tmpfs unbounded or cache on tmpfs"
echo "backup/test: OK"
