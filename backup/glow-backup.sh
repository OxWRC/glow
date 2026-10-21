#!/bin/sh
# glow-backup.sh run-loop | backup | verify | check-key | restore-files | restore-dbs | rollback-dbs
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
RESTIC_OUT=/tmp/glow-restic-backup.json
LOCK=/tmp/glow-backup.lock
export RESTIC_REPOSITORY=${RESTIC_REPOSITORY:-/backup}
# Encrypted repo metadata only, so it may live on disk (a volume), not tmpfs.
export RESTIC_CACHE_DIR=${RESTIC_CACHE_DIR:-/cache}

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
cleanup() {
	if [ -f "$VERIFY/pg/postmaster.pid" ]; then
		su-exec postgres pg_ctl -D "$VERIFY/pg" -m immediate -w stop >/dev/null 2>&1 || true
	fi
	# A previous step may have been SIGKILLed, leaving its postgres running.
	if pgrep -u postgres -f "$VERIFY" >/dev/null 2>&1; then
		pkill -INT -u postgres -f "$VERIFY" 2>/dev/null || true # fast shutdown
		i=0
		while [ "$i" -lt 20 ] && pgrep -u postgres -f "$VERIFY" >/dev/null 2>&1; do
			sleep 0.5; i=$((i + 1))
		done
		pkill -KILL -u postgres -f "$VERIFY" 2>/dev/null || true
	fi
	rm -rf "$DUMPS" "$VERIFY" "$RESTIC_OUT"
}
# The dump/verify dirs are shared by every process in the container (the loop
# and one-shot `docker exec`s): only a holder of $LOCK may clean them. Steps
# (_step) run with the lock held; everyone else takes it first.
locked_cleanup() { (exec 9>"$LOCK"; flock 9; cleanup); }
# Stop our own steps (releasing their lock), then clean what they left.
on_signal() {
	trap '' TERM INT
	kill 0 2>/dev/null
	locked_cleanup
	exit 143
}

# write_status JQ_ARGS... : merge named fields into the status file, keeping the
# rest, via temp file + rename in the same dir. A corrupt file starts over but
# keeps "has backed up" (see ever_backed_up), so it never unlocks repo init.
write_status() {
	cur='{}'
	if [ -s "$STATUS" ] && jq -e 'type == "object"' "$STATUS" >/dev/null 2>&1; then
		cur=$(cat "$STATUS")
	elif [ -e "$STATUS" ]; then
		cur='{"last_backup_success_at":"unknown (status file was unreadable)"}'
	fi
	tmp=$(mktemp "$STATUS_DIR/.backup-status.XXXXXX") || return 1
	echo "$cur" | jq -S '{last_backup_at:"",last_backup_ok:false,last_backup_error:"",
		last_verify_at:"",last_verify_ok:false,last_verify_error:"",
		last_prune_at:"",last_prune_ok:false,last_prune_error:"",
		last_backup_success_at:""} + .
		| . + ($ARGS.named | with_entries(.value |= (if . == "true" then true elif . == "false" then false else . end)))' \
		"$@" >"$tmp" || { rm -f "$tmp"; return 1; }
	chmod 0644 "$tmp" && mv "$tmp" "$STATUS" || { rm -f "$tmp"; return 1; }
}

record() { # record backup|verify|prune ok|fail [error]
	kind=$1 ok=$2 err=${3:-}
	[ "$ok" = ok ] && flag=true || flag=false
	t=$(now)
	if [ "$kind$ok" = backupok ]; then
		write_status --arg last_backup_at "$t" --arg last_backup_ok true --arg last_backup_error "" \
			--arg last_backup_success_at "$t"
	else
		write_status --arg "last_${kind}_at" "$t" --arg "last_${kind}_ok" "$flag" \
			--arg "last_${kind}_error" "$err"
	fi
}

count_rows() { # PASSWORD HOST USER DB TABLE -> row count, or -1 if unreadable
	PGPASSWORD=$1 psql -At -h "$2" -U "$3" -d "$4" -c "select count(*) from $5" 2>/dev/null || echo -1
}

# ever_backed_up: the status file records a successful backup at some point
# (last_backup_success_at; older status files only have last_backup_ok). A
# status file that exists but cannot be parsed proves nothing: answer yes, so
# only a missing file (a genuine first backup) lets ensure_repo init.
ever_backed_up() {
	[ -e "$STATUS" ] || return 1
	jq -e 'type == "object"' "$STATUS" >/dev/null 2>&1 || return 0
	jq -e '(.last_backup_success_at // "") != "" or .last_backup_ok == true' "$STATUS" >/dev/null 2>&1
}

# ensure_repo: open the repository, creating it only on the very first backup.
# Once a backup has succeeded, a missing repository means the backup disk is
# not mounted (an empty mountpoint): creating a new repo there would fork the
# history onto the root disk while status looked healthy, so fail instead.
ensure_repo() {
	rc=0
	restic cat config >/dev/null 2>&1 || rc=$?
	[ "$rc" -ne 0 ] || return 0
	if [ "$rc" -eq 10 ] && ! ever_backed_up; then
		restic init
		return
	fi
	if [ "$rc" -eq 10 ]; then
		echo "no restic repository at $RESTIC_REPOSITORY (backup disk unmounted?)"
	else
		echo "cannot open restic repository at $RESTIC_REPOSITORY (restic exit $rc)"
	fi
	return 1
}

do_backup() {
	: "${RESTIC_PASSWORD:?GLOW_BACKUP_KEY is not set}"
	cleanup; mkdir -p "$DUMPS"
	ensure_repo
	restic unlock # removes stale locks only
	PGPASSWORD=$POSTGRES_PASSWORD pg_dump -Fc -h "${POSTGRES_HOST:-api-db}" \
		-U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$DUMPS/glow.dump"
	PGPASSWORD=$ODK_POSTGRES_PASSWORD pg_dump -Fc -h "${ODK_POSTGRES_HOST:-postgres14}" \
		-U "$ODK_POSTGRES_USER" -d "$ODK_POSTGRES_DB" -f "$DUMPS/odk.dump"
	# Counted just after the dump (tiny race: rows arriving after the dump can only cause a false verify FAILURE, never hide a loss).
	# Source row counts (-1: table missing) so verify can tell "empty" from "lost".
	g_users=$(count_rows "$POSTGRES_PASSWORD" "${POSTGRES_HOST:-api-db}" "$POSTGRES_USER" "$POSTGRES_DB" users)
	g_schools=$(count_rows "$POSTGRES_PASSWORD" "${POSTGRES_HOST:-api-db}" "$POSTGRES_USER" "$POSTGRES_DB" schools)
	o_forms=$(count_rows "$ODK_POSTGRES_PASSWORD" "${ODK_POSTGRES_HOST:-postgres14}" "$ODK_POSTGRES_USER" "$ODK_POSTGRES_DB" forms)
	jq -n --argjson u "$g_users" --argjson s "$g_schools" --argjson f "$o_forms" \
		'{glow:{users:$u,schools:$s},odk:{forms:$f}}' >"$DUMPS/meta.json"
	restic backup --json --tag glow --host glow --exclude '.tmp-*' \
		"$DUMPS" "$STATE/odk-secrets" "$STATE/secrets.env" "$RELEASE_ENV" \
		"$STATE/api-audit-logs" "$STATE/vintages" \
		"$STATE/odk-forms-state.json" "$STATE/deployment.json" >"$RESTIC_OUT" ||
		{ cat "$RESTIC_OUT"; return 1; }
	# glowctl update reads this line: the exact pre-update snapshot.
	id=$(jq -Rr 'fromjson? | select(.message_type == "summary") | .snapshot_id // empty' "$RESTIC_OUT" | tail -n 1)
	[ -n "$id" ] || { echo "restic backup reported no snapshot id"; cat "$RESTIC_OUT"; return 1; }
	echo "snapshot_id=$id"
}

do_prune() {
	restic forget --tag glow --host glow --prune \
		--keep-hourly 48 --keep-daily 30 --keep-monthly 12
}

# psqlv DB SQL: query the throwaway cluster (unix socket only, trust auth).
psqlv() { su-exec postgres psql -At -h "$VERIFY/sock" -p "$VPORT" -d "$1" -c "$2"; }

do_verify() {
	: "${RESTIC_PASSWORD:?GLOW_BACKUP_KEY is not set}"
	restic check || { echo "verify FAILED: restic check"; return 1; }
	cleanup; mkdir -p "$VERIFY/sock"
	restic restore latest --tag glow --target "$VERIFY" --include "$DUMPS" ||
		{ echo "verify FAILED: restic restore"; return 1; }
	for d in glow odk; do
		[ -s "$VERIFY$DUMPS/$d.dump" ] || { echo "verify FAILED: restored $d.dump missing or empty"; return 1; }
	done
	meta=$VERIFY$DUMPS/meta.json
	jq -e . "$meta" >/dev/null 2>&1 || { echo "verify FAILED: meta.json missing or invalid"; return 1; }
	# Throwaway cluster: no TCP (listen_addresses=''), socket in the temp dir.
	VPORT=$(( $(od -An -N2 -tu2 /dev/urandom) % 20000 + 20000 ))
	chown -R postgres "$VERIFY"
	su-exec postgres initdb -D "$VERIFY/pg" -A trust --no-sync >/dev/null ||
		{ echo "verify FAILED: initdb"; return 1; }
	su-exec postgres pg_ctl -D "$VERIFY/pg" -w -l "$VERIFY/pg.log" \
		-o "-c listen_addresses='' -c unix_socket_directories=$VERIFY/sock -c port=$VPORT -c fsync=off" start >/dev/null ||
		{ echo "verify FAILED: start throwaway postgres"; return 1; }
	for d in glow odk; do
		psqlv postgres "create database $d" >/dev/null
		su-exec postgres pg_restore --exit-on-error --no-owner --no-privileges \
			-h "$VERIFY/sock" -p "$VPORT" -d "$d" "$VERIFY$DUMPS/$d.dump" ||
			{ echo "verify FAILED: pg_restore of $d.dump"; return 1; }
	done
	for t in glow:users glow:schools odk:forms; do
		db=${t%%:*} tbl=${t##*:}
		n=$(psqlv "$db" "select count(*) from $tbl" 2>/dev/null) ||
			{ echo "verify FAILED: table $db.$tbl missing after restore"; return 1; }
		want=$(jq -r ".$db.$tbl" "$meta")
		if [ "$want" -gt 0 ] && [ "$n" -eq 0 ]; then
			echo "verify FAILED: $db.$tbl empty after restore (source had $want rows)"; return 1
		fi
	done
}

# run_step KIND FN: run FN under the lock, record the outcome, return its status.
# The lock lives only in this subshell, so it is released when the step ends.
run_step() {
	kind=$1 fn=$2
	(
		exec 9>"$LOCK"
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
				grep '^snapshot_id=' "$log" || true
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
		echo "$(now) unparseable last_verify_at '$last'; running the restore test now" >&2
		return 0
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

# --- Restore (glowctl restore). The key arrives on stdin, never in argv/env.
read_key() {
	RESTIC_PASSWORD=
	IFS= read -r RESTIC_PASSWORD || true
	[ -n "$RESTIC_PASSWORD" ] || { echo "no backup key on stdin" >&2; exit 2; }
	export RESTIC_PASSWORD
}

# check-key SNAPSHOT|--key-only: open the repo with the key (nothing is
# written to it), then print SNAPSHOT (an ID or "latest") as
# {"id","time","version"} on the last line; version is from the snapshot's
# deployment.json ("" if absent).
do_check_key() {
	rc=0
	restic --no-lock --no-cache cat config >/dev/null || rc=$?
	case $rc in
		0) ;;
		12) echo "wrong backup key for $RESTIC_REPOSITORY" >&2; exit 1 ;;
		10) echo "no restic repository at $RESTIC_REPOSITORY" >&2; exit 1 ;;
		*) echo "cannot open $RESTIC_REPOSITORY (restic exit $rc)" >&2; exit 1 ;;
	esac
	[ "$1" != --key-only ] || return 0
	snap=$(restic --no-lock --no-cache snapshots --json --tag glow "$1" |
		jq -ce --arg s "$1" 'if length == 1 then .[0] | {id, time} else error("no GLOW snapshot " + $s) end')
	ver=$(restic --no-lock --no-cache dump "$(echo "$snap" | jq -r .id)" "$STATE/deployment.json" 2>/dev/null |
		jq -r '.version // ""' 2>/dev/null) || ver=
	echo "$snap" | jq -c --arg v "$ver" '. + {version: $v}'
}

# restore-files SNAPSHOT TARGET: the whole snapshot (dumps, state files,
# glow.env) under TARGET, at their backup paths.
do_restore_files() {
	restic --no-lock --no-cache restore "$1" --target "$2"
}

# restore_db PASSWORD HOST USER DB DUMP: drop, recreate and pg_restore DB.
restore_db() {
	export PGPASSWORD="$1"
	psql -v ON_ERROR_STOP=1 -q -h "$2" -U "$3" -d postgres \
		-c "drop database if exists \"$4\" with (force)" -c "create database \"$4\""
	pg_restore --exit-on-error -h "$2" -U "$3" -d "$4" "$5"
	echo "restored $4 on $2"
}

# restore-dbs DIR: both DBs from DIR/glow.dump and DIR/odk.dump (pg_restore 16
# for both: the dumps were made with pg_dump 16).
do_restore_dbs() {
	restore_db "$POSTGRES_PASSWORD" "${POSTGRES_HOST:-api-db}" "$POSTGRES_USER" "$POSTGRES_DB" "$1/glow.dump"
	restore_db "$ODK_POSTGRES_PASSWORD" "${ODK_POSTGRES_HOST:-postgres14}" "$ODK_POSTGRES_USER" "$ODK_POSTGRES_DB" "$1/odk.dump"
}

# rollback-dbs SNAPSHOT glow|odk...: restore only the named DBs from
# SNAPSHOT's dumps (glowctl update rollback; key from the environment). The
# dumps stay in this container's tmpfs and are removed afterwards.
ROLLBACK=/tmp/glow-rollback
do_rollback_dbs() {
	: "${RESTIC_PASSWORD:?GLOW_BACKUP_KEY is not set}"
	snap=$1; shift
	for d in "$@"; do
		case $d in glow | odk) ;; *) echo "unknown database $d (want glow or odk)" >&2; exit 2 ;; esac
	done
	rm -rf "$ROLLBACK"
	restic restore --retry-lock 10m "$snap" --target "$ROLLBACK" --include "$DUMPS"
	for d in "$@"; do
		[ -s "$ROLLBACK$DUMPS/$d.dump" ] || { echo "snapshot $snap has no $d.dump" >&2; exit 1; }
	done
	for d in "$@"; do
		if [ "$d" = glow ]; then
			restore_db "$POSTGRES_PASSWORD" "${POSTGRES_HOST:-api-db}" "$POSTGRES_USER" "$POSTGRES_DB" "$ROLLBACK$DUMPS/glow.dump"
		else
			restore_db "$ODK_POSTGRES_PASSWORD" "${ODK_POSTGRES_HOST:-postgres14}" "$ODK_POSTGRES_USER" "$ODK_POSTGRES_DB" "$ROLLBACK$DUMPS/odk.dump"
		fi
	done
}

case "${1:-}" in
	run-loop) trap locked_cleanup EXIT; trap on_signal TERM INT; run_loop ;;
	backup) trap on_signal TERM INT; backup_cycle ;;
	_step) trap cleanup EXIT; trap 'cleanup; exit 143' TERM INT; "$2" ;; # $LOCK is held by run_step
	verify) trap on_signal TERM INT; run_step verify do_verify ;;
	check-key) [ $# -eq 2 ] || exit 2; read_key; do_check_key "$2" ;;
	restore-files) [ $# -eq 3 ] || exit 2; read_key; do_restore_files "$2" "$3" ;;
	restore-dbs) [ $# -eq 2 ] || exit 2; do_restore_dbs "$2" ;;
	rollback-dbs)
		[ $# -ge 3 ] || exit 2
		# Runs beside the backup loop (exec): leave its dump/verify dirs alone.
		trap 'rm -rf "$ROLLBACK"' EXIT
		trap 'rm -rf "$ROLLBACK"; exit 143' TERM INT
		shift; do_rollback_dbs "$@" ;;
	*) echo "usage: $0 run-loop|backup|verify|check-key SNAP|restore-files SNAP DIR|restore-dbs DIR|rollback-dbs SNAP glow|odk..." >&2; exit 2 ;;
esac
