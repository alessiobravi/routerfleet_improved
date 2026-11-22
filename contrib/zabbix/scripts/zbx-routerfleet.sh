#!/bin/bash
# RouterFleet monitoring collector for the Zabbix agent.
#
# Reads the RouterFleet PostgreSQL database through a read-only role and prints
# one value per invocation, in the form the Zabbix agent expects.
#
# Invoked by UserParameters - see agent/routerfleet.conf.
#
#   zbx-routerfleet.sh discovery        -> LLD JSON of monitored routers
#   zbx-routerfleet.sh age   <uuid>     -> seconds since the last GOOD backup
#   zbx-routerfleet.sh result <uuid>    -> 1 if the latest ATTEMPT succeeded, else 0
#   zbx-routerfleet.sh probe            -> 1 if the database answered, else 0
#
# Every value is a number. The script never returns an empty string: an
# unreachable database yields a sentinel, so "no data" is distinguishable from
# "bad data" and the probe item can alarm on the collector itself.

set -uo pipefail

# --- configuration -----------------------------------------------------------
# Override in /etc/default/zbx-routerfleet if your deployment differs.
RF_CONTAINER="${RF_CONTAINER:-routerfleet-postgres}"
RF_DB="${RF_DB:-routerfleet}"
RF_USER="${RF_USER:-zbx_monitor}"
RF_TIMEOUT="${RF_TIMEOUT:-10}"
[ -r /etc/default/zbx-routerfleet ] && . /etc/default/zbx-routerfleet

# Sentinel returned when the last good backup is unknown or has never happened.
# Deliberately enormous (~31 years) so staleness triggers fire rather than stay
# silent: an unknown backup age is a problem, not an absence of one.
NEVER=999999999

psql_q() {
  timeout "$RF_TIMEOUT" docker exec -i "$RF_CONTAINER" \
    psql -U "$RF_USER" -d "$RF_DB" -At -c "$1" 2>/dev/null
}

valid_uuid() { [[ "${1:-}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; }

case "${1:-}" in

  discovery)
    # Enabled routers that have a backup profile assigned. A router with no
    # profile is not supposed to be backed up, so discovering it would create
    # an item that is permanently stale.
    rows=$(psql_q "SELECT uuid::text || '|' || name
                   FROM router_manager_router
                   WHERE enabled AND backup_profile_id IS NOT NULL
                   ORDER BY name;") || rows=""
    printf '{"data":['
    first=1
    while IFS='|' read -r uuid name; do
      [ -z "$uuid" ] && continue
      [ $first -eq 1 ] || printf ','
      first=0
      jname=$(printf '%s' "$name" | sed 's/\\/\\\\/g; s/"/\\"/g')
      printf '{"{#ROUTER}":"%s","{#UUID}":"%s"}' "$jname" "$uuid"
    done <<< "$rows"
    printf ']}\n'
    ;;

  age)
    # Seconds since the last backup that actually SUCCEEDED.
    #
    # This is the authoritative health signal. RouterFleet does not deduplicate
    # or backfill: if a nightly backup fails, no row with a finish_time appears,
    # so this value simply keeps growing. A rising age IS a failing backup,
    # whatever the result item says.
    valid_uuid "${2:-}" || { echo "$NEVER"; exit 0; }
    val=$(psql_q "SELECT COALESCE(EXTRACT(EPOCH FROM (now() - b.finish_time))::bigint, $NEVER)
                  FROM router_manager_router r
                  LEFT JOIN LATERAL (
                    SELECT finish_time FROM backup_data_routerbackup
                    WHERE router_id = r.id AND success AND NOT error
                      AND finish_time IS NOT NULL
                    ORDER BY finish_time DESC LIMIT 1) b ON TRUE
                  WHERE r.uuid = '$2';")
    echo "${val:-$NEVER}"
    ;;

  result)
    # Did the most recent backup ATTEMPT succeed?
    #
    # Ordering is the whole correctness story here - see docs/design-notes.md.
    # A failed attempt is stored with finish_time = NULL. Ordering by
    # "finish_time DESC NULLS LAST" therefore sorts every failure behind every
    # success, so the newest SUCCESS is always selected and this item can never
    # report 0. Order by the identity column instead: it reflects insertion
    # order, so it genuinely returns the latest attempt.
    valid_uuid "${2:-}" || { echo 0; exit 0; }
    val=$(psql_q "SELECT CASE WHEN b.success AND NOT b.error THEN 1 ELSE 0 END
                  FROM router_manager_router r
                  JOIN LATERAL (
                    SELECT success, error FROM backup_data_routerbackup
                    WHERE router_id = r.id
                    ORDER BY id DESC LIMIT 1) b ON TRUE
                  WHERE r.uuid = '$2';")
    echo "${val:-0}"
    ;;

  probe)
    # Collector health. Without this, a broken collector looks exactly like a
    # healthy fleet: items stop updating and nothing alarms.
    val=$(psql_q "SELECT 1;")
    echo "${val:-0}"
    ;;

  routers)
    val=$(psql_q "SELECT count(*) FROM router_manager_router WHERE enabled AND backup_profile_id IS NOT NULL;")
    echo "${val:-0}"
    ;;

  failing)
    # Fleet-wide count of routers whose latest attempt failed.
    val=$(psql_q "WITH cur AS (
                    SELECT r.id,
                      (SELECT CASE WHEN success AND NOT error THEN 1 ELSE 0 END
                       FROM backup_data_routerbackup WHERE router_id = r.id
                       ORDER BY id DESC LIMIT 1) AS ok
                    FROM router_manager_router r
                    WHERE r.enabled AND r.backup_profile_id IS NOT NULL)
                  SELECT count(*) FILTER (WHERE ok = 0) FROM cur;")
    echo "${val:-0}"
    ;;

  *)
    echo "usage: $0 {discovery|age <uuid>|result <uuid>|probe|routers|failing}" >&2
    exit 2
    ;;
esac
