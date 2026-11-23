#!/bin/sh
# Refuses to create an empty database while the PostgreSQL 16 database of an
# earlier RouterFleet is mounted beside it (read-only, at the legacy path):
# taking the new compose file without migrating must stop here with what to
# do - not start RouterFleet on an empty database. The migration script sets
# RF_MIGRATION=1 for the one start that creates the new database.
set -eu
data="${PGDATA:-/var/lib/postgresql/18/docker}"
legacy=/var/lib/postgresql-legacy
if [ ! -s "$data/PG_VERSION" ] && [ -s "$legacy/PG_VERSION" ] && [ "${RF_MIGRATION:-}" != "1" ]; then
    echo "RouterFleet: the database of an earlier version (PostgreSQL $(cat "$legacy/PG_VERSION")) is" >&2
    echo "RouterFleet: still to be moved to PostgreSQL 18. Nothing was changed." >&2
    echo "RouterFleet: run, in the folder of the compose file:  ./scripts/migrate-database.sh" >&2
    echo "RouterFleet: (docs/upgrading.md, 'Moving to PostgreSQL 18')" >&2
    exit 1
fi
exec docker-entrypoint.sh "$@"
