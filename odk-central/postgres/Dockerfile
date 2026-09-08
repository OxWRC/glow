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
