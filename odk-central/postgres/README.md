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

`generate_seed_dump.sh` needs `data/glow_base.csv` -- the slimmed dataset
produced by `scripts/odk/slim_mock_data.py` from glow-dummies' raw output
(see that script and `generate_seed_dump.sh`'s header comment for the exact
two-stage command). The unfiltered glow-dummies output is ~9,263 students
across 20 schools, which transforms into ~60,918 ODK submissions -- at ODK's
throttled HTTP seeding rate (~2.9 submissions/sec) that's a 5-6 hour
regeneration. `slim_mock_data.py` thins classes-per-school (never dropping a
school, since each has a distinct test-scenario plan) down to ~12k
submissions instead.

Measured on the last regeneration: 1,790 students / 5,370 wave-rows / 20
schools slimmed down from the raw glow-dummies output, transforming into
12,427 ODK submissions, seeded with 0 failures in 4,293.7s (~71.6 min).
Total script wall time (stack boot, seeding, timestamp backdating, dump,
teardown) was ~72.7 min, producing a 2,712,594-byte (~2.6MB) dump.

## Known limitations

- Enketo's webform survey cache lives in a separate bind mount
  (`docker-mount-data/odk-enketo-redis-main`) that this dump doesn't cover
  -- live Enketo webform links from seeded forms may not resolve in a
  freshly built environment. Data reads (OData exports, the Glow API) are
  unaffected.
