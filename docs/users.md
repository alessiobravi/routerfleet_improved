# Users and permissions

Accounts are Django users with one extra record holding a **permission level**.
Levels are numeric and cumulative — a level reaches everything the levels below
it reach.

| Level | Name | Reaches |
|---|---|---|
| 10 | Viewer | Read-only access to devices, groups and backup listings |
| 20 | Backup Operator | Instant backups, download and compare backups, group membership, run/stop views in Fleet Commander listings, notification channel listings |
| 30 | Host Manager | Create and edit devices, bulk edit, refresh inventory, delete backups |
| 40 | Configuration Manager | Backup profiles, groups, SSH keys, commands, variants, schedules, schedule defaults, notification channels and settings |
| 50 | Administrator | User management, audit log, import tool, external integrations |

## What each page requires

| Area | Action | Level |
|---|---|---|
| Devices | View | 10 |
| | Instant backup, group membership | 20 |
| | Create/edit a device, bulk edit, refresh inventory | 30 |
| | Groups, SSH keys | 40 |
| Backups | View, compare, download | 20 |
| | Delete a backup | 30 |
| | Backup profiles | 40 |
| Fleet Commander | View commands, jobs, tasks | 20 |
| | Stop a job or task | 20 |
| | Run a command | 40 |
| | Commands, variants, schedules, defaults | 40 |
| Notifications | Channel list, message history | 20 |
| | Channels and settings | 40 |
| Audit log | Login history, change log | 50 |
| Users | List, create, edit | 50 |
| Import tool | All of it | 50 |
| Integrations | Configure | 50 |

## The first account

The first visit to a fresh installation offers to create the first user, which
is given level **50**. After that, accounts are created from the User Manager.

## Per-user settings

**My Settings** (the gear icon, top right) holds preferences a person sets for
themselves rather than permissions:

- **Address link behaviour** — what clicking a device's address does: nothing,
  SSH, Winbox, or a hover menu with both. See
  [Devices](devices.md#address-links).

This setting is deliberately excluded from the audit log, since it grants
nothing.

## Accounts in the audit log

Account changes *are* audited: username, names, email, active and superuser
flags, the password (as `(set)`/`(empty)`, never the value) and the permission
level. See [Audit log](audit-log.md).
