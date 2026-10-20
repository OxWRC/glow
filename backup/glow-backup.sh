#!/bin/sh
# glow-backup.sh run-loop | backup | verify
#
# One restic snapshot (tag "glow") per run holding: pg_dump -Fc of the GLOW DB
# and the ODK DB, odk-secrets, secrets.env, glow.env, audit logs, vintages
# (minus in-flight .tmp-* dirs), odk-forms-state.json and deployment.json.
#
# Consistency: each pg_dump is transactionally consistent for its own DB. The
# two DBs are dumped one after the other, so they are taken seconds apart, not
# at the same instant; ODK submissions arriving in between show up in only one.
#
# Outcome of every run is recorded in $STATE/backup-status.json (atomic write).
set -eu

STATE=${GLOW_STATE_MNT:-/state}
RELEASE_ENV=${GLOW_RELEASE_ENV:-/release/glow.env}
STATUS=$STATE/backup-status.json
DUMPS=/tmp/glow-dumps
export RESTIC_REPOSITORY=${RESTIC_REPOSITORY:-/backup}
export RESTIC_CACHE_DIR=/tmp/restic-cache

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# write_status KEY VALUE... : merge string/bool fields into the status file,
# keeping the rest, via temp file + rename in the same directory.
write_status() {
	cur='{}'
	[ -s "$STATUS" ] && cur=$(cat "$STATUS")
	tmp=$(mktemp "$STATE/.backup-status.XXXXXX")
	echo "$cur" | jq -S '{last_backup_at:"",last_backup_ok:false,last_backup_error:"",
		last_verify_at:"",last_verify_ok:false,last_verify_error:""} + .
		| . + ($ARGS.named | with_entries(.value |= (if . == "true" then true elif . == "false" then false else . end)))' \
		"$@" >"$tmp" || { rm -f "$tmp"; return 1; }
	chmod 0644 "$tmp"
	mv "$tmp" "$STATUS"
}

# Named jq args for write_status are passed as --arg k v pairs.
record() { # record backup|verify ok|fail [error]
	kind=$1 ok=$2 err=${3:-}
	[ "$ok" = ok ] && flag=true || flag=false
	write_status --arg "last_${kind}_at" "$(now)" --arg "last_${kind}_ok" "$flag" \
		--arg "last_${kind}_error" "$err"
}

lock() { exec 9>/tmp/glow-backup.lock; flock 9; }

repo_init() {
	restic cat config >/dev/null 2>&1 || restic init
}

do_backup() {
	: "${RESTIC_PASSWORD:?GLOW_BACKUP_KEY is not set}"
	rm -rf "$DUMPS"; mkdir -p "$DUMPS"
	repo_init
	PGPASSWORD=$POSTGRES_PASSWORD pg_dump -Fc -h "${POSTGRES_HOST:-api-db}" \
		-U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$DUMPS/glow.dump"
	PGPASSWORD=$ODK_POSTGRES_PASSWORD pg_dump -Fc -h "${ODK_POSTGRES_HOST:-postgres14}" \
		-U "$ODK_POSTGRES_USER" -d "$ODK_POSTGRES_DB" -f "$DUMPS/odk.dump"
	restic backup --tag glow --host glow --exclude '.tmp-*' \
		"$DUMPS" "$STATE/odk-secrets" "$STATE/secrets.env" "$RELEASE_ENV" \
		"$STATE/api-audit-logs" "$STATE/vintages" \
		"$STATE/odk-forms-state.json" "$STATE/deployment.json"
	restic forget --tag glow --host glow --prune \
		--keep-hourly 48 --keep-daily 30 --keep-monthly 12
}

do_verify() {
	: "${RESTIC_PASSWORD:?GLOW_BACKUP_KEY is not set}"
	restic check
	out=/tmp/glow-verify
	rm -rf "$out"; mkdir -p "$out"
	restic restore latest --tag glow --target "$out" --include "$DUMPS"
	for d in glow odk; do
		f=$out$DUMPS/$d.dump
		[ -s "$f" ] || { echo "restored $d.dump missing or empty"; return 1; }
		pg_restore --list "$f" >/dev/null
	done
}

# run_step KIND FN: run FN, record the outcome, return its exit status.
run_step() {
	kind=$1 fn=$2
	lock
	log=$(mktemp)
	if ( set -e; "$fn" ) >"$log" 2>&1; then
		rm -rf "$DUMPS" /tmp/glow-verify
		record "$kind" ok
		rm -f "$log"
		echo "$(now) $kind ok"
	else
		rc=$?
		err=$(tail -n 5 "$log" | tr '\n' ' ' | cut -c1-500)
		cat "$log" >&2
		rm -rf "$DUMPS" /tmp/glow-verify
		record "$kind" fail "${err:-$kind failed (exit $rc)}" || true
		rm -f "$log"
		echo "$(now) $kind FAILED" >&2
		return 1
	fi
}

epoch() { date -u -D %Y-%m-%dT%H:%M:%SZ -d "$1" +%s 2>/dev/null || echo 0; }

verify_due() {
	last=$(jq -r '.last_verify_at // ""' "$STATUS" 2>/dev/null || true)
	[ -n "$last" ] || return 0
	[ $(( $(date -u +%s) - $(epoch "$last") )) -ge 604800 ]
}

run_loop() {
	sleep "${GLOW_BACKUP_FIRST_DELAY:-300}"
	while :; do
		run_step backup do_backup || true
		if verify_due; then run_step verify do_verify || true; fi
		sleep "${GLOW_BACKUP_INTERVAL:-3600}"
	done
}

case "${1:-}" in
	run-loop) run_loop ;;
	backup) run_step backup do_backup ;;
	verify) run_step verify do_verify ;;
	*) echo "usage: $0 run-loop|backup|verify" >&2; exit 2 ;;
esac
