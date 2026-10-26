#!/usr/bin/env bash
# Usage: stage_release.sh <stage-dir> <version> <registry> <digests-out.json>
# Stages the release archive layout in <stage-dir> (run from the repo root),
# renders compose.yml and compose.demo.yml pinned to digests, and prints nothing
# on success. <digests-out.json> must already map each image ref to a digest ref
# (see release.yml); extra files (e.g. glowctl) can be copied in afterwards.
set -euo pipefail
stage=$1 version=$2 registry=$3 digests=$4
here=$(cd "$(dirname "$0")" && pwd)
repo=$(pwd)
work=$(mktemp -d)

# Compose resolves `extends` while reading, and with --no-interpolate it will not
# expand the TLS-mode variable in the path, so give it a literal file to find.
# render_release.py puts the `extends` back.
mkdir -p "$work/proxy" "$work/odk-central"
cp compose.yml compose.demo.yml compose.glow-init.yml "$work/"
cp proxy/compose.acme.yml "$work/proxy/"'compose.${GLOW_TLS_MODE:-acme}.yml'
cp -r proxy/Caddyfile.* proxy/demo-mail.caddy "$work/proxy/"
cp -r odk-central/nginx odk-central/redis odk-central/local-glow.json "$work/odk-central/"

export GLOW_OIDC_ISSUER=x GLOW_OIDC_CLIENT_ID=x
render() { # <out> <compose -f args...>
  local out=$1; shift
  (cd "$work" && docker compose --profile '*' "$@" config --no-interpolate --format json > "$work/c.json")
  python3 "$here/render_release.py" --compose-json "$work/c.json" --digests "$digests" \
    --stage "$work" --set "GLOW_VERSION=$version" --set "GLOW_REGISTRY=$registry" --out "$out"
}

rm -rf "$stage"; mkdir -p "$stage"
cp -r proxy odk-forms "$stage/"
mkdir -p "$stage/odk-central"
cp -r odk-central/nginx odk-central/redis odk-central/local-glow.json "$stage/odk-central/"
rm -rf "$stage/proxy/certs"
cp glow.env.example DEPLOYMENT.md "$stage/"
echo "$version" > "$stage/VERSION"
render "$stage/compose.yml" -f compose.yml
render "$stage/compose.demo.yml" -f compose.yml -f compose.demo.yml
rm -rf "$work"
