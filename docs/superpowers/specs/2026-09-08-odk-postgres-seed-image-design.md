# ODK Postgres seed image

## Problem

Every fresh dev/demo/CI environment seeds ODK Central the same way: boot an
empty `postgres14`, bootstrap an ODK admin user via CLI, create a project and
API user via HTTP, generate mock data (glow-dummies), transform it into ODK
submission XML, POST ~12k submissions to ODK's HTTP API one at a time, then
run a separate script to directly rewrite `submissions`/`submission_defs`
timestamps in Postgres so the data lands in realistic academic-year buckets
(ODK stamps `createdAt` as "now" on ingest, which isn't useful for a
historical dataset).

This happens three times with three different implementations:
`dev-init.sh` (full ~12k-row dataset), `scripts/smoke_compose.sh` (a small
fixed phq9-only fixture for CI), plus the standalone timestamp-rewrite step.
All three pay the same live-HTTP-bootstrap cost on every fresh environment,
and the CI/smoke path exercises meaningfully different (smaller, single-form)
data than what a developer sees locally.

## Goal

Bake the seed data directly into the ODK Postgres image so a fresh
environment starts with a fully-migrated, pre-populated database — no HTTP
bootstrap, no per-run submission POSTing, no separate timestamp-rewrite step.
One dataset, shared by local dev, demo, and CI, so CI is exercising the same
data shape developers see (and can grow into e2e tests later without a
separate fixture to maintain).

## Non-goals

- Changing how `api-db` (Glow's own Postgres) is seeded — unaffected.
- Changing production credential handling — `base` target keeps taking
  `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` from env exactly as today.
- Automating schema-drift detection across ODK version bumps — documented
  convention only (see Risks).

## Architecture

New `odk-central/postgres/Dockerfile` with two build targets, replacing the
inline `image: postgres:14-alpine` reference in `compose.yml`:

- **`base`** — `FROM postgres:14-alpine`, unmodified. PGDATA stays at the
  image's default `/var/lib/postgresql/data`, which the base image declares
  as a `VOLUME` (confirmed via `docker inspect postgres:14-alpine`). This is
  what `compose.yml` builds by default, and what production runs — blank
  cluster, current bind-mount-to-host-dir behavior, current env-var-driven
  credentials.

- **`dev`** — `FROM base`, bakes the seed data into the actual Postgres data
  files at *build* time, not at container-start time. Key points:

  - Sets `ENV PGDATA=/var/lib/postgresql/pgdata` — a path the base image does
    **not** declare as a `VOLUME`. This is the critical detail: writing to
    the default `/var/lib/postgresql/data` inside a `RUN` would silently land
    in a throwaway anonymous volume Docker mounts there during build, and the
    resulting image would look fine but boot empty.
  - One `RUN` layer: `initdb` as the `postgres` user (must not run as root) →
    start postgres socket-only (`-c listen_addresses=''`, no TCP, avoids
    build-time port conflicts) → `pg_restore` the seed dump → `pg_ctl -m fast
    -w stop` and confirm a clean exit (an unclean shutdown leaves WAL
    recovery to replay on every future container boot, which is exactly the
    cost this design removes) → delete the copied-in dump file, all inside
    the same `RUN` so the dump isn't duplicated across layers.
  - No volume is mounted over `/var/lib/postgresql/pgdata` for this target.
    Once the cluster is materialized in the image layer, a mount there would
    shadow it (bind mounts unconditionally; named volumes after first use).
    Dropping the mount means `docker compose down && up --build` always
    yields an instantly-pristine seeded database — no stale-data footgun to
    document or guard against.
  - `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` become no-ops at
    runtime for this target — the official entrypoint only applies them
    during `initdb`, which already happened at build time. The role and
    password are frozen into the image. `dev-init.sh` and `compose.override.yml`
    must supply the fixed dev DB credentials baked at generation time (see
    Credentials below), not whatever `ODK_POSTGRES_PASSWORD` happens to be in
    `.env`.

`compose.yml`'s `postgres14` service changes from `image: postgres:14-alpine`
to `build: {context: ./odk-central/postgres, target: base}` (same shape as
the existing `api`/`dashboard` build blocks). `compose.override.yml` (dev)
and `compose.test.yml` (CI/smoke) both add `build: {target: dev}` — the exact
pattern already used for `dashboard: build: target: dev` in
`compose.override.yml` today.

## Seed dump

- Format: `pg_dump -Fc` (custom format) piped through the restore directly —
  smaller artifact than plain SQL, and `pg_restore -j` parallelizes the
  build-time restore. (Plain SQL was considered and rejected: it only matters
  for postgres's `docker-entrypoint-initdb.d` auto-restore-on-first-boot
  mechanism, which this design doesn't use — the restore is invoked
  explicitly inside the Dockerfile's `RUN`.)
- Stored at `odk-central/postgres/seed/dev-seed.dump`, tracked via **Git
  LFS** (`.gitattributes`: `odk-central/postgres/seed/*.dump filter=lfs
  diff=lfs merge=lfs -text`). Estimated tens-of-MB scale (~12k submissions);
  regenerated periodically as the model/forms/timestamps change, so plain git
  would otherwise accumulate a full binary blob per regeneration in history.
  CI checkout must set `lfs: true` (default is `false`) or the build context
  gets a ~130-byte pointer file instead of the dump.
- Contains: ODK project id `1`, admin + API user accounts with fixed dev
  passwords (bcrypt hashes baked in — see Credentials), and all four forms
  currently seeded by `dev-init.sh`'s pipeline: `bewell_questionnaire` (v1
  and v2), `phq9_questionnaire`, `demographics_questionnaire`. This replaces
  `smoke_compose.sh`'s separate small phq9-only fixture — CI now exercises
  the same dataset developers see locally.
- Generated by a new `scripts/odk/generate_seed_dump.sh`, which **relocates**
  (does not delete) the existing bootstrap pipeline: spins up scratch
  `postgres14` (`base` target) + `odk-service` containers, reuses
  `odk-api-helper.sh`, `transform_mock_data.py`, `seed_odk_test_data.py`, and
  `rewrite_odk_submission_timestamps.py` to create the project/users/forms,
  POST submissions, and backdate timestamps — then `pg_dump -Fc`s the
  resulting database to `dev-seed.dump`. The curl `-k`/`Host: odk.local` TLS
  shim currently duplicated in both `dev-init.sh` and `smoke_compose.sh`
  moves into `odk-api-helper.sh` as a shared function while this script is
  written, rather than being duplicated a third time.
  - Asserts the created project id is `1` before dumping (the seed's
    consumers hardcode this).
  - Sanity-checks the output dump's file size against a minimum threshold
    before finishing, so a truncated or LFS-pointer-only artifact fails
    loudly instead of producing a container that boots healthy with an
    empty-looking (or wrong) dataset.

## Credentials

Two independent layers, both fixed for the `dev` target (never for `base`/prod):

1. **ODK application accounts** (admin, API user) — bcrypt hashes baked into
   the dump at generation time. Fixed dev password (e.g. `devpassword`),
   documented in `odk-central/postgres/README.md`. Replaces `dev-init.sh`'s
   current per-run `generate_password()` call for this path — matches how
   `smoke_compose.sh` already uses a fixed `SmokeTest123!` today.
2. **Postgres role/database** (`POSTGRES_USER`/`PASSWORD`/`DB`) — frozen at
   `initdb` time inside the `dev` image build, so they're no longer
   env-var-driven for this target at runtime. `generate_seed_dump.sh` bakes a
   fixed value (matching today's `odk`/`odk`/`odk` defaults is sufficient);
   `dev-init.sh` writes these fixed values into `.env` instead of passing
   through whatever `ODK_POSTGRES_PASSWORD` a developer might have set.

`base`/production is unaffected by both: env-var-driven passwords, current
deploy-script flow, no baked accounts.

## dev-init.sh / smoke_compose.sh changes

`dev-init.sh` drops: ODK admin/project/API-user creation, mock-data
generation/transform/POST-seeding, and the timestamp-rewrite call. It keeps:
writing the (now-fixed) ODK credentials to `.env`, starting containers,
creating the Glow admin user, and schools sync (all against `api-db`,
untouched by this change). The `--limit N` flag (today: limits how many rows
get POSTed per run) no longer applies — row count is now fixed at
seed-generation time, controlled by `generate_seed_dump.sh`, not per dev-init
run.

`smoke_compose.sh` drops its entire ODK bootstrap block (admin creation,
project creation, form upload, `seed_smoke_data.py`) and relies on the baked
`dev`-target data, same as local dev. Its Glow-side user/school setup is
unchanged. Its `/dimensions` assertion now reflects the full seeded dataset
rather than the old phq9-only fixture — this is the intended effect of
unifying to one dataset, not a regression to guard against.

## Risks / known limitations

- **Schema drift on ODK version bumps.** The dump is tied to whatever
  migration state `ODK_CENTRAL_TAG` was at when generated. Bumping that tag
  requires regenerating the dump. Documented as a convention (a comment in
  `generate_seed_dump.sh` and a PR-checklist line), not automated — version
  bumps are rare and deliberate.
- **Enketo webform links.** Enketo's survey cache lives in a separate bind
  mount (`docker-mount-data/odk-enketo-redis-main`) that the dump doesn't
  cover. If seeded forms have live Enketo webform links, those may not
  resolve in a freshly-built environment. Data reads (OData exports, Glow
  API) are unaffected — this only matters for someone clicking through to
  Enketo's own web-form UI against seeded submissions. One line in
  `odk-central/postgres/README.md`.
- **Build/run platform must match.** The baked cluster is portable across
  containers built from the same Postgres major version, OS/libc, and CPU
  architecture. Since `dev` always builds `FROM base` (the same image used at
  runtime), this holds automatically for single-arch builds; a multi-arch CI
  build must run `initdb` natively per architecture (standard `buildx`
  behavior), not attempt to reuse one arch's baked layer for another.
- **Image size.** The `dev` target's layer includes the full materialized
  Postgres data files (tens of MB), larger than a thin `pg_dump` layer would
  be, in exchange for the near-zero container-boot cost. Acceptable trade
  given the seed changes rarely and Docker layer caching means most builds
  don't pay this cost anyway.

## Testing

- `generate_seed_dump.sh` run once locally; measure and record actual
  restore time (both the one-time build-time restore and resulting container
  boot time) in this doc or the script's header, replacing "faster" with a
  real number.
- `docker compose build postgres14` with `target: dev` against a clean
  Docker build cache → verify the resulting container reaches healthy
  quickly, `psql` row counts match the manifest used to generate the dump,
  and ODK Central's UI shows the project/forms/submissions with backdated
  timestamps.
- Full `dev-init.sh` run end-to-end, confirming the shrunk script still
  produces a working Glow admin + schools sync against the pre-seeded ODK
  data.
- `scripts/smoke_compose.sh` run in CI with `lfs: true` on checkout,
  confirming `/dimensions` reflects the full seeded dataset.
- Confirm `base` target still builds and boots exactly as `postgres:14-alpine`
  does today (blank cluster, env-var credentials, bind-mount persistence) —
  no regression for production.
