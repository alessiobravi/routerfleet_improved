-- Read-only database role for RouterFleet monitoring.
--
-- Run inside the RouterFleet PostgreSQL container, as the owning role:
--   docker exec -i routerfleet-postgres psql -U routerfleet -d routerfleet < zbx_monitor.sql
--
-- The role can read exactly the columns the collector uses and nothing else.
-- Column privileges, not table privileges: every table below also holds data a
-- monitoring account has no business reading - encrypted device passwords, the
-- full text of every configuration backup, user names, addresses, the keys of
-- open sessions, the recorded diffs of the audit log.

-- Use a strong password, or configure peer/trust for a local socket only.
-- Replace the placeholder before running.
CREATE ROLE zbx_monitor WITH LOGIN PASSWORD 'CHANGE_ME';

-- No database-wide rights.
REVOKE ALL ON DATABASE routerfleet FROM zbx_monitor;
GRANT CONNECT ON DATABASE routerfleet TO zbx_monitor;

REVOKE ALL ON SCHEMA public FROM zbx_monitor;
GRANT USAGE ON SCHEMA public TO zbx_monitor;

-- Backup health: which routers, and whether and when their backups succeeded.
-- Not granted: password, username, internal notes; backup_text, the binary,
-- console output - a configuration backup routinely contains secrets.
GRANT SELECT (id, uuid, name, enabled, backup_profile_id, address, port)
    ON router_manager_router TO zbx_monitor;
GRANT SELECT (id, router_id, success, error, finish_time)
    ON backup_data_routerbackup TO zbx_monitor;

-- Host keys: which address and port has one, and since when.
-- Not granted: the keys and fingerprints themselves.
GRANT SELECT (address, port, first_seen)
    ON router_manager_sshhostkey TO zbx_monitor;

-- Sign-ins: whether each attempt succeeded, and when.
-- Not granted: username, address, browser, session_key - the last one would
-- let whoever holds the monitoring password take over an open session.
GRANT SELECT (successful, created)
    ON audit_log_loginrecord TO zbx_monitor;

-- Changes: when each was recorded.
-- Not granted: what was changed (the diff), by whom, from where.
GRANT SELECT (created)
    ON audit_log_changerecord TO zbx_monitor;

-- Command jobs: when each completed, and how many of its tasks failed.
GRANT SELECT (completed, error_count, aborted_count)
    ON fleet_commander_commandjob TO zbx_monitor;

-- Deliberately not granted at all: router_manager_sshkey (private keys),
-- message_center_* (notification tokens), import_tool_*, auth_user,
-- user_manager_* (accounts and second factors), django_session.
-- A blanket "GRANT SELECT ON ALL TABLES" would hand the monitoring role all of
-- that. Do not do it.

-- Confirm the grant set (column privileges are listed per column):
--   \dp router_manager_router
--   \dp audit_log_loginrecord
