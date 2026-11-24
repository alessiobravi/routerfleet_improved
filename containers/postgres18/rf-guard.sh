#!/bin/sh
# The first start of the PostgreSQL 18 database, beside the PostgreSQL 16
# database of an earlier RouterFleet (mounted read-only at the legacy path):
# the database is moved by itself - export, import, every table compared - and
# only then started. The old volume is never changed: this copies its files to
# a working folder (as root: they belong to another user id), and
# rf-migrate-initdb.sh does the move while the new database is created.
#
# RF_AUTO_MIGRATE=0 keeps the database waiting for scripts/migrate-database.sh
# instead; the script sets RF_MIGRATION=1 for the start it makes itself. A move
# that did not finish leaves a marker: the database then refuses to start,
# never on half the data.
set -eu
root=/var/lib/postgresql
data="${PGDATA:-$root/18/docker}"
legacy=$root-legacy
copy=$root/rf-pg16-copy
marker=$root/rf-migration-incomplete

say() { echo "RouterFleet: $*" >&2; }
manual() {
    cat >&2 <<EOT
RouterFleet: In the folder of the compose file, on the host (not in a container):
RouterFleet:   mkdir -p scripts
RouterFleet:   wget -O scripts/migrate-database.sh https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/scripts/migrate-database.sh
RouterFleet:   chmod +x scripts/migrate-database.sh
RouterFleet:   ./scripts/migrate-database.sh
RouterFleet: Ports 80 or 443 taken by another web server? Set HTTP_PORT and HTTPS_PORT in .env first.
RouterFleet: Or go back: the previous compose file, then docker compose up -d.
RouterFleet: (docs/upgrading.md, 'Moving to PostgreSQL 18')
EOT
}

if [ -e "$marker" ]; then
    say "the move of the PostgreSQL 16 database did not complete, so this database is not"
    say "started. The old database was not changed. To try again: docker compose down,"
    say "docker volume rm <project>_postgres18_data, docker compose up -d. The reason is in"
    say "the log of the attempt, above. Or move it with the script:"
    manual
    exit 1
fi

if [ ! -s "$data/PG_VERSION" ] && [ -s "$legacy/PG_VERSION" ] && [ "${RF_MIGRATION:-}" != "1" ]; then
    version="$(cat "$legacy/PG_VERSION")"
    if [ "${RF_AUTO_MIGRATE:-1}" = "0" ] || [ "$version" != "16" ]; then
        say "the database of an earlier version (PostgreSQL $version) is"
        say "still to be moved to PostgreSQL 18. Nothing was changed."
        manual
        exit 1
    fi
    need_kb="$(du -sk "$legacy" | cut -f1)"
    free_kb="$(df -Pk "$root" | awk 'NR == 2 {print $4}')"
    if [ "$free_kb" -lt $((need_kb * 3)) ]; then
        say "the PostgreSQL 16 database ($((need_kb / 1024)) MB) is to be moved to PostgreSQL 18, which needs"
        say "about $((need_kb * 3 / 1024)) MB free; there are $((free_kb / 1024)) MB. Nothing was changed."
        exit 1
    fi
    say "moving the PostgreSQL 16 database to PostgreSQL 18 - the old one is not changed."
    rm -rf "$copy"
    cp -a "$legacy" "$copy"
    rm -f "$copy/postmaster.pid"
    chown -R postgres:postgres "$copy"
    chmod 700 "$copy"
    export RF_AUTO_MIGRATION=1
fi
exec docker-entrypoint.sh "$@"
