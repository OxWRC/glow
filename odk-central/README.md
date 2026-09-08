# ODK Central In This Repo

This directory contains the local support files for the optional `odk` Docker Compose profile.

We run ODK Central to handle the raw data collection and storage in a secure and interoperable way.
The ODK Central data is then syndicated through GLOW's API.

## Why So Many Containers?

ODK Central is a multi-service application upstream, not a single image.

In `compose.yml`, the `odk` profile includes:

- `nginx`
  - public entrypoint for Central
  - exposes ports `80` and `443` to the host as `${ODK_HTTP_PORT}` and `${ODK_HTTPS_PORT}`
  - serves the Central frontend and proxies to the backend and Enketo
- `service`
  - the main ODK Central backend
  - handles auth, forms, submissions, and the REST/OpenRosa APIs
- `postgres14`
  - ODK Central's own database
- `pyxform`
  - converts XLSForm files for Central
- `enketo`
  - browser-based web form runtime used by Central
- `enketo_redis_main`
  - Enketo's primary Redis store
- `enketo_redis_cache`
  - Enketo's cache Redis store
- `secrets`
  - creates the secret files Enketo and Central expect to find on a shared volume
- `mail`
  - local SMTP sink for Central mail integration

## Network Shape

The compose setup intentionally separates public reachability from private service wiring:

- `public`
  - only for services that publish ports to the host
  - currently GLOW `api`, GLOW `dashboard`, and ODK `nginx`
- `odk_internal`
  - private ODK Central service-to-service traffic
- `odk_api`
  - the only shared network between ODK Central and GLOW
  - only `service` and GLOW `api` are attached

This is why ODK Central cannot talk directly to the GLOW dashboard.

## Why These Local Files Exist

We use upstream-published images where possible, but some of those images expect mounted templates or startup scripts.

This directory exists to provide the minimum local files needed for that:

- `enketo/`
  - local Enketo image wrapper
  - injects runtime config and secret handling that upstream Central normally provides from its own repo
- `nginx/`
  - Central nginx templates mounted into `ghcr.io/getodk/central-nginx`
- `redis/`
  - Enketo Redis configs mounted into the Redis containers
- `postgres/`
  - local Dockerfile for the `postgres14` service's `base`/`dev` build
    targets -- see `postgres/README.md` for the `dev` target's baked seed
    data and fixed credentials

These files are mostly copied from the upstream `getodk/central` repo so that the official images can boot in this repository without vendoring the full Central source tree.

## Version Pinning And Why It Looks Old

Several images are pinned to versions that may look behind the rest of this repo.

### `postgres14`

The ODK Central stack is pinned to `postgres:14-alpine`, not Postgres 16.

Why:

- upstream Central's service image installs `postgresql-client-14`
- upstream Central's official compose file still uses a `postgres14` service name and Postgres 14 runtime
- keeping the runtime aligned with the Central release reduces the chance of subtle incompatibilities during migrations or startup

This is separate from GLOW's own metadata database, which is Postgres 16.

### `service` and `nginx`

These use:

- `ghcr.io/getodk/central-service:${ODK_CENTRAL_TAG}`
- `ghcr.io/getodk/central-nginx:${ODK_CENTRAL_TAG}`

Default tag:

- `v2026.1.2`

Why:

- these are official published images from the ODK Central project
- pinning them through one shared tag makes upgrades explicit and keeps `service` and `nginx` on the same Central release

### `enketo`

The local `enketo/Dockerfile` builds from:

- `ghcr.io/enketo/enketo:7.5.1`

Why:

- upstream Central still treats Enketo as a separate service with its own config expectations
- we need a thin local wrapper to provide the templated config and startup script

### `pyxform`

Pinned to:

- `ghcr.io/getodk/pyxform-http:v4.4.1`

Why:

- this matches the dependency shape used by current upstream Central releases

### `redis`

Pinned to:

- `redis:8.6.2`

Why:

- copied from the current upstream Central compose definition we based this on

### `mail`

Uses:

- `axllent/mailpit:v1.27`

Why:

- this is intentionally not upstream-exact
- upstream Central uses a lightweight SMTP image oriented around real outbound mail
- here we want a development-safe mail sink that is simpler to run locally

## Why Not Vendor The Whole ODK Central Repo?

That would make upgrades and ownership worse here.

Instead, this repo keeps:

- official `central-service` and `central-nginx` images
- a small number of mounted templates and helper scripts
- a thin local Enketo wrapper

This keeps the amount of ODK-specific code in this repo relatively small while still matching the upstream container topology closely enough to be understandable.

## Upgrade Notes

If you need to update ODK Central, check these together:

1. `ODK_CENTRAL_TAG` in `.env.example` and your runtime env
2. `postgres14` compatibility with that Central release
3. `odk-central/postgres/seed/dev-seed.dump` -- regenerate via
   `scripts/odk/generate_seed_dump.sh` if you bump `ODK_CENTRAL_TAG`; the
   dump is tied to the migration state it was generated against.
4. `pyxform` version expected by upstream compose
5. `enketo` base image compatibility
6. local files in:
   - `enketo/`
   - `nginx/`
   - `redis/`

When upgrading, compare this repo against the corresponding upstream `getodk/central` release's compose file and template files. The local files here are intentionally derived from upstream, so drift is the main risk.
