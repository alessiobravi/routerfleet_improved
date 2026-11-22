# Users and permissions

Accounts are Django users with one extra record holding a **permission level**.
Levels are numeric and cumulative — a level reaches everything the levels below
it reach.

| Level | Name | Reaches |
|---|---|---|
| 10 | Viewer | Nothing beyond what any signed-in account sees (see below) |
| 20 | Backup Operator | Instant backups, download and compare backups, group membership, run/stop views in Fleet Commander listings, notification channel listings |
| 30 | Host Manager | Create and edit devices, bulk edit, refresh inventory, delete backups |
| 40 | Configuration Manager | Backup profiles, groups, SSH keys, commands, variants, schedules, schedule defaults, notification channels and settings |
| 50 | Administrator | User management, audit log, import tool |

Some pages check only that you are signed in, with no level at all: the
status page, the device list and device details, groups, SSH key
names, the backup list, backup profiles, the About page and *My
Settings*. An account with no level — or level 10 — sees exactly these.

## What each page requires

| Area | Action | Level |
|---|---|---|
| Devices | View | signed in |
| | Instant backup, group membership | 20 |
| | Create/edit a device, bulk edit, refresh inventory | 30 |
| | Groups, SSH keys | 40 |
| Backups | Backup list, profile list | signed in |
| | Details, compare, download | 20 |
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

## Second factors

Every user can turn on an authenticator app and add passkeys in **My
Settings → Sign-in security**, whatever their level. An administrator sees on
each user's page which second factors the user has, and can **Reset** them -
for a lost phone or passkey - which leaves the user signing in with their
password only. How the second factors work, and what passkeys need from the
installation, is in [Security](security.md#second-factors-and-passkeys).

