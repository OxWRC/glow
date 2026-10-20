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
# Outcome of every run is recorded in $STATUS_DIR/backup-status.json (atomic
# write). Pruning is recorded separately (last_prune_*) so a prune failure never
# hides a good backup.
set -eu
umask 077

STATE=${GLOW_STATE_MNT:-/state}
STATUS_DIR=$STATE/backup-status
RELEASE_ENV=${GLOW_RELEASE_ENV:-/release/glow.env}
STATUS=$STATUS_DIR/backup-status.json
DUMPS=/tmp/glow-dumps
VERIFY=/tmp/glow-verify
export RESTIC_REPOSITORY=${RESTIC_REPOSITORY:-/backup}
# Encrypted repo metadata only, so it may live on disk (a volume), not tmpfs.
export RESTIC_CACHE_DIR=${RESTIC_CACHE_DIR:-/cache}

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
cleanup() { rm -rf "$DUMPS" "$VERIFY"; }
trap cleanup EXIT
trap 'trap "" TERM INT; cleanup; kill 0 2>/dev/null; exit 143' TERM INT

# write_status JQ_ARGS... : merge named fields into the status file, keeping the
# rest (a corrupt file starts over), via temp file + rename in the same dir.
write_status() {
	cur='{}'
	if [ -s "$STATUS" ] && jq -e 'type == "object"' "$STATUS" >/dev/null 2>&1; then
		cur=$(cat "$STATUS")
	fi
	tmp=$(mktemp "$STATUS_DIR/.backup-status.XXXXXX") || return 1
	echo "$cur" | jq -S '{last_backup_at:"",last_backup_ok:false,last_backup_error:"",
		last_verify_at:"",last_verify_ok:false,last_verify_error:"",
		last_prune_at:"",last_prune_ok:false,last_prune_error:""} + .
		| . + ($ARGS.named | with_entries(.value |= (if . == "true" then true elif . == "false" then false else . end)))' \
		"$@" >"$tmp" || { rm -f "$tmp"; return 1; }
	chmod 0644 "$tmp" && mv "$tmp" "$STATUS" || { rm -f "$tmp"; return 1; }
}

record() { # record backup|verify|prune ok|fail [error]
	kind=$1 ok=$2 err=${3:-}
	[ "$ok" = ok ] && flag=true || flag=false
	write_status --arg "last_${kind}_at" "$(now)" --arg "last_${kind}_ok" "$flag" \
		--arg "last_${kind}_error" "$err"
}

do_backup() {
	: "${RESTIC_PASSWORD:?GLOW_BACKUP_KEY is not set}"
	cleanup; mkdir -p "$DUMPS"
	restic cat config >/dev/null 2>&1 || restic init
	restic unlock # removes stale locks only
	PGPASSWORD=$POSTGRES_PASSWORD pg_dump -Fc -h "${POSTGRES_HOST:-api-db}" \
		-U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$DUMPS/glow.dump"
	PGPASSWORD=$ODK_POSTGRES_PASSWORD pg_dump -Fc -h "${ODK_POSTGRES_HOST:-postgres14}" \
		-U "$ODK_POSTGRES_USER" -d "$ODK_POSTGRES_DB" -f "$DUMPS/odk.dump"
	restic backup --tag glow --host glow --exclude '.tmp-*' \
		"$DUMPS" "$STATE/odk-secrets" "$STATE/secrets.env" "$RELEASE_ENV" \
		"$STATE/api-audit-logs" "$STATE/vintages" \
		"$STATE/odk-forms-state.json" "$STATE/deployment.json"
}

do_prune() {
	restic forget --tag glow --host glow --prune \
		--keep-hourly 48 --keep-daily 30 --keep-monthly 12
}

do_verify() {
	: "${RESTIC_PASSWORD:?GLOW_BACKUP_KEY is not set}"
	restic check
	cleanup; mkdir -p "$VERIFY"
	restic restore latest --tag glow --target "$VERIFY" --include "$DUMPS"
	for d in glow odk; do
		f=$VERIFY$DUMPS/$d.dump
		[ -s "$f" ] || { echo "restored $d.dump missing or empty"; return 1; }
		pg_restore --list "$f" >/dev/null
	done
}

# run_step KIND FN: run FN under the lock, record the outcome, return its status.
# The lock lives only in this subshell, so it is released when the step ends.
run_step() {
	kind=$1 fn=$2
	(
		exec 9>/tmp/glow-backup.lock
		flock 9
		log=$(mktemp)
		# A fresh process: set -e is ignored in any subshell that inherits an
		# if/&&/|| context, so FN must not run in one.
		set +e
		sh "$0" _step "$fn" >"$log" 2>&1
		rc=$?
		set -e
		cleanup
		if [ "$rc" -eq 0 ]; then
			# run_step runs in an || context (set -e off): check explicitly.
			if record "$kind" ok; then
				echo "$(now) $kind ok"
			else
				echo "$(now) $kind succeeded but the status file could not be written" >&2
				rc=1
			fi
		else
			err=$(tail -n 5 "$log" | tr '\n' ' ' | cut -c1-500)
			cat "$log" >&2
			record "$kind" fail "${err:-$kind failed (exit $rc)}" ||
				echo "$(now) could not record the $kind failure in the status file" >&2
			echo "$(now) $kind FAILED" >&2
		fi
		rm -f "$log"
		exit "$rc"
	)
}

# backup_cycle: backup, then (only if it succeeded) prune, reported separately.
backup_cycle() {
	run_step backup do_backup || return 1
	run_step prune do_prune || true
}

epoch() { date -u -D %Y-%m-%dT%H:%M:%SZ -d "$1" +%s 2>/dev/null; }

verify_due() {
	last=$(jq -r '.last_verify_at // ""' "$STATUS" 2>/dev/null || true)
	[ -n "$last" ] || return 0
	if ! e=$(epoch "$last") || [ -z "$e" ]; then
		echo "$(now) unparseable last_verify_at '$last'; skipping verify" >&2
		return 1
	fi
	[ $(( $(date -u +%s) - e )) -ge 604800 ]
}

nap() { sleep "$1" & wait $!; }

run_loop() {
	nap "${GLOW_BACKUP_FIRST_DELAY:-300}"
	while :; do
		backup_cycle || true
		if verify_due; then run_step verify do_verify || true; fi
		nap "${GLOW_BACKUP_INTERVAL:-3600}"
	done
}

case "${1:-}" in
	run-loop) run_loop ;;
	backup) backup_cycle ;;
	_step) "$2" ;;
	verify) run_step verify do_verify ;;
	*) echo "usage: $0 run-loop|backup|verify" >&2; exit 2 ;;
esac
