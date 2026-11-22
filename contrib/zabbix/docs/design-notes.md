# Design notes

Why this integration is built the way it is. Read this before changing a query
or a trigger: two of the decisions below look wrong until you know what they
are protecting against, and one of them describes a query that looks right and
can never report a failure.

## 1. Backup *age* is the authoritative signal, not backup *result*

RouterFleet does not backfill or deduplicate. If a scheduled backup does not
complete, no row with a `finish_time` is written, and nothing later fills the
gap. So the time since the last successful backup only stops growing when a
backup genuinely succeeds.

That makes age a signal that cannot lie:

- it needs no interpretation of status flags;
- it survives schema changes to the result columns;
- it reports the thing that actually matters - *how far back is my newest
  usable configuration?* - rather than a per-attempt verdict.

The result item is still worth having, because it reacts within one scheduling
interval instead of waiting for the staleness window to elapse. But if the two
ever disagree, **age is right**.

## 2. The ordering bug: why `result` could never report a failure

A failed backup is recorded with `finish_time = NULL`. A natural way to find
the most recent attempt is:

```sql
-- WRONG
SELECT success, error FROM backup_data_routerbackup
WHERE router_id = r.id
ORDER BY finish_time DESC NULLS LAST
LIMIT 1
```

`NULLS LAST` sorts every failed attempt *behind* every successful one. The
query therefore returns the newest **success** no matter how many failures came
after it, and the item reports `1` forever. The trigger built on it can never
fire.

This follows from how RouterFleet writes backups (`finish_time` is set only
when a backup succeeds), so it holds on every installation:

```
every failed row     has finish_time IS NULL
every successful row has finish_time set

ORDER BY finish_time DESC NULLS LAST -> no router is ever reported as failing
ORDER BY id DESC                     -> the failing routers appear
```

A device can fail every night and nothing alarms, because the item it depends
on is structurally incapable of returning 0.

The fix is to order by the identity column, which reflects insertion order and
is never NULL:

```sql
-- RIGHT
SELECT success, error FROM backup_data_routerbackup
WHERE router_id = r.id
ORDER BY id DESC
LIMIT 1
```

The general lesson: **when a column is NULL precisely in the failure case, do
not sort by it to find failures.** Sort by something that exists for every row.

## 3. Unknown is not healthy

When no successful backup is on record, the age item returns `999999999`
(about 31 years) rather than `0` or an empty value.

An empty value makes the item unsupported, which is easy to miss. A `0` would
read as "backed up just now" and actively suppress the alarm. A huge number
trips every staleness threshold immediately, which is the correct behaviour: a
router with no backup on record is the worst case, not the best one.

## 4. The collector monitors itself

`routerfleet.probe` exists because a broken collector and a healthy fleet look
identical from the server: items simply stop changing, and triggers that test
`last()` keep returning the last good value indefinitely.

The probe is paired with a `nodata()` trigger so that both failure modes are
covered - the collector answering "broken", and the collector not answering at
all.

## 5. The database role is narrow on purpose

The monitoring role is granted `SELECT` on exactly the columns the collector
reads, in two tables - column privileges, not table privileges. The two tables
are not harmless as a whole: `router_manager_router` carries the encrypted
device passwords, and `backup_data_routerbackup` carries the full text of every
configuration backup, which routinely contains secrets of its own (PPP and
SNMP credentials, pre-shared keys). RouterFleet's
database also holds device passwords, SSH private keys and notification
tokens, in the same schema. They are encrypted at rest, but the database is
still no place for a monitoring account to browse: a blanket
`GRANT SELECT ON ALL TABLES` would hand it the encrypted credential store, the
user accounts and the audit trail, and monitoring credentials are widely
distributed by nature - they live in agent configuration files on every
monitored host.

If a future version of this integration needs another column, grant that
column explicitly.

## 6. Why UUIDs are validated in the collector

Router UUIDs arrive as Zabbix item-key parameters and are interpolated into
SQL. They are validated against a strict pattern before use, so the integration
works with `UnsafeUserParameters=0` - the safe default - and an operator who
can edit item keys still cannot reach the database with arbitrary SQL.
