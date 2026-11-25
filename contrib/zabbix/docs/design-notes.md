# Design notes

Why this integration is built the way it is. Read this before changing a query
or a trigger: two of the decisions below look wrong until you know what they
are protecting against, and one of them describes a query that looks right and
can never report a failure.

## 1. Backup *age* is the authoritative signal, not backup *result*

RouterFleet does not backfill. If a scheduled backup does not complete, no row
with a `finish_time` is written, and nothing later fills the gap. So the time
since the last successful backup only stops growing when a backup genuinely
succeeds.

**This depends on a property of RouterFleet, so check it after an upgrade.** A
backup profile can keep only changes: a scheduled backup whose configuration
matches the one last stored has its *content* dropped and is linked to the
stored copy through `same_as`. The row itself is still written, with `success`,
`error` and `finish_time` set exactly as for any backup, so the age query below
is unaffected. RouterFleet documents this as a contract for integrations
([Backups](../../../docs/backups.md#a-contract-for-integrations)) and its test
suite enforces it.

It would not hold if a version skipped writing the row instead of blanking its
content: every device would begin reporting staleness within the alert window,
and the result would look precisely like a genuine fleet-wide backup failure. If
you upgrade RouterFleet and every host starts alarming at once, suspect this
before you suspect your fleet. The check is cheap:

```sql
-- an unchanged backup must still look like a success
SELECT success, error, finish_time IS NOT NULL AS has_finish_time
FROM backup_data_routerbackup WHERE same_as_id IS NOT NULL
ORDER BY id DESC LIMIT 5;
```

All three must be true, false and true. If they are not, the age item needs
revisiting before it is trusted. (`same_as_id` is not among the columns
`sql/zbx_monitor.sql` grants: run it as the owning role.)

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

The wider lesson is worth stating on its own, because this shape recurs well
beyond backups. **A check that cannot produce a negative result is
indistinguishable from a check that passes.** An update check that only
understands one registry and stays silent about everything it cannot judge; a
pending-update count computed from a stale package index, which honestly finds
nothing to install - both report "all well" precisely when they have stopped
looking.

The fix is the same in spirit every time: separate "there is nothing" from "I
could not tell", and make the second one visible.

## 3. Unknown is not healthy

When no successful backup is on record, the age item returns `999999999`
(about 31 years) rather than `0` or an empty value.

An empty value makes the item unsupported, which is easy to miss. A `0` would
read as "backed up just now" and actively suppress the alarm. A huge number
trips every staleness threshold immediately, which is the correct behaviour: a
device with no backup on record is the worst case, not the best one.

The counters follow the same rule from the other side. A count that cannot be
read - a grant that was never applied, a table this installation does not
have, a query that fails - is reported as `-1`, not `0`, and one trigger fires
on any counter at `-1`. Reporting `0` there would make the failing-sign-in and
missing-host-key counters most reassuring exactly when they have stopped
working.

The probe item is the exception: there, `0` genuinely means "could not reach
the database", which is the thing being measured.

## 4. The collector monitors itself

`routerfleet.probe` exists because a broken collector and a healthy fleet look
identical from the server: items simply stop changing, and triggers that test
`last()` keep returning the last good value indefinitely.

The probe is paired with a `nodata()` trigger so that both failure modes are
covered - the collector answering "broken", and the collector not answering at
all.

## 5. The database role is narrow on purpose

The monitoring role is granted `SELECT` on exactly the columns the collector
reads - column privileges, not table privileges. Every table it reads also holds
something a monitoring account must not see:

| Table | The collector reads | Also in the table, and not granted |
|---|---|---|
| `router_manager_router` | id, uuid, name, enabled, backup profile, address, port | the (encrypted) device password, username, notes |
| `backup_data_routerbackup` | id, router, success, error, finish time | the full text of every configuration backup, which routinely contains secrets (PPP and SNMP credentials, pre-shared keys) |
| `router_manager_sshhostkey` | address, port, first seen | the keys and fingerprints |
| `audit_log_loginrecord` | successful, created | user names, addresses, browsers, and the keys of open sessions - enough to take one over |
| `audit_log_changerecord` | created | what was changed, by whom, from where |
| `fleet_commander_commandjob` | completed, error and aborted counts | who ran what |

The rest of the database - SSH private keys, notification tokens, accounts and
their second factors, sessions - is not granted at all. Monitoring credentials
are widely distributed by nature: they live in agent configuration files on
every monitored host. A blanket `GRANT SELECT ON ALL TABLES` would hand them
all of the above.

If a future version of this integration needs another column, grant that column
explicitly.

## 6. Why UUIDs are validated in the collector

Device UUIDs arrive as Zabbix item-key parameters and are interpolated into
SQL. They are validated against a strict pattern before use, so the integration
works with `UnsafeUserParameters=0` - the safe default - and an operator who
can edit item keys still cannot reach the database with arbitrary SQL.

## 7. What the security items are for, and what they cost

RouterFleet Improved keeps an SSH host-key store and an audit log. Four of the
items in this template read them, and each is here for a reason that is worth
knowing before deciding whether to keep it.

**Host keys.** RouterFleet learns a device's key on first contact and refuses
to connect if it later changes. That makes two things visible that were not
before: a device it has never reached (no key recorded), and a key that was
forgotten and re-learned.

The second is the one that matters. A key change is normal after a deliberate
rebuild and alarming otherwise, and the two are indistinguishable from the
data alone - so the trigger fires on both and asks a person to say which it
was. That is a deliberate trade: it will fire legitimately on planned work.
The alternative is that the case worth catching stays silent, and by the time
anyone looks, credentials have already been sent to whatever answered.

**Failed sign-ins** are counted over fifteen minutes rather than alarmed on
individually, because people mistype passwords and a single failure means
nothing. RouterFleet locks an account or address after five failures in that
window, so a count that stays high means something is still working through
other accounts or other addresses - which is exactly the case the per-account
lockout does not cover.

**Audited changes** are collected and graphed but never alarmed. A change is
normally somebody doing their job. The value is that an unexplained burst is
visible afterwards without anyone having to go looking for it.

**Command jobs** are counted as failures only once completed, so a job still
running is not mistaken for a broken one.

A note on cost: counting sign-ins and changes needs nothing but their
timestamps (and, for sign-ins, whether each succeeded), so that is all the role
is granted. It cannot read who signed in, from where, or what a change was -
see note 5. Counting costs no privacy.
