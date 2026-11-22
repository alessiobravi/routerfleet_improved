-- Read-only database role for RouterFleet monitoring.
--
-- Run inside the RouterFleet PostgreSQL container, as the owning role:
--   docker exec -i routerfleet-postgres psql -U routerfleet -d routerfleet < zbx_monitor.sql
--
-- The monitoring role can read the two tables the collector needs and nothing
-- else. It cannot write, cannot read device passwords or SSH keys, and cannot
-- see future tables unless they are granted explicitly.

-- Use a strong password, or configure peer/trust for a local socket only.
-- Replace the placeholder before running.
CREATE ROLE zbx_monitor WITH LOGIN PASSWORD 'CHANGE_ME';

-- No database-wide rights.
REVOKE ALL ON DATABASE routerfleet FROM zbx_monitor;
GRANT CONNECT ON DATABASE routerfleet TO zbx_monitor;

REVOKE ALL ON SCHEMA public FROM zbx_monitor;
GRANT USAGE ON SCHEMA public TO zbx_monitor;

-- Exactly the columns the collector reads, in the two tables it reads. Column
-- privileges, not table privileges: router_manager_router also holds the
-- (encrypted) device passwords, and backup_data_routerbackup holds the full
-- text of every configuration backup - which often contains secrets of its own.
GRANT SELECT (id, uuid, name, enabled, backup_profile_id)
    ON router_manager_router TO zbx_monitor;
GRANT SELECT (id, router_id, success, error, finish_time)
    ON backup_data_routerbackup TO zbx_monitor;

-- Deliberately NOT granted: anything holding secrets or people.
--   router_manager_sshkey, router_manager_sshhostkey, message_center_*,
--   import_tool_*, auth_user, user_manager_*, audit_log_*
-- Adding a blanket "GRANT SELECT ON ALL TABLES" would hand the monitoring
-- role the encrypted credential store. Do not do it.

-- Confirm the grant set (column privileges are listed per column):
--   \dp router_manager_router
--   \dp backup_data_routerbackup
