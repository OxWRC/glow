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
