#!/usr/bin/env bash
# Moves the bundled PostgreSQL 16 database of RouterFleet to PostgreSQL 18.
#
# Run it once, on the host, in the folder of the compose file, after taking the
# compose file of a release that uses PostgreSQL 18:
#
#     ./scripts/migrate-database.sh            # docker-compose.yml
#     ./scripts/migrate-database.sh -f FILE    # another compose file
#
# What it does - stopping at the first problem, never touching the old data:
#   1. checks the old database (the postgres_data volume) is PostgreSQL 16
#      and the new one (postgres18_data) is still empty;
#   2. stops RouterFleet;
#   3. starts the old database on its own, counts the rows of every table and
#      exports it (pg_dump) and its other roles (pg_dumpall --roles-only) to
#      files next to the compose file;
#   4. creates the new database - PostgreSQL 18, built-in C.UTF-8 ordering -
#      on the new volume, creates the roles and imports the export into it;
#   5. counts the rows of every table again and compares;
#   6. starts RouterFleet on the new database.
#
# The old volume is left exactly as it was: the way back is the previous
# compose file and `docker compose up -d`. Once the new database has served
# you well, remove the legacy line from the compose file and the old volume
# (the script prints how).
set -euo pipefail

COMPOSE=(docker compose)
while getopts 'f:h' option; do
    case "$option" in
        f) COMPOSE+=(-f "$OPTARG") ;;
        *) sed -n '2,25p' "$0"; exit 2 ;;
    esac
done

LEGACY_IMAGE="${RF_LEGACY_IMAGE:-ghcr.io/alessiobravi/routerfleet-postgres:latest}"
OLD_NAME=rf-migrate-pg16
NEW_NAME=rf-migrate-pg18
APP_SERVICES=(routerfleet routerfleet-cron routerfleet-monitoring routerfleet-nginx)

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nMIGRATION STOPPED: %s\nNothing was changed in the old database.\n' "$*" >&2; exit 1; }

cleanup() {
    docker rm -f "$OLD_NAME" "$NEW_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

[ -f .env ] || fail "run this in the folder of the compose file (no .env here)"
setting() { sed -n "s/^$1=//p" .env | tail -1 | sed 's/^"\(.*\)"$/\1/'; }
DB_USER="$(setting POSTGRES_USER)"
DB_NAME="$(setting POSTGRES_DB)"
DB_PASSWORD="$(setting POSTGRES_PASSWORD)"
[ -n "$DB_USER" ] && [ -n "$DB_NAME" ] && [ -n "$DB_PASSWORD" ] || fail "POSTGRES_USER, POSTGRES_DB and POSTGRES_PASSWORD must be set in .env"

PROJECT="$("${COMPOSE[@]}" config --format json | grep -o '"name": *"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
[ -n "$PROJECT" ] || fail "could not read the compose project name"
OLD_VOLUME="${PROJECT}_postgres_data"
NEW_VOLUME="${PROJECT}_postgres18_data"
"${COMPOSE[@]}" config --volumes | grep -qx postgres18_data \
    || fail "the compose file does not use PostgreSQL 18 yet (no postgres18_data volume): take the current one first"

say "1. Checking the databases"
docker volume inspect "$OLD_VOLUME" >/dev/null 2>&1 || fail "no old database: volume $OLD_VOLUME does not exist (a new installation needs no migration)"
OLD_VERSION="$(docker run --rm -v "$OLD_VOLUME":/old:ro --entrypoint cat "$LEGACY_IMAGE" /old/PG_VERSION 2>/dev/null || true)"
[ "$OLD_VERSION" = "16" ] || fail "the old database is not PostgreSQL 16 (found '${OLD_VERSION:-nothing}')"
if docker volume inspect "$NEW_VOLUME" >/dev/null 2>&1; then
    NEW_VERSION="$(docker run --rm -v "$NEW_VOLUME":/new:ro --entrypoint sh "$LEGACY_IMAGE" -c 'cat /new/18/docker/PG_VERSION 2>/dev/null' || true)"
    [ -z "$NEW_VERSION" ] || fail "the new database ($NEW_VOLUME) is not empty: already migrated? To start over, remove that volume first"
fi
echo "Old: PostgreSQL 16 in $OLD_VOLUME. New: PostgreSQL 18 in $NEW_VOLUME (empty)."

say "2. Stopping RouterFleet"
"${COMPOSE[@]}" stop "${APP_SERVICES[@]}" routerfleet-postgres >/dev/null 2>&1 || true
docker stop routerfleet-postgres >/dev/null 2>&1 || true

wait_ready() {
    local name="$1" tries=0
    until docker exec "$name" pg_isready -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; do
        tries=$((tries + 1))
        [ "$tries" -lt 90 ] || fail "$name did not become ready (docker logs $name)"
        sleep 2
    done
}

COUNT_SQL="SELECT table_name, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
counts() { docker exec "$1" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -AtF ' ' -c "$COUNT_SQL"; }

say "3. Exporting the old database"
docker run -d --name "$OLD_NAME" -e POSTGRES_PASSWORD="$DB_PASSWORD" -e POSTGRES_USER="$DB_USER" -e POSTGRES_DB="$DB_NAME" \
    -v "$OLD_VOLUME":/var/lib/postgresql/data "$LEGACY_IMAGE" >/dev/null
wait_ready "$OLD_NAME"
OLD_COUNTS="$(counts "$OLD_NAME")"
[ -n "$OLD_COUNTS" ] || fail "the old database has no tables"
DUMP="routerfleet-pg16-$(date +%Y%m%d-%H%M%S).dump"
( umask 077; docker exec "$OLD_NAME" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$DUMP" )
[ -s "$DUMP" ] || fail "the export is empty"
# Roles live beside the database, not in it: a monitoring role (the Zabbix
# kit's zbx_monitor, for example) and its memberships, with their password
# hashes. The main role exists in the new database already and is left out.
ROLES="${DUMP%.dump}.roles.sql"
( umask 077; docker exec "$OLD_NAME" pg_dumpall -U "$DB_USER" --roles-only \
    | grep -v -E "^(CREATE|ALTER) ROLE \"?${DB_USER}\"?( |;)" > "$ROLES" ) || fail "the roles could not be exported"
ROLE_SQL="SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' AND rolname <> '$DB_USER' ORDER BY rolname"
OLD_ROLES="$(docker exec "$OLD_NAME" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -Atc "$ROLE_SQL")"
docker rm -f "$OLD_NAME" >/dev/null
echo "$(printf '%s\n' "$OLD_COUNTS" | wc -l | tr -d ' ') tables exported to $DUMP ($(du -h "$DUMP" | cut -f1))."
echo "Other roles: ${OLD_ROLES:-none}" | tr '\n' ' '; echo

say "4. Creating the new database and importing"
"${COMPOSE[@]}" run -d --no-deps --name "$NEW_NAME" -e RF_MIGRATION=1 routerfleet-postgres >/dev/null
wait_ready "$NEW_NAME"
docker exec -i "$NEW_NAME" psql -q -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" < "$ROLES" >/dev/null \
    || fail "the roles could not be created; the new volume $NEW_VOLUME should be removed before trying again"
NEW_ROLES="$(docker exec "$NEW_NAME" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -Atc "$ROLE_SQL")"
[ "$OLD_ROLES" = "$NEW_ROLES" ] || fail "the roles differ: old '$OLD_ROLES', new '$NEW_ROLES'"
docker exec -i "$NEW_NAME" pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner --exit-on-error < "$DUMP" \
    || fail "the import failed; the new volume $NEW_VOLUME holds a partial copy - remove it before trying again"
PROVIDER="$(docker exec "$NEW_NAME" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -Atc "SELECT datlocprovider FROM pg_database WHERE datname = current_database()")"
[ "$PROVIDER" = "b" ] || fail "the new database does not use the built-in ordering (provider '$PROVIDER')"

say "5. Comparing every table"
NEW_COUNTS="$(counts "$NEW_NAME")"
if [ "$OLD_COUNTS" != "$NEW_COUNTS" ]; then
    diff <(printf '%s\n' "$OLD_COUNTS") <(printf '%s\n' "$NEW_COUNTS") || true
    fail "the row counts differ (above); the new volume $NEW_VOLUME should be removed before trying again"
fi
echo "$(printf '%s\n' "$NEW_COUNTS" | wc -l | tr -d ' ') tables, every row count identical."
docker rm -f "$NEW_NAME" >/dev/null

say "6. Starting RouterFleet on PostgreSQL 18"
"${COMPOSE[@]}" up -d
cat <<EOT

Done. RouterFleet runs on PostgreSQL 18.

Kept, untouched: the old database (volume $OLD_VOLUME), the export $DUMP and
the roles $ROLES (password hashes of other roles - delete it with the export).
The way back: the previous compose file, then docker compose up -d.

When the new database has served you well:
  - remove the line "- postgres_data:/var/lib/postgresql-legacy:ro" from the compose file
    and "postgres_data:" from its volumes, then: docker compose up -d
  - docker volume rm $OLD_VOLUME
  - delete $DUMP (it holds the whole database, passwords encrypted)
EOT
