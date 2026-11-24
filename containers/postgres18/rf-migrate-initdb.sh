#!/bin/sh
# Run by the official entrypoint while the new database is created (as the
# postgres user, the new server running on its local socket). With a copy of a
# PostgreSQL 16 database prepared by rf-guard.sh, it moves it in: PostgreSQL 16
# on the copy, the row count of every table, the other roles (with their
# password hashes), export, import, every count compared. Any difference stops
# here - the marker stays, and the database refuses to start. On success the
# working copy is removed; the old volume was never changed.
[ "${RF_AUTO_MIGRATION:-}" = "1" ] || exit 0
set -eu
root=/var/lib/postgresql
copy=$root/rf-pg16-copy
marker=$root/rf-migration-incomplete
dump=$root/rf-migration.dump
roles=$root/rf-migration-roles.sql
pg16="$(cat /usr/local/share/rf-pg16-bin)"
user="$POSTGRES_USER"
# Local connections may ask for the password while the database is created
export PGPASSWORD="${POSTGRES_PASSWORD:-}"
db="${POSTGRES_DB:-$POSTGRES_USER}"

say() { echo "RouterFleet: $*" >&2; }
stop_old() { "$pg16/pg_ctl" -D "$copy" -m fast -w stop >/dev/null 2>&1 || true; }
fail() { say "MOVE STOPPED: $*"; say "The old database was not changed."; stop_old; exit 1; }

touch "$marker"
# Locale-neutral messages and formats: the files come from another C library
"$pg16/pg_ctl" -D "$copy" -l /tmp/rf-pg16.log -w -t 600 \
    -o "-p 5499 -k /tmp -c listen_addresses='' -c lc_messages=C -c lc_monetary=C -c lc_numeric=C -c lc_time=C" start \
    >/dev/null || { cat /tmp/rf-pg16.log >&2; fail "PostgreSQL 16 did not start on the copy of the old database"; }
trap stop_old EXIT

old() { psql -X -q -v ON_ERROR_STOP=1 -h /tmp -p 5499 -U "$user" -d "$db" "$@"; }
new() { psql -X -q -v ON_ERROR_STOP=1 -h /var/run/postgresql -U "$user" -d "$db" "$@"; }
COUNT_SQL="SELECT table_name, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name COLLATE \"C\""
ROLE_SQL="SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' AND rolname <> '$user' ORDER BY rolname COLLATE \"C\""

old_counts="$(old -AtF ' ' -c "$COUNT_SQL")" || fail "the old database could not be read"
[ -n "$old_counts" ] || fail "the old database has no tables"
old_roles="$(old -At -c "$ROLE_SQL")"
pg_dumpall -h /tmp -p 5499 -U "$user" --roles-only \
    | grep -v -E "^(CREATE|ALTER) ROLE \"?${user}\"?( |;)" > "$roles" || fail "the roles could not be exported"
pg_dump -h /tmp -p 5499 -U "$user" -d "$db" -Fc -f "$dump" || fail "the export failed"
tables="$(printf '%s\n' "$old_counts" | wc -l | tr -d ' ')"
say "exported $tables tables; importing into PostgreSQL 18."

new -f "$roles" >/dev/null || fail "the roles could not be created"
pg_restore -h /var/run/postgresql -U "$user" -d "$db" --no-owner --exit-on-error "$dump" || fail "the import failed"
new_counts="$(new -AtF ' ' -c "$COUNT_SQL")"
[ "$old_counts" = "$new_counts" ] || {
    printf 'old:\n%s\nnew:\n%s\n' "$old_counts" "$new_counts" >&2
    fail "the row counts differ"
}
[ "$old_roles" = "$(new -At -c "$ROLE_SQL")" ] || fail "the roles differ"
[ "$(new -At -c "SELECT datlocprovider FROM pg_database WHERE datname = current_database()")" = b ] \
    || fail "the new database does not use the built-in ordering"

stop_old
trap - EXIT
rm -rf "$copy" "$dump" "$roles" /tmp/rf-pg16.log
rm -f "$marker"
say "moved: $tables tables, every row count identical. The old volume is kept as it was."
