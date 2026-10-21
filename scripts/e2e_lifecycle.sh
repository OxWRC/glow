#!/usr/bin/env bash
# End-to-end lifecycle of a production GLOW deployment, driven by glowctl:
#   install -> proxy checks -> backup -> status -> wipe -> restore -> data
#   present -> update --bundle (same images) -> update to a release whose API
#   never gets healthy, which must roll back with the old version serving.
#
# Usage (repo root or anywhere): scripts/e2e_lifecycle.sh
# Needs docker (compose v2), go, uv, openssl, curl, jq, root or passwordless
# sudo (glowctl restore must run as root), and host ports 80/443 free.
# Uses the fixed compose project "glow": it refuses to run if one exists.
#
# Releases are staged locally with fake digests: every bundle's digests.json
# maps tag refs to digest refs that are never in the image store, so
# bundle.Load rewrites the compose files back to the (local) tag refs. The
# bundles' image parts are just busybox: the real images are built here.
#
# E2E_SKIP_BUILD=1 reuses glow-e2e/glow-*:v0.0.0 images already built.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

# Plain vX.Y.Z: glowctl update accepts no pre-release tags. The glow-e2e
# registry name keeps these images apart from real ones.
REG=glow-e2e V0=v0.0.0 V1=v0.0.1 V2=v0.0.2
DOMAIN=glow.test ADMIN=admin@example.org EXTRA_USER=e2e-user@example.org
SUDO=""
[ "$(id -u)" = 0 ] || SUDO="sudo -n"

CURRENT="setup"
step() { CURRENT="$*"; printf '\n[e2e] ===== %s =====\n' "$*"; }
die() { echo "[e2e] FAIL [$CURRENT]: $*" >&2; exit 1; }

# Cleanup and the bundles need busybox: have it before anything else.
docker image inspect busybox:1.36 >/dev/null 2>&1 || docker pull -q busybox:1.36 >/dev/null ||
  die "cannot get busybox:1.36"

if [ -n "$(docker ps -aq --filter label=com.docker.compose.project=glow)" ]; then
  die "a compose project named glow already exists on this host; refusing to touch it"
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/glow-e2e.XXXXXX")
# Anonymous volumes (postgres/redis VOLUMEs) outlive recreated containers:
# cleanup removes the dangling ones this run left behind.
VOLS_BEFORE=$(docker volume ls -q)
PARENT=$WORK/releases STATE=$WORK/state BACKUPS=$WORK/backups CERTS=$WORK/certs
STAGE=$WORK/stage BUNDLES=$WORK/bundles KEY=$WORK/backup.key
mkdir -p "$PARENT" "$STATE" "$BACKUPS" "$CERTS" "$STAGE" "$BUNDLES"
REL0=$PARENT/glow-$V0 REL1=$PARENT/glow-$V1

cleanup() {
  local status=$?
  set +e
  if [ "$status" -ne 0 ]; then
    echo "[e2e] FAILED in step: $CURRENT (exit $status) -- container logs follow" >&2
    for c in $(docker ps -aq --filter label=com.docker.compose.project=glow); do
      echo "----- $(docker inspect --format '{{.Name}} {{.Config.Image}} {{.State.Status}}' "$c") -----" >&2
      docker logs --tail 60 "$c" 2>&1 | sed 's/^/    /' >&2
    done
  fi
  echo "[e2e] cleaning up"
  local f=label=com.docker.compose.project=glow
  docker ps -aq --filter "$f" | xargs -r docker rm -f -v >/dev/null
  docker volume ls -q --filter "$f" | xargs -r docker volume rm >/dev/null
  docker network ls -q --filter "$f" | xargs -r docker network rm >/dev/null
  docker volume ls -q --filter dangling=true --filter label=com.docker.volume.anonymous |
    grep -vxF -e "$VOLS_BEFORE" -e '' | xargs -r docker volume rm >/dev/null
  docker rmi "$REG/glow-api:$V2" >/dev/null 2>&1
  # State and staging hold root-owned files (DB dirs, restored state).
  docker run --rm -v "$WORK:/d" busybox:1.36 sh -c 'rm -rf /d/* /d/.[!.]*' >/dev/null 2>&1
  rm -rf "$WORK"
  [ "$status" -eq 0 ] && echo "[e2e] PASS: full lifecycle"
  exit "$status"
}
trap cleanup EXIT

gc() { # <release dir> <glowctl args...>
  local rel=$1; shift
  $SUDO "$rel/glowctl" "$@" --dir "$rel"
}

# Each target must answer 200 through the proxy on loopback (SNI = host).
proxy_checks() {
  local h p
  for t in "$DOMAIN /en/login" "api.$DOMAIN /health" "odk.$DOMAIN /"; do
    read -r h p <<<"$t"
    curl -fsS -k --noproxy '*' --max-time 30 --resolve "$h:443:127.0.0.1" -o /dev/null "https://$h$p" ||
      die "https://$h$p did not answer 200 through the proxy"
    echo "[e2e] ok https://$h$p"
  done
}

users_list() { gc "$1" users list | grep -o '[[:alnum:]._+-]*@[[:alnum:].-]*' | sort -u; }

# ODK's own data (projects, forms, users) as one comparable line.
odk_counts() {
  docker exec "$(docker ps -q --filter label=com.docker.compose.project=glow --filter label=com.docker.compose.service=postgres14)" \
    sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select (select count(*) from projects), (select count(*) from forms), (select count(*) from users)"'
}

# Image VOLUMEs must all be covered by compose mounts: an anonymous volume
# on a glow container is left behind on every recreate.
no_anon_volumes() {
  local c
  for c in $(docker ps -aq --filter label=com.docker.compose.project=glow); do
    docker inspect --format '{{.Name}}{{range .Mounts}}{{if eq .Type "volume"}}{{if not (eq (len .Name) 64)}}{{else}} anonymous:{{.Destination}}{{end}}{{end}}{{end}}' "$c"
  done | grep anonymous: && die "containers with anonymous volumes (uncovered image VOLUMEs) above"
  echo "[e2e] ok no anonymous volumes"
}

deployed() { jq -r ".$1" "$STATE/deployment.json"; }

# ---------------------------------------------------------------- build
step "build images and glowctl"
if [ "${E2E_SKIP_BUILD:-}" != 1 ]; then
  BUILD_ARGS=()
  for v in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; do
    [ -n "${!v:-}" ] && BUILD_ARGS+=(--build-arg "$v=${!v}")
  done
  b() { docker build "${BUILD_ARGS[@]}" -t "$REG/$1:$V0" "${@:2}"; }
  b glow-api api
  b glow-dashboard dashboard
  b glow-odk-postgres --target base odk-central/postgres
  b glow-enketo odk-central/enketo
  b glow-backup backup
fi
for i in api dashboard odk-postgres enketo backup; do
  docker image inspect "$REG/glow-$i:$V0" >/dev/null || die "image $REG/glow-$i:$V0 missing"
done
# The broken release: same API image, but it never serves (health fails).
# Before idling it writes to the GLOW DB (a user the rollback's DB restore
# must remove) and then a file in the audit-log state dir (proof it ran).
MARKER=rollback-marker@example.org
printf 'FROM %s\nCMD ["sh", "-c", "glow-api users create %s && touch /var/log/glow-api/e2e-broken-ran && exec sleep infinity"]\n' \
  "$REG/glow-api:$V0" "$MARKER" | docker build -q -t "$REG/glow-api:$V2" - >/dev/null
(cd glowctl && CGO_ENABLED=0 go build -trimpath -ldflags "-X main.version=e2e" -o "$WORK/glowctl" .)

# ---------------------------------------------------------------- stage
step "stage releases $V0, $V1 and bundles"
fake() { printf '%s@sha256:%s' "${1%:*}" "$(printf %s "$1" | sha256sum | cut -c1-64)"; }

# digests.json for version $1: every image of $1 maps to the fake digest of
# its $V0 tag, under both tag refs (bundle.Load rewrites to the first tag
# sorted, the $V0 one, which exists locally). For $V2 the API keeps its own tag.
digests() { # <version> <out>
  local v=$1 out=$2 ref base
  echo '{}' >"$out"
  { GLOW_REGISTRY=$REG GLOW_VERSION=$v GLOW_OIDC_ISSUER=x GLOW_OIDC_CLIENT_ID=x \
      docker compose --profile '*' -f compose.yml config --images
    GLOW_REGISTRY=$REG GLOW_VERSION=$v GLOW_OIDC_ISSUER=x GLOW_OIDC_CLIENT_ID=x \
      docker compose --profile '*' -f compose.yml -f compose.demo.yml config --images
  } | sort -u | while read -r ref; do
    base=${ref/%:$v/:$V0}
    [ "$v" = "$V2" ] && [ "$ref" = "$REG/glow-api:$V2" ] && base=$ref
    jq --arg a "$ref" --arg b "$base" --arg d "$(fake "$base")" '.[$a] = $d | .[$b] = $d' "$out" >"$out.tmp"
    mv "$out.tmp" "$out"
  done
}

stage() { # <version> <dir>
  digests "$1" "$WORK/digests-$1.json"
  uv run -q --with pyyaml bash scripts/release/stage_release.sh "$2" "$1" "$REG" "$WORK/digests-$1.json"
  install -m 0755 "$WORK/glowctl" "$2/glowctl"
}

bundle() { # <version>: archive (staged under $STAGE) + busybox image part + digests
  local v=$1 d=$BUNDLES/$1
  mkdir -p "$d"
  stage "$v" "$STAGE/glow-$v"
  tar czf "$d/glow-$v.tar.gz" -C "$STAGE" "glow-$v"
  docker save busybox:1.36 | gzip >"$d/glow-images-$v.tar.gz.part-00"
  cp "$WORK/digests-$v.json" "$d/digests.json"
  (cd "$d" && sha256sum ./* | sed 's, \./, ,' >SHA256SUMS)
}

stage "$V0" "$REL0"
mkdir -p "$BUNDLES/$V0"
docker save busybox:1.36 | gzip >"$BUNDLES/$V0/glow-images-$V0.tar.gz.part-00"
cp "$WORK/digests-$V0.json" "$BUNDLES/$V0/digests.json"
(cd "$BUNDLES/$V0" && sha256sum ./* | sed 's, \./, ,' >SHA256SUMS)
bundle "$V1"
bundle "$V2"

openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
  -keyout "$CERTS/privkey.pem" -out "$CERTS/fullchain.pem" -subj "/CN=$DOMAIN" \
  -addext "subjectAltName=DNS:$DOMAIN,DNS:api.$DOMAIN,DNS:odk.$DOMAIN" 2>/dev/null
chmod 0644 "$CERTS"/*.pem
# Production mode. The OIDC issuer is never reachable: the API only fetches
# its discovery document on the first token check, and nothing here logs in.
cat >"$REL0/glow.env" <<EOF
GLOW_DOMAIN=$DOMAIN
GLOW_OIDC_ISSUER=https://issuer.invalid/realms/glow
GLOW_OIDC_CLIENT_ID=glow-dashboard
GLOW_TLS_MODE=files
GLOW_TLS_CERT_DIR=$CERTS
GLOW_DEMO_MODE=false
GLOW_STATE_DIR=$STATE
GLOW_BACKUP_PATH=$BACKUPS
GLOW_REGISTRY=$REG
EOF

# ---------------------------------------------------------------- 1 install
step "1 install $V0 (production, --bundle)"
gc "$REL0" doctor || true
$SUDO "$REL0/glowctl" install --dir "$REL0" --admin-email "$ADMIN" --bundle "$BUNDLES/$V0" | tee "$WORK/install.log"
sed -n 's/^\[glowctl\] backup key: //p' "$WORK/install.log" >"$KEY"
chmod 0600 "$KEY"
[ -s "$KEY" ] || die "install did not print the backup key"
[ "$(deployed version)" = "$V0" ] || die "deployment.json version is $(deployed version), want $V0"

# ---------------------------------------------------------------- 2 proxy
step "2 checks through the proxy"
proxy_checks
no_anon_volumes

# ---------------------------------------------------------------- 3 backup
step "3 add a user, then backup"
gc "$REL0" users add "$EXTRA_USER"
users_list "$REL0" >"$WORK/users.before"
cat "$WORK/users.before"
grep -qx "$ADMIN" "$WORK/users.before" || die "admin $ADMIN missing from users list"
grep -qx "$EXTRA_USER" "$WORK/users.before" || die "$EXTRA_USER missing from users list"
odk_counts >"$WORK/odk.before"
echo "[e2e] ODK projects|forms|users: $(cat "$WORK/odk.before")"
gc "$REL0" backup
[ -f "$BACKUPS/config" ] || die "no restic repository in $BACKUPS"

# ---------------------------------------------------------------- 4 status
step "4 status exits 0"
gc "$REL0" status || die "glowctl status failed after install + backup"

# ---------------------------------------------------------------- 5 wipe
step "5 stop the stack and wipe the state dir (fresh server)"
$SUDO docker compose --project-name glow --project-directory "$REL0" --env-file "$STATE/runtime.env" \
  -f "$REL0/compose.yml" down -v
docker run --rm -v "$STATE:/d" busybox:1.36 sh -c 'rm -rf /d/* /d/.[!.]*'
[ -z "$(ls -A "$STATE")" ] || die "state dir not empty after wipe"

# ---------------------------------------------------------------- 6 restore
step "6 restore from $BACKUPS with the key file"
gc "$REL0" restore --from "$BACKUPS" --key-file "$KEY"

# ---------------------------------------------------------------- 7 data
step "7 restored data present"
users_list "$REL0" >"$WORK/users.after"
diff "$WORK/users.before" "$WORK/users.after" || die "users differ after restore"
odk_counts | diff "$WORK/odk.before" - || die "ODK data differs after restore"
proxy_checks
gc "$REL0" status || die "glowctl status failed after restore"

# ---------------------------------------------------------------- 8 update
step "8 update $V0 -> $V1 (--bundle, same images)"
gc "$REL0" update "$V1" --bundle "$BUNDLES/$V1" --yes
[ "$(deployed version)" = "$V1" ] || die "deployment.json version is $(deployed version), want $V1"
[ "$(deployed release_dir)" = "$REL1" ] || die "deployment.json release_dir is $(deployed release_dir), want $REL1"
proxy_checks
users_list "$REL1" | diff "$WORK/users.before" - || die "users differ after update"

# ---------------------------------------------------------------- 9 rollback
step "9 update $V1 -> $V2 (API never healthy) must roll back"
set +e
gc "$REL1" update "$V2" --bundle "$BUNDLES/$V2" --yes 2>&1 | tee "$WORK/update2.log"
rc=${PIPESTATUS[0]}
set -e
[ "$rc" -ne 0 ] || die "update to the broken release succeeded"
grep -q "changed services: api" "$WORK/update2.log" || die "expected only the api service to change"
grep -q "rolled back to $V1" "$WORK/update2.log" || die "no successful rollback reported"
[ "$(deployed version)" = "$V1" ] || die "deployment.json version is $(deployed version), want $V1"
api_img=$(docker inspect --format '{{.Config.Image}}' "$(docker ps -q --filter label=com.docker.compose.project=glow --filter label=com.docker.compose.service=api)")
[ "$api_img" = "$REG/glow-api:$V0" ] || die "api runs $api_img after rollback, want $REG/glow-api:$V0"
proxy_checks
[ -f "$STATE/api-audit-logs/e2e-broken-ran" ] || die "the broken API never ran, so the DB restore is unproven"
users_list "$REL1" | diff "$WORK/users.before" - || die "users differ after rollback (marker $MARKER not removed?)"
odk_counts | diff "$WORK/odk.before" - || die "ODK data differs after rollback"
no_anon_volumes
gc "$REL1" status || die "glowctl status failed after rollback"
CURRENT="done"
