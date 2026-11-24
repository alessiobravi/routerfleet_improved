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
    # Everything needed, in order: often only the compose file was downloaded
    cat >&2 <<EOT
RouterFleet: the database of an earlier version (PostgreSQL $(cat "$legacy/PG_VERSION")) is
RouterFleet: still to be moved to PostgreSQL 18. Nothing was changed.
RouterFleet: In the folder of the compose file, on the host (not in a container):
RouterFleet:   mkdir -p scripts
RouterFleet:   wget -O scripts/migrate-database.sh https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/scripts/migrate-database.sh
RouterFleet:   chmod +x scripts/migrate-database.sh
RouterFleet:   ./scripts/migrate-database.sh
RouterFleet: Ports 80 or 443 taken by another web server? Set HTTP_PORT and HTTPS_PORT in .env first.
RouterFleet: Or go back: the previous compose file, then docker compose up -d.
RouterFleet: (docs/upgrading.md, 'Moving to PostgreSQL 18')
EOT
    exit 1
fi
exec docker-entrypoint.sh "$@"
