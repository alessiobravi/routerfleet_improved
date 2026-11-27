# Users and permissions

Accounts are Django users with one extra record holding a **permission level**.
Levels are numeric and cumulative — a level reaches everything the levels below
it reach.

| Level | Name | Reaches |
|---|---|---|
| 10 | Viewer | Nothing beyond what any signed-in account sees (see below) |
| 20 | Backup Operator | Instant backups, download and compare backups, group membership, run/stop views in Fleet Commander listings, notification channel listings |
| 30 | Host Manager | Create and edit devices, bulk edit, refresh inventory, test connection, delete backups, **device console** |
| 40 | Configuration Manager | Backup profiles, groups, SSH keys, commands, variants, schedules, schedule defaults, notification channels and settings |
| 50 | Administrator | User management, audit log, import tool |

Some pages check only that you are signed in, with no level at all: the
status page, the device list and device details, groups, SSH key
names, the backup list, backup profiles, the About page and *My
Settings*. A Viewer (level 10) sees exactly these; an account with no level
sees only *My Settings* (see [Accounts without an access level](#accounts-without-an-access-level)).

## What each page requires

| Area | Action | Level |
|---|---|---|
| Devices | View | signed in |
| | Instant backup, group membership | 20 |
| | Create/edit a device, bulk edit, refresh inventory, test connection | 30 |
| | [Device console](console.md) (SSH or Telnet) | 30 |
| | Groups | 40 |
| SSH Keys | View the key names | signed in |
| | Add, generate, edit or delete a key | 40 |
| | Show or replace a stored private key, change its passphrase | 50 |
| Backups | Backup list, profile list | signed in |
| | Details, compare, download | 20 |
| | Delete a backup | 30 |
| | Backup profiles | 40 |
| Fleet Commander | View commands, jobs, tasks | 20 |
| | Stop a job or task | 20 |
| | Run a command (always confirmed on a page showing the exact commands) | 40 |
| | Commands, variants, schedules, defaults | 40 |
| | Edit a system command (with confirmation) | 50 |
| Notifications | Message history | 20 |
| | Channels, grouping and daily reports, mail server (System settings → Media and messaging) | 50 |
| | Own email address and email notifications (My Settings) | signed in |
| Audit log | Login history, change log | 50 |
| Users | List, create, edit | 50 |
| Import tool | All of it | 50 |
| Tools | Backup clean-up, export of the latest backups | 50 |
| System settings | All of System settings - administrators only: Media and messaging, Devices, Display (default rows per table), Backups and jobs, Security | 50 |
| | [Firmware repository](firmware.md): upload, switch off, delete | 50 |
| Device Discovery | See discovered devices, hide them, discover adjacent devices, read chosen devices' neighbours | 30 |
| | Add a discovered device (the wizard, its groups included) | 50 |
| Devices | Forget a device's host key, or remove an unused one on the SSH Keys page (confirmed with a small sum) | 50 |
| Firmware updates | Start and follow updates of devices (the arrows in the device list and on the device page), abort a rollout | 40 |
| | [Device types](device-types.md): edit, create (wizard), try on a device, delete | 50 |
| | HTTPS, [HTTP access and banning](security.md#http-access-lists-and-banning), [Updates](updating.md) (the update check) | 50 |

## User details

A user has a username, a name and surname, an email address (for
notifications) and an optional phone number - edited in User Manager by an
administrator; the list shows them with the user's level and second factors.

## Accounts without an access level

An account made outside the User Manager (with `manage.py createsuperuser`,
say) has no access level ("No access level"): it sees no page but its own
settings, until an administrator gives it one. A superuser keeps its access.

## The first account

The first visit to a fresh installation offers to create the first user, which
is given level **50**. After that, accounts are created from the User Manager.

## Per-user settings

**My Settings** (the gear icon, top right) holds what a person sets for
themselves rather than permissions. At the top: their username, level and
email address. Below, in collapsible sections:

- **Security settings** — the authenticator app, their own password, passkeys
  (greyed out, with the reason, where the installation's HTTPS does not allow
  them) and the **session expiration time**: how long a sign-in lasts without
  use, 24 hours unless changed. See [Security](security.md#second-factors-and-passkeys).
- **Visualization options** — light, dark or automatic.
- **Device address links** — what clicking a device's address does: nothing,
  SSH, Winbox, or a hover menu with both. See
  [Devices](devices.md#address-links).
- **Email notifications** — the email address (also where a password reset
  link goes) and which notifications to receive; greyed out until an
  administrator has set up a working mail server.

The audit log records the password, the email address and second factors
changing; the address-link and session-length choices are deliberately left
out, since they grant nothing.

## Accounts in the audit log

Account changes *are* audited: username, names, email, active and superuser
flags, the password (as `(set)`/`(empty)`, never the value) and the permission
level. See [Audit log](audit-log.md).

## Second factors

Every user can turn on an authenticator app and add passkeys in **My
Settings → Security settings**, whatever their level. An administrator sees on
each user's page which second factors the user has, and can **Reset** them -
for a lost phone or passkey - which leaves the user signing in with their
password only. How the second factors work, and what passkeys need from the
installation, is in [Security](security.md#second-factors-and-passkeys).

