# ODK Postgres Seed Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ODK Central's live-HTTP bootstrap-and-seed dance (run on every fresh dev/demo/CI environment) with a Postgres image whose `dev` build target has the full seed dataset already materialized into the actual data files at build time, so a fresh environment boots pre-seeded in about a second instead of minutes.

**Architecture:** A new `odk-central/postgres/Dockerfile` with `base` (vanilla `postgres:14-alpine`, what production runs) and `dev` (base + seed data baked into `/var/lib/postgresql/pgdata` — a path outside the base image's declared `VOLUME` — via a build-time `initdb` + `pg_restore`, copied into a clean final stage so the raw dump never appears in the shipped image's layer history) targets. `compose.yml` builds `base` by default; `compose.override.yml` (dev) and `compose.test.yml` (CI) select `dev`. The seed data itself is a `pg_dump -Fc` of a real ODK Central instance produced once by a new `scripts/odk/generate_seed_dump.sh`, which relocates the existing bootstrap-and-seed pipeline (`odk-api-helper.sh`, `transform_mock_data.py`, `seed_odk_test_data.py`, `rewrite_odk_submission_timestamps.py`) to run against an isolated scratch stack instead of a developer's live environment. `dev-init.sh` and `scripts/smoke_compose.sh` shrink to drop the now-unnecessary bootstrap/seed calls.

**Tech Stack:** Docker multi-stage builds, Docker Compose (v5.1.2+, this repo already relies on `build.target` overrides), POSIX shell, Bash, Python (existing scripts, unmodified), Git LFS, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-08-odk-postgres-seed-image-design.md`

## Global Constraints

- `base` target stays byte-for-byte equivalent to today's `image: postgres:14-alpine` reference — same `PGDATA` (`/var/lib/postgresql/data`), same bind mount, same env-var-driven `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`. Never touch these for `base`.
- `dev` target's baked Postgres role/database is fixed at `odk`/`odk`/`odk` — matches `compose.yml`'s existing defaults, so no `.env` change is required for the common case.
- `dev` target's baked ODK Central accounts use the fixed password `devpassword` for both the admin (`admin@glow.local`) and API (`api@glow.local`) users. Dev/demo-only, documented, never used for `base`/production.
- The baked seed hardcodes ODK project id `1` and all four current forms: `bewell_questionnaire` (v1 and v2), `phq9_questionnaire`, `demographics_questionnaire`.
- The seed dump (`odk-central/postgres/seed/dev-seed.dump`) is tracked via Git LFS, not plain git.
- No task deletes the existing bootstrap pipeline scripts (`odk-api-helper.sh`, `transform_mock_data.py`, `seed_odk_test_data.py`, `rewrite_odk_submission_timestamps.py`) — they're relocated to run inside `generate_seed_dump.sh` instead of `dev-init.sh`/`smoke_compose.sh`.

---

## Task 1: `odk-central/postgres/Dockerfile` — base and dev build targets

**Files:**
- Create: `odk-central/postgres/Dockerfile`
- Create: `odk-central/postgres/bake-seed.sh`
- Create: `odk-central/postgres/README.md`
- Create: `odk-central/postgres/seed/.gitkeep` (placeholder — Task 2 fills `seed/dev-seed.dump`)
- Create: `.gitattributes` (repo root)
- Modify: `odk-central/README.md` (cross-reference the new subdirectory)

**Interfaces:**
- Produces: two Docker build targets, `base` and `dev`, at context `./odk-central/postgres`. Task 3 references these by name in `compose.yml`/`compose.override.yml`/`compose.test.yml`.
- Consumes: nothing from other tasks yet — this task is tested with a throwaway dummy dump, not the real one (Task 2 produces the real, committed dump).

This is the riskiest piece of Docker/Postgres plumbing in the plan (writing directly to Postgres's data files at build time, bypassing the official entrypoint's own setup logic). The exact commands below were verified by hand before being written into this plan: built, booted, confirmed sub-second boot-to-ready, confirmed baked data present, and confirmed password auth works over a Docker network from a separate container (not just localhost/trust).

- [ ] **Step 1: Create the seed directory placeholder and `.gitattributes`**

```bash
mkdir -p odk-central/postgres/seed
touch odk-central/postgres/seed/.gitkeep
```

Create `.gitattributes` at the repo root:

```gitattributes
odk-central/postgres/seed/*.dump filter=lfs diff=lfs merge=lfs -text
```

- [ ] **Step 2: Run `git lfs install` (repo-local) if not already configured**

```bash
git lfs install
git lfs env | head -5
```

Expected: no error; confirms `git-lfs` is available (it already is in this environment — verified during design).

- [ ] **Step 3: Write `odk-central/postgres/bake-seed.sh`**

```sh
#!/bin/sh
# Bakes odk-central/postgres/seed/dev-seed.dump into a real Postgres data
# directory at build time. Runs as root inside the `dev-builder` Docker
# stage; su's to `postgres` for anything Postgres itself refuses to run as
# root. Not meant to be run outside that build context.
set -eux

PGDATA="${PGDATA:?PGDATA must be set}"
PG_USER="odk"
PG_PASSWORD="odk"
PG_DB="odk"
DUMP_PATH="/tmp/dev-seed.dump"

mkdir -p "$PGDATA"
chown postgres:postgres "$PGDATA"
chmod 0700 "$PGDATA"

su postgres -c "initdb -D '$PGDATA' --username=postgres --auth=trust"

# initdb's default pg_hba.conf only covers loopback addresses (trust) --
# this is the only rule that will match a real Docker-network peer, so it's
# safe to append rather than replace. Postgres reads pg_hba.conf top-down;
# the loopback-only rules above it never apply to another container's IP.
echo "host all all all scram-sha-256" >> "$PGDATA/pg_hba.conf"
# initdb defaults to a commented-out (loopback-only) listen_addresses;
# the container's *runtime* start (not this build) needs to accept
# connections from other containers on the Docker network.
sed -i "s/^#listen_addresses.*/listen_addresses = '*'/" "$PGDATA/postgresql.conf"

# Socket-only during the build itself -- no TCP, no port conflicts, and
# password auth isn't needed yet since we connect locally as the OS user.
su postgres -c "pg_ctl -D '$PGDATA' -o \"-c listen_addresses=''\" -w start"

su postgres -c "psql -v ON_ERROR_STOP=1 -c \"CREATE ROLE ${PG_USER} WITH LOGIN SUPERUSER PASSWORD '${PG_PASSWORD}';\""
su postgres -c "psql -v ON_ERROR_STOP=1 -c \"CREATE DATABASE ${PG_DB} OWNER ${PG_USER};\""
su postgres -c "pg_restore -j4 --no-owner --role=${PG_USER} -U ${PG_USER} -d ${PG_DB} '$DUMP_PATH'"

# Must exit 0 (clean shutdown) -- an unclean shutdown leaves WAL recovery
# to replay on every future container boot, which defeats the point of
# baking this in.
su postgres -c "pg_ctl -D '$PGDATA' -m fast -w stop"

rm -f "$DUMP_PATH"
```

```bash
chmod +x odk-central/postgres/bake-seed.sh
```

- [ ] **Step 4: Write `odk-central/postgres/Dockerfile`**

```dockerfile
# ODK Central's own Postgres database.
#
# `base`: vanilla postgres:14-alpine, blank cluster. What production runs
#         (compose.yml's default `build.target`).
# `dev`:  `base` plus the GLOW mock dataset materialized into the actual
#         Postgres data files at build time -- container boot is just
#         starting postgres on an already-populated cluster, no
#         restore-on-first-start cost. Used by local dev
#         (compose.override.yml) and CI (compose.test.yml).
#         See README.md for how the seed dump is generated/regenerated.

FROM postgres:14-alpine AS base

FROM base AS dev-builder
# A path the base image does NOT declare as a VOLUME (unlike the default
# /var/lib/postgresql/data) -- writes made here during `docker build` land
# in a real image layer instead of a throwaway anonymous volume Docker
# would otherwise mount over a declared VOLUME path during build.
ENV PGDATA=/var/lib/postgresql/pgdata
COPY seed/dev-seed.dump /tmp/dev-seed.dump
COPY bake-seed.sh /bake-seed.sh
RUN chmod +x /bake-seed.sh && /bake-seed.sh && rm -f /bake-seed.sh

FROM base AS dev
ENV PGDATA=/var/lib/postgresql/pgdata
# Copying only the materialized data directory (not the dump, not the
# script) keeps the raw seed dump out of this stage's layer history
# entirely -- the final image is just the data files.
COPY --from=dev-builder /var/lib/postgresql/pgdata /var/lib/postgresql/pgdata
```

- [ ] **Step 5: Write `odk-central/postgres/README.md`**

```markdown
# ODK Postgres seed image

Two build targets (see `Dockerfile`):

- `base` -- vanilla `postgres:14-alpine`, blank cluster. What production
  runs, via `compose.yml`'s default `build.target: base`.
- `dev` -- `base` plus the ~12k-row GLOW mock dataset (project, forms,
  users, submissions with backdated timestamps) baked into the actual
  Postgres data files at *build* time. Used by local dev
  (`compose.override.yml`) and CI (`compose.test.yml`). Container boot is
  near-instant since there's no restore step at startup -- the cluster is
  already there.

## Fixed dev credentials

The `dev` target's data is baked with fixed, dev-only credentials -- never
used in `base`/production, which keeps taking `POSTGRES_USER`/
`POSTGRES_PASSWORD`/`POSTGRES_DB` from the environment as normal:

- Postgres role/database: `odk` / `odk` / `odk` (matches `compose.yml`'s
  existing defaults, so no `.env` change is needed for the common case --
  but a customized `ODK_POSTGRES_PASSWORD` has no effect against the `dev`
  target, since the role's password is frozen into the image at build
  time, not read from the environment at container start).
- ODK Central admin: `admin@glow.local` / `devpassword`
- ODK Central API user (used by the Glow API): `api@glow.local` /
  `devpassword`

## Regenerating the seed data

`seed/dev-seed.dump` is a `pg_dump -Fc` custom-format dump, tracked via Git
LFS. Regenerate it with `scripts/odk/generate_seed_dump.sh` whenever the
glow-dummies model, the ODK forms, or the timestamp backdating logic
changes -- and whenever `ODK_CENTRAL_TAG` is bumped, since the dump is tied
to that release's migration state.

## Known limitations

- Enketo's webform survey cache lives in a separate bind mount
  (`docker-mount-data/odk-enketo-redis-main`) that this dump doesn't cover
  -- live Enketo webform links from seeded forms may not resolve in a
  freshly built environment. Data reads (OData exports, the Glow API) are
  unaffected.
```

- [ ] **Step 6: Cross-reference the new directory from `odk-central/README.md`**

Add a new list item under the existing `- postgres14` bullet in the "Why These Local Files Exist" section (after the `redis/` bullet, matching that section's existing style):

```markdown
- `postgres/`
  - local Dockerfile for the `postgres14` service's `base`/`dev` build
    targets -- see `postgres/README.md` for the `dev` target's baked seed
    data and fixed credentials
```

Also add a line to the existing "Upgrade Notes" numbered list (after item 2, renumbering the rest):

```markdown
3. `odk-central/postgres/seed/dev-seed.dump` -- regenerate via
   `scripts/odk/generate_seed_dump.sh` if you bump `ODK_CENTRAL_TAG`; the
   dump is tied to the migration state it was generated against.
```

- [ ] **Step 7: Test the mechanism with a throwaway dummy dump**

This validates the Dockerfile/bake-seed.sh mechanism in isolation, before Task 2 produces the real seed data.

```bash
# Create a tiny scratch Postgres and dump it -- NOT the real seed, just
# enough to prove the bake mechanism works.
docker run --rm -e POSTGRES_PASSWORD=scratch -e POSTGRES_USER=odk -e POSTGRES_DB=odk \
  --name pgscratch -d postgres:14-alpine
for i in $(seq 1 20); do docker exec pgscratch pg_isready -U odk -d odk >/dev/null 2>&1 && break; sleep 1; done
docker exec pgscratch psql -U odk -d odk -c \
  "CREATE TABLE widgets (id serial primary key, name text); INSERT INTO widgets (name) VALUES ('a'),('b');"
docker exec pgscratch pg_dump -Fc -U odk -d odk > odk-central/postgres/seed/dev-seed.dump
docker rm -f pgscratch
```

```bash
docker build -t glow-odk-postgres-dev-test --target dev odk-central/postgres
```

Expected: build succeeds; the `RUN /bake-seed.sh` step's log shows `CREATE ROLE`, `CREATE DATABASE`, and a clean `pg_ctl ... stop` (`database system is shut down`), with no errors.

```bash
docker network create odk-pg-test-net
docker run --rm -d --name odk-pg-test --network odk-pg-test-net glow-odk-postgres-dev-test
for i in $(seq 1 20); do docker exec odk-pg-test pg_isready -U odk -d odk >/dev/null 2>&1 && break; sleep 0.3; done
docker exec odk-pg-test psql -U odk -d odk -c "SELECT * FROM widgets;"
docker run --rm --network odk-pg-test-net -e PGPASSWORD=odk postgres:14-alpine \
  psql -h odk-pg-test -U odk -d odk -c "SELECT count(*) FROM widgets;"
```

Expected: first `psql` shows the 2 seeded rows (`a`, `b`); second `psql` (a *separate* container connecting over the network, not localhost) shows `count = 2` — this proves the baked `pg_hba.conf`/password auth works for real container-to-container connections, not just local trust.

```bash
docker rm -f odk-pg-test
docker network rm odk-pg-test-net
docker rmi glow-odk-postgres-dev-test
rm -f odk-central/postgres/seed/dev-seed.dump
```

Remove the dummy dump — Task 2 produces the real, committed one. `git status` should show no changes under `odk-central/postgres/seed/` at this point (only `.gitkeep` present).

- [ ] **Step 8: Confirm `base` target is unaffected (regression check)**

```bash
docker build -t glow-odk-postgres-base-test --target base odk-central/postgres
docker run --rm -e POSTGRES_PASSWORD=odk -e POSTGRES_USER=odk -e POSTGRES_DB=odk \
  --name odk-pg-base-test -d glow-odk-postgres-base-test
for i in $(seq 1 20); do docker exec odk-pg-base-test pg_isready -U odk -d odk >/dev/null 2>&1 && break; sleep 1; done
docker exec odk-pg-base-test psql -U odk -d odk -c "SELECT current_database();"
docker rm -f odk-pg-base-test
docker rmi glow-odk-postgres-base-test
```

Expected: behaves exactly like plain `postgres:14-alpine` — starts empty, accepts the env-var-driven credentials, no baked data.

- [ ] **Step 9: Commit**

```bash
git add odk-central/postgres/Dockerfile odk-central/postgres/bake-seed.sh \
  odk-central/postgres/README.md odk-central/postgres/seed/.gitkeep \
  odk-central/README.md .gitattributes
git commit -m "feat: add base/dev build targets for ODK's Postgres image"
```

---

## Task 2: `scripts/odk/generate_seed_dump.sh` — produce and commit the real seed dump

**Files:**
- Create: `scripts/odk/generate_seed_dump.sh`
- Create: `scripts/odk/seed-gen.compose.yml`
- Create: `scripts/odk/slim_mock_data.py`
- Modify: `scripts/odk/odk-api-helper.sh:38-45` (`odk_curl` — add TLS-insecure support)
- Modify: `scripts/odk/odk-api-helper.sh:26` (`ODK_HOST_HEADER` default — fix a real pre-existing bug, see Step 1a)
- Create (via running the script): `odk-central/postgres/seed/dev-seed.dump` (committed via Git LFS)

**Interfaces:**
- Consumes: `odk-central/postgres/Dockerfile`'s `base` target (Task 1) — the scratch stack builds `base`, never `dev` (which doesn't have real data yet the first time this runs).
- Consumes: `odk_login`, `odk_create_project`, `odk_create_user`, `odk_assign_role` from `odk-api-helper.sh` (existing), plus the new `ODK_CURL_INSECURE` env var this task adds to `odk_curl`.
- Produces: `odk-central/postgres/seed/dev-seed.dump`, which Task 3's compose wiring and Task 1's `dev` target build depend on.

- [ ] **Step 1: Add TLS-insecure support to `odk_curl` in `scripts/odk/odk-api-helper.sh`**

This replaces the temp-directory `curl` PATH-shim hack that `dev-init.sh` and `scripts/smoke_compose.sh` each currently duplicate to talk to ODK Central's self-signed HTTPS cert from the host. `ODK_HOST_HEADER` already exists in this file; this adds the missing `-k` (skip TLS verify) support alongside it.

Replace the existing `odk_curl` function (lines 38-45):

```bash
odk_curl() {
  if [[ -n "${ODK_HOST_HEADER}" ]]; then
    curl -H "Host: ${ODK_HOST_HEADER}" "$@"
    return
  fi

  curl "$@"
}
```

with:

```bash
odk_curl() {
  local -a extra_args=()
  if [[ -n "${ODK_HOST_HEADER}" ]]; then
    extra_args+=(-H "Host: ${ODK_HOST_HEADER}")
  fi
  if [[ -n "${ODK_CURL_INSECURE:-}" ]]; then
    extra_args+=(-k)
  fi
  curl "${extra_args[@]}" "$@"
}
```

- [ ] **Step 1a: Fix `ODK_HOST_HEADER`'s default at `scripts/odk/odk-api-helper.sh:26`**

This is a real, independent bug discovered while building this task (not part of the original design): line 26 currently reads

```bash
ODK_HOST_HEADER="${ODK_DOMAIN:-}"
```

This unconditionally overwrites `ODK_HOST_HEADER` from a *different* variable (`ODK_DOMAIN`) at source time — it does not default from itself. A caller that does `export ODK_HOST_HEADER="odk.local"` *before* sourcing this file (exactly what `generate_seed_dump.sh` in Step 4 below does) gets it silently reset to empty, because `ODK_DOMAIN` is unset in that context. The practical effect: `odk_curl` sends no `Host:` header, nginx can't route the request, and `odk_login` fails with no useful error.

Fix:

```bash
ODK_HOST_HEADER="${ODK_HOST_HEADER:-${ODK_DOMAIN:-}}"
```

This defaults from itself first, falling back to `ODK_DOMAIN` only when `ODK_HOST_HEADER` itself is unset — preserving current behavior for every other caller (`scripts/smoke_compose.sh` sets only `ODK_DOMAIN`, never `ODK_HOST_HEADER`, so it still flows through the fallback unchanged; `dev-init.sh` doesn't use either variable, it uses its own separate curl-wrapper PATH shim).

- [ ] **Step 2: Test the `odk_curl` change in isolation**

```bash
bash -c '
  source scripts/odk/odk-api-helper.sh
  ODK_HOST_HEADER="example.com"
  ODK_CURL_INSECURE=1
  ODK_API_BASE="https://self-signed.badssl.com"
  odk_curl -sf -o /dev/null -w "%{http_code}\n" "${ODK_API_BASE}/"
'
```

Expected: an HTTP status code printed (not a TLS verification error) — confirms `-k` is being applied. (badssl.com's self-signed endpoint is a convenient public target for this one check; no network access is needed for the rest of this task.)

- [ ] **Step 3: Write `scripts/odk/seed-gen.compose.yml`**

Isolates the scratch stack from anything a developer might already have running: a Docker-managed named volume instead of the usual bind mount (so it can't collide with `docker-mount-data/odk-postgres`, and gets cleaned up by `down -v`), and alternate host ports for `nginx` so port `8080`/`8443` stays free for an active dev stack. Verified during design that Compose's list-merge for `ports:` *appends* rather than replaces by default — the `!override` tag is required to actually replace them.

Note: `compose.yml`'s `postgres14` has no `build:` block until Task 3 lands (right now it's still `image: postgres:14-alpine`), so this override cannot inherit a `build.context` from it yet — an explicit `context:` is required here even though it will duplicate what Task 3 later adds to `compose.yml` itself. Once Task 3 lands, this becomes a harmless no-op collision (same value asserted twice), not a conflict — discovered and confirmed during Task 2's implementation.

```yaml
# Isolated overrides for scripts/odk/generate_seed_dump.sh. Never used by
# normal dev/demo/CI compose runs.
services:
  postgres14:
    build:
      context: ./odk-central/postgres
      target: base
    volumes:
      - seed_gen_odk_postgres:/var/lib/postgresql/data

  nginx:
    ports: !override
      - "18080:80"
      - "18443:443"

volumes:
  seed_gen_odk_postgres:
```

- [ ] **Step 4: Write `scripts/odk/generate_seed_dump.sh`**

```bash
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
```

```bash
chmod +x scripts/odk/generate_seed_dump.sh
```

- [ ] **Step 4a: Write `scripts/odk/slim_mock_data.py`**

The unfiltered glow-dummies output is 9,263 students across 20 schools, producing ~60,918 ODK submissions once transformed — a ~5-6 hour regeneration at ODK's HTTP seeding rate, discovered while building this task. `transform_mock_data.py` assigns each school a *distinct* test-scenario plan (a unique `target_waves`/`phq_mode`/v1-quirk combination per school — verified via `data/mock_seed/summary.json`'s `plans` array), so dropping schools entirely would silently delete specific boundary-condition coverage. This script preserves all 20 schools but thins students-per-school by keeping only a deterministically-chosen subset of each school's classes (never a partial class — every kept student keeps all their wave-1/2/3 rows intact).

The per-school class counts and a fixed seed were chosen and hand-verified to land close to ~12k submissions, with deliberately uneven (not uniform) retention per school — some schools keep 1 class, others up to 6 — while confirming the two donor-only schools (`transform_mock_data.py`'s alphabetically-last two, which supply "joiner" wave-4/5 data for other schools — see `transform_mock_data.py:375-393`) retain far more than the one student they structurally require:

```python
#!/usr/bin/env python3
"""Slim the canonical glow-dummies base CSV down to a manageable seed size.

The full base dataset (~9,263 students across 20 schools) produces ~60,918
ODK submissions once transformed via transform_mock_data.py -- over 5 hours
to seed through ODK's throttled HTTP API. This keeps every school's distinct
test-scenario plan intact (transform_mock_data.py assigns each school a
unique target_waves/phq_mode/v1-quirk combination) while cutting each school
down to a small, deterministically-chosen subset of its classes -- entire
classes only, never partial -- landing the transformed dataset around ~12k
submissions.

The two donor schools (transform_mock_data.py's alphabetically-last two
school names, used to supply wave-4/5 "joiner" data for other schools) only
need >=1 retained student for that role (transform_mock_data.py:375-393
picks one via a hash modulo the donor pool size -- no other minimum).  This
script's floor of 1 kept class per school (~28+ students on this dataset)
clears that by a wide margin.
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

SEED = 42
MAX_CLASSES_PER_SCHOOL = 6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # sorted() matches transform_mock_data.py's own school ordering exactly --
    # that ordering is what determines which two schools are donor-only, so
    # this must stay in lockstep with transform_mock_data.py:332.
    schools = sorted({row["school"] for row in rows})
    classes_by_school: dict[str, set[str]] = {}
    for row in rows:
        classes_by_school.setdefault(row["school"], set()).add(row["class"])

    rng = random.Random(SEED)
    kept_classes: dict[str, set[str]] = {}
    for school in schools:
        available = sorted(classes_by_school[school])
        n_keep = rng.randint(1, min(MAX_CLASSES_PER_SCHOOL, len(available)))
        kept_classes[school] = set(available[:n_keep])

    kept_rows = [row for row in rows if row["class"] in kept_classes[row["school"]]]

    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    kept_students = len({row["uid"] for row in kept_rows})
    print(f"Kept {kept_students} students ({len(kept_rows)} wave-rows) across {len(schools)} schools")
    for school in schools:
        print(f"  {school}: {len(kept_classes[school])}/{len(classes_by_school[school])} classes")


if __name__ == "__main__":
    main()
```

```bash
chmod +x scripts/odk/slim_mock_data.py
```

Expected output when run against a fresh `data/glow_base_raw.csv` (Step 5 below): `Kept 1861 students (5583 wave-rows) across 20 schools`, with South Joana Secondary School and West Pamelia College (the two donor-only schools) each retaining well above 1 student. If the printed donor counts come out at 0 for either school, stop — that would mean the school ordering assumption above no longer matches `transform_mock_data.py`, and the wave-4/5 mappings would silently produce fewer rows than expected.

- [ ] **Step 5: Generate `data/glow_base.csv` (prerequisite, not committed — already gitignored)**

```bash
cd ../glow-dummies
julia --project=. -e 'import Pkg; Pkg.instantiate()'
julia --project=. bin/glow_dummies --config examples/glow_model.toml --seed 42 \
  > ../glow/data/glow_base_raw.csv
cd ../glow
python scripts/odk/slim_mock_data.py \
  --input data/glow_base_raw.csv --output data/glow_base.csv
```

Note: `glow-dummies` is a Julia CLI tool in a sibling checkout (`../glow-dummies`), not a published package — discovered while building this task that `uvx glow-dummies` (the command this plan originally specified, copied from already-stale instructions elsewhere in this repo) does not work. If `../glow-dummies` isn't present or its `examples/glow_model.toml` config is missing, that's a separate, pre-existing documentation gap outside this task's scope — see this task's report for details rather than trying to fix glow-dummies' own repo from here.

- [ ] **Step 6: Run the script and record timing**

```bash
time ./scripts/odk/generate_seed_dump.sh
```

Expected: ends with `✅ Wrote odk-central/postgres/seed/dev-seed.dump (N bytes)`. Record the elapsed time and the dump size — update `odk-central/postgres/README.md`'s "Regenerating the seed data" section with the actual figures (replacing any hand-wavy "faster" language) once known.

- [ ] **Step 7: Verify the dump against Task 1's `dev` target for real**

```bash
docker build -t glow-odk-postgres-dev-real --target dev odk-central/postgres
docker network create odk-pg-real-test
START=$(date +%s.%N)
docker run --rm -d --name odk-pg-real-test --network odk-pg-real-test glow-odk-postgres-dev-real
for i in $(seq 1 30); do docker exec odk-pg-real-test pg_isready -U odk -d odk >/dev/null 2>&1 && break; sleep 0.3; done
END=$(date +%s.%N)
echo "boot-to-ready seconds: $(echo "$END - $START" | bc)"
docker exec odk-pg-real-test psql -U odk -d odk -c "SELECT count(*) FROM projects;"
docker exec odk-pg-real-test psql -U odk -d odk -c "SELECT count(*) FROM submissions;"
docker rm -f odk-pg-real-test
docker network rm odk-pg-real-test
docker rmi glow-odk-postgres-dev-real
```

Expected: boot-to-ready well under a few seconds; `projects` shows at least the one seeded project; `submissions` count is around 12,000-13,000 (per Step 4a's slimming, not the unfiltered dataset's ~60,918), matching `scripts/odk/generate_seed_dump.sh`'s printed totals from Step 6.

- [ ] **Step 8: Commit**

```bash
git add scripts/odk/generate_seed_dump.sh scripts/odk/seed-gen.compose.yml \
  scripts/odk/odk-api-helper.sh odk-central/postgres/seed/dev-seed.dump \
  odk-central/postgres/README.md
git commit -m "feat: generate and commit the real ODK Postgres seed dump"
```

Confirm the dump went through LFS, not plain git:

```bash
git lfs ls-files | grep dev-seed.dump
```

Expected: the file is listed (confirms LFS tracked it, per `.gitattributes` from Task 1).

---

## Task 3: Wire `compose.yml` / `compose.override.yml` / `compose.test.yml` to the new image

**Files:**
- Modify: `compose.yml:104-122` (`postgres14` service — `image:` → `build:`)
- Modify: `compose.override.yml` (add `postgres14: build: target: dev`)
- Modify: `compose.test.yml` (add `postgres14: build: target: dev`)

**Interfaces:**
- Consumes: `odk-central/postgres/Dockerfile`'s `base`/`dev` targets (Tasks 1-2) and the committed seed dump (Task 2).
- Produces: nothing new for later tasks — this is pure compose wiring.

- [ ] **Step 1: Change `postgres14`'s image reference to a build in `compose.yml`**

In `compose.yml`, replace (around line 105):

```yaml
  postgres14:
    image: postgres:14-alpine
```

with:

```yaml
  postgres14:
    build:
      context: ./odk-central/postgres
      target: base
```

Leave the rest of the `postgres14` block (`environment:`, `volumes:`, `networks:`, `restart:`, `healthcheck:`) unchanged.

- [ ] **Step 2: Add the `dev` target override to `compose.override.yml`**

Add a new `postgres14:` block (alongside the existing `api`, `dashboard`, `nginx` blocks):

```yaml
  postgres14:
    build:
      target: dev
```

- [ ] **Step 3: Add the `dev` target override to `compose.test.yml`**

Add a new `postgres14:` block (alongside the existing `api`, `api-db`, `dashboard` blocks), matching this file's existing convention of `restart: "no"` for test services:

```yaml
  postgres14:
    build:
      target: dev
    restart: "no"
```

- [ ] **Step 4: Verify the merged config for each compose combination**

```bash
docker compose -f compose.yml config --format json | jq '.services.postgres14.build'
```

Expected: `{"context": ".../odk-central/postgres", "dockerfile": "Dockerfile", "target": "base"}`.

```bash
docker compose -f compose.yml -f compose.override.yml config --format json | jq '.services.postgres14.build'
```

Expected: same `context`, `target: "dev"`.

```bash
docker compose -f compose.yml -f compose.test.yml config --format json | jq '.services.postgres14.build, .services.postgres14.restart'
```

Expected: `target: "dev"`, `restart: "no"`.

- [ ] **Step 5: Boot the base stack and confirm no regression**

```bash
docker compose -f compose.yml up -d --build postgres14
docker compose -f compose.yml exec postgres14 psql -U odk -d odk -c "SELECT count(*) FROM pg_tables WHERE schemaname='public';"
docker compose -f compose.yml down -v
```

Expected: builds `base`, boots empty (0 or near-0 application tables — whatever a truly blank ODK-less Postgres reports), no different from today's `image: postgres:14-alpine` behavior.

- [ ] **Step 6: Boot the dev-override stack and confirm seeded data is present**

```bash
docker compose -f compose.yml -f compose.override.yml up -d --build postgres14
docker compose -f compose.yml -f compose.override.yml exec postgres14 psql -U odk -d odk -c "SELECT count(*) FROM submissions;"
docker compose -f compose.yml -f compose.override.yml down
```

Expected: submission count matches Task 2's dump, and the `up` completes almost immediately (no restore-on-boot wait).

- [ ] **Step 7: Commit**

```bash
git add compose.yml compose.override.yml compose.test.yml
git commit -m "feat: build postgres14 from base/dev targets instead of a bare image"
```

---

## Task 4: Shrink `dev-init.sh`

**Files:**
- Modify: `dev-init.sh`

**Interfaces:**
- Consumes: `compose.override.yml`'s `postgres14: build: target: dev` (Task 3) and the fixed dev credentials documented in `odk-central/postgres/README.md` (Task 1).
- Produces: nothing new for later tasks.

Drops: prerequisite checks for `jq`/`uv` (no longer used anywhere in this script), ODK admin/project/API-user creation, mock-data generation/transform/POST-seeding, the timestamp-rewrite call, the `--limit` flag, the curl-wrapper TLS shim, and the `SKIP_SEED` branching (data is now always present). Keeps: prerequisite check for `docker`, `.env`/`.env.dev` credential writing (now fixed values, not generated), starting containers, waiting for ODK/API health, creating the Glow admin user, and schools sync (always runs now).

- [ ] **Step 1: Replace the prerequisite checks (around lines 180-194)**

Replace:

```bash
check_command "docker" "https://docs.docker.com/get-docker/"
check_command "jq" "apt install jq (or brew install jq)"
check_command "uv" "curl -LsSf https://astral.sh/uv/install.sh | sh"
```

with:

```bash
check_command "docker" "https://docs.docker.com/get-docker/"
```

- [ ] **Step 2: Remove the `--limit` option**

Remove the `LIMIT=""` variable (line 25) and its `--limit)` case in the argument parser (lines 158-161):

```bash
    --limit)
      LIMIT="$2"
      shift 2
      ;;
```

Remove `--limit N` from `show_help`'s usage text and examples too.

- [ ] **Step 3: Replace the credential-generation block (lines 222-278) with fixed values**

Replace the entire "Step 4: Generate or Reuse Credentials" section through the `.env` update (everything from `step "Generating credentials"` through `info "Updated ODK credentials in .env for docker-compose"`) with:

```bash
step "Writing fixed dev credentials"

# odk-central/postgres/README.md documents these -- they're baked into the
# dev image's seed data at build time (scripts/odk/generate_seed_dump.sh),
# not generated per-run. GLOW_ADMIN_USER/PASSWORD stay simple local-only
# values, unrelated to the baked ODK data.
ODK_ADMIN_EMAIL="admin@glow.local"
ODK_ADMIN_PASSWORD="devpassword"
ODK_API_EMAIL="api@glow.local"
ODK_API_PASSWORD="devpassword"
GLOW_ADMIN_USER="admin"
GLOW_ADMIN_PASSWORD="admin"

cat > .env.dev <<EOF
# Auto-generated by dev-init.sh - DO NOT COMMIT
# Generated: $(date -u +"%Y-%m-%d %H:%M:%S UTC")

# ODK Central Admin (fixed dev credentials -- see odk-central/postgres/README.md)
ODK_ADMIN_EMAIL=${ODK_ADMIN_EMAIL}
ODK_ADMIN_PASSWORD=${ODK_ADMIN_PASSWORD}

# ODK Central API User (for Glow API)
ODK_API_EMAIL=${ODK_API_EMAIL}
ODK_API_PASSWORD=${ODK_API_PASSWORD}

# Glow API Admin
GLOW_ADMIN_USER=${GLOW_ADMIN_USER}
GLOW_ADMIN_PASSWORD=${GLOW_ADMIN_PASSWORD}
EOF

info "Credentials ready: ${ODK_ADMIN_EMAIL}"

# Update .env with credentials needed by docker-compose
# (Do this early so they're available when containers start)
if grep -q "# ODK Central Integration" .env 2>/dev/null; then
  sed -i '/# ODK Central Integration/,/^$/d' .env
fi

cat >> .env <<EOF

# ODK Central Integration (added by dev-init.sh)
# Use HTTPS through nginx (Basic Auth requires HTTPS)
GLOW_ODK_API_URL=https://nginx
GLOW_ODK_API_EMAIL=${ODK_API_EMAIL}
GLOW_ODK_API_PASSWORD=${ODK_API_PASSWORD}
GLOW_ODK_PROJECT_ID=1
GLOW_ODK_FORM_ID=bewell_questionnaire
GLOW_ODK_VERIFY_SSL=false
EOF
info "Updated ODK credentials in .env for docker-compose"
```

- [ ] **Step 4: Remove the ODK admin/project/API-user bootstrap section (old "Step 7")**

Remove everything from `step "Configuring ODK Central"` through the `odk_assign_role` call — i.e. the `source .env.dev` re-read, the ODK CLI `user-create`/`user-promote` calls, the `source ./scripts/odk/odk-api-helper.sh` line, the `CURL_WRAPPER_DIR` temp-curl-wrapper block, and the `odk_login`/`odk_create_project`/`odk_create_user`/`odk_assign_role` calls. None of this is needed — the accounts and project already exist in the baked seed data.

- [ ] **Step 5: Remove the mock-data seeding section (old "Step 8") and drop `SKIP_SEED`**

Remove the entire block from `SEED_DIR="./data/mock_seed"` through `SKIP_SEED=false` (the `transform_mock_data.py` call, the "Test data not found" instructions, the `seed_odk_test_data.py`/`rewrite_odk_submission_timestamps.py` calls). Also remove the `SKIP_SEED=false` variable declaration near the top (line 26) and both later references to `SKIP_SEED` (the schools-sync conditional and the final summary's status line) — schools sync now always runs, and the summary always reports data as seeded.

Replace the schools-sync conditional:

```bash
if [[ ${SKIP_SEED} != true ]]; then
  info "Extracting schools from data and creating neighbor relationships"
  docker compose exec -T api glow-api schools sync
else
  info "Skipping school extraction (no data seeded)"
fi
```

with:

```bash
info "Extracting schools from data and creating neighbor relationships"
docker compose exec -T api glow-api schools sync
```

And the final summary's status block:

```bash
if [[ ${SKIP_SEED} != true ]]; then
  echo "   Status:       ✅ Data seeded"
else
  echo "   Status:       ⚠️  No data seeded - follow instructions above"
fi
```

with:

```bash
echo "   Status:       ✅ Data seeded (baked into the postgres14 dev image)"
```

- [ ] **Step 6: Remove the now-dead curl-wrapper cleanup line at the end of the file**

Remove the final `rm -rf "$CURL_WRAPPER_DIR" 2>/dev/null || true` line — `$CURL_WRAPPER_DIR` no longer exists anywhere in the script.

- [ ] **Step 7: Update the help text and tips**

In `show_help`, remove the `--limit N` line and its usage example (already covered in Step 2). In the final "💡 Tips" section, remove the `- Use --limit 100 for faster seeding...` line.

- [ ] **Step 8: Run the full script end-to-end**

```bash
./dev-init.sh --reset
```

Expected: completes without calling `uv`/`jq`, without any ODK HTTP bootstrap output, reaches "✅ Development environment initialized successfully!" with `Status: ✅ Data seeded`. Then verify against the running stack:

```bash
curl -s http://localhost:8000/dimensions | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('variables', [])))"
```

Expected: a non-zero variable count, confirming the Glow API is reading real data from the pre-seeded ODK instance.

- [ ] **Step 9: Commit**

```bash
git add dev-init.sh
git commit -m "feat: drop dev-init.sh's ODK bootstrap now that postgres14 ships pre-seeded"
```

---

## Task 5: Shrink `scripts/smoke_compose.sh`

**Files:**
- Modify: `scripts/smoke_compose.sh`

**Interfaces:**
- Consumes: `compose.test.yml`'s `postgres14: build: target: dev` (Task 3).
- Produces: the `up -d --wait` (no `--build`) invocation pattern this task establishes is what makes Task 6's image-cache-load path actually skip rebuilding `postgres14`.

Drops: the entire ODK bootstrap block (admin creation, project creation, form upload, `seed_smoke_data.py`, the curl-wrapper TLS shim). Also splits the single `up --build` call into an explicit `build` of only `api`/`dashboard` followed by a plain `up` — `--build` on `docker compose up` forces a rebuild of every service in the dependency graph (including `postgres14`, a dependency of `api` via `odk-service`), which would silently defeat Task 6's cached-image-load optimization. Compose already builds a service automatically when its image is missing (e.g. the very first local run, or a CI cache-miss handled by Task 6's own explicit build step) — it just won't force a *rebuild* when a valid image is already present, which is exactly the behavior needed here.

- [ ] **Step 1: Replace the combined build+up call**

Replace:

```bash
docker compose -f compose.yml -f compose.test.yml down -v --remove-orphans
docker compose -f compose.yml -f compose.test.yml up --build -d --wait
```

with:

```bash
docker compose -f compose.yml -f compose.test.yml down -v --remove-orphans
docker compose -f compose.yml -f compose.test.yml build api dashboard
docker compose -f compose.yml -f compose.test.yml up -d --wait
```

- [ ] **Step 2: Remove the ODK bootstrap block**

Remove everything from the `# Seed ODK Central with the minimal PHQ-9 fixture...` comment through the `seed_smoke_data.py` call — i.e. the `ODK_ADMIN_EMAIL`/`ODK_ADMIN_PASSWORD`/`ODK_API_EMAIL`/`ODK_API_PASSWORD` fixed-value assignments, the ODK CLI `user-create`/`user-promote` calls, the `ODK_CURL_WRAPPER_DIR` temp-curl-wrapper block, the `odk_login`/`odk_create_project`/`odk_create_user`/`odk_assign_role`/`odk_upload_form` calls, and the `seed_smoke_data.py` invocation. The comment above the `GLOW_ODK_API_URL` export block (`# compose.yml's default GLOW_ODK_API_URL...`) stays — that export block itself is unaffected by this change, since `GLOW_ODK_PROJECT_ID` is now always `1` from the baked seed, matching what this script's export already needs.

Update the `export GLOW_ODK_PROJECT_ID="$PROJECT_ID"` line (now that `$PROJECT_ID` is no longer defined) to the fixed value:

```bash
export GLOW_ODK_PROJECT_ID=1
```

- [ ] **Step 3: Run the script and confirm the smoke checks still pass**

```bash
bash scripts/smoke_compose.sh
```

Expected: completes without any ODK CLI/HTTP bootstrap output, `/dimensions` polling succeeds (now reflecting the full baked dataset instead of the old phq9-only fixture — this is the intended effect of unifying to one dataset), and the final schools listing prints successfully.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_compose.sh
git commit -m "feat: drop smoke_compose.sh's ODK bootstrap now that postgres14 ships pre-seeded"
```

---

## Task 6: CI caching — skip the LFS pull on a cache hit

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `odk-central/postgres/Dockerfile`/`bake-seed.sh`/`seed/dev-seed.dump` (Tasks 1-2) as the cache-key inputs, and Task 5's `build api dashboard` + `up -d --wait` split (so a cache-hit's loaded image doesn't get force-rebuilt).

Adds a cache step for the *built* `postgres14` `dev` image, keyed off content that's present without ever resolving the LFS pointer to its real content. On a cache hit, the real seed dump is never downloaded. On a cache miss (first run, or a change to the Dockerfile/bake script/dump), only that one LFS path is pulled, the image is built, and the result is saved for next time.

- [ ] **Step 1: Add the cache/build/load steps before the existing smoke-test step**

In `.github/workflows/ci.yml`, insert these steps in the `compose-smoke` job, after `actions/checkout@v7` and before `actions/setup-python@v7` (order relative to the Python/Node setup steps doesn't matter — inserting right after checkout keeps the Docker-related steps grouped together):

```yaml
      - name: Compute ODK postgres dev image cache key
        id: odk-image-key
        run: |
          echo "key=odk-postgres-dev-${{ hashFiles('odk-central/postgres/Dockerfile', 'odk-central/postgres/bake-seed.sh', 'odk-central/postgres/seed/dev-seed.dump') }}" >> "$GITHUB_OUTPUT"

      - name: Restore cached ODK postgres dev image
        id: odk-image-cache
        uses: actions/cache@v4
        with:
          path: /tmp/odk-postgres-dev.tar
          key: ${{ steps.odk-image-key.outputs.key }}

      - name: Pull seed dump (cache miss only)
        if: steps.odk-image-cache.outputs.cache-hit != 'true'
        run: git lfs pull --include odk-central/postgres/seed/dev-seed.dump

      - name: Build ODK postgres dev image (cache miss only)
        if: steps.odk-image-cache.outputs.cache-hit != 'true'
        run: |
          docker compose -f compose.yml -f compose.test.yml build postgres14
          docker save "$(docker compose -f compose.yml -f compose.test.yml config --images postgres14)" -o /tmp/odk-postgres-dev.tar

      - name: Load cached ODK postgres dev image (cache hit only)
        if: steps.odk-image-cache.outputs.cache-hit == 'true'
        run: docker load -i /tmp/odk-postgres-dev.tar
```

- [ ] **Step 2: Confirm `actions/checkout@v7` is not requesting LFS**

Verify the existing checkout step has no `lfs:` key at all (its default is `false`, which is what's wanted — checking out an LFS-tracked file without `lfs: true` still gets the pointer text, which is exactly what `hashFiles` above needs and all that's needed unless Step 1 above determines a real pull is required):

```yaml
      - uses: actions/checkout@v7
```

No change needed if it already looks like this.

- [ ] **Step 3: Push to a branch and confirm both paths in the Actions run**

```bash
git push -u origin HEAD
```

Open the resulting Actions run. On this first run (cache miss, since the key has never been seen before): confirm the "Pull seed dump" and "Build ODK postgres dev image" steps ran, and "Load cached ODK postgres dev image" was skipped.

Push a trivial unrelated commit (e.g. a comment tweak in `ci.yml` itself, or any change outside `odk-central/postgres/`) and push again. Confirm this second run hits the cache: "Restore cached ODK postgres dev image" reports a hit, "Pull seed dump" and "Build ODK postgres dev image" are both skipped, "Load cached ODK postgres dev image" ran instead, and the overall smoke test still passes.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "feat: cache the built ODK postgres dev image in CI, skip LFS on cache hit"
```

---

## Self-Review Notes

- **Spec coverage:** every spec section has a task — Architecture/Seed dump → Tasks 1-2, CI caching → Task 6, Credentials → Tasks 1/2/4 (documented + baked + written to `.env`), dev-init.sh/smoke_compose.sh changes → Tasks 4-5, Risks (Enketo limitation, schema drift) → Task 1's README, image size → inherent to Task 1's approach (not a separate task, it's a stated trade-off), Testing → verification steps embedded in each task.
- **Corrections made from the spec's literal text during planning, verified empirically before being written in:** (1) the spec said "delete the copied-in dump file... so the dump isn't duplicated across layers" — a same-layer `rm` doesn't actually shrink a prior `COPY` layer's history, so Task 1 uses a two-stage `dev-builder`/`dev` split instead, which does. (2) Compose's `ports:` list merges by concatenation, not by-target replacement — Task 2's `seed-gen.compose.yml` needed the `!override` tag, confirmed against this repo's actual Compose version (v5.1.2) before being written into the plan.
- **Type/name consistency checked:** `ODK_CURL_INSECURE` (Task 2) is used consistently in both the `odk_curl` implementation and `generate_seed_dump.sh`'s call site. `COMPOSE_PROJECT_NAME=glow-seed-gen` (Task 2) is relied upon by `rewrite_odk_submission_timestamps.py`'s bare `docker compose exec` calls (that script takes no project-name argument), not just the script's own `$COMPOSE` variable — both need the same project name, which the exported env var guarantees.
