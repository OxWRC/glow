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
