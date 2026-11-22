# Changelog

Releases are numbered MAJOR.MINOR.PATCH. Every push to `main` is a release: a
push that fixes or maintains raises PATCH (0.11.5 → 0.11.6), a release that
brings a new feature raises MINOR (→ 0.12.0). Each release is tagged
`v<version>`, published as a GitHub Release, and its Docker images are tagged
with it. The release is `ROUTERFLEET_RELEASE` in `routerfleet/settings.py`;
`scripts/version.py bump patch|minor` raises it.

Only the newest release is published: each release deletes the older ones,
with their tags and images. Use `:latest`.

A release that changes how backup rows are written, or the database columns
the Zabbix kit reads, says so under **Monitoring**.

## [0.20.0] - 2026-11-22

### Added
- **System settings in sections**: Messaging, Display, Backups and jobs,
  Security - each setting of the installation in one place.
- **Default rows per table** (Display): 10, 25, 50 or 100 - what every list
  opens with, for everyone. Upgraded installations keep 10, as before; each
  list still offers the other sizes.

### Fixed
- The change log now records a channel's ntfy server, priority and the
  newer events (backup succeeded, Fleet Commander job with failures) when
  they change.

## [0.19.0] - 2026-11-22

### Added
- **Console: a way back when the connection closes.** *Connection closed*
  appears over the terminal with the reason, a button back to the devices list
  and one to the device; the output can be kept on screen to read or copy.
- **Devices remember when they were enabled or disabled**, on every save,
  single or bulk. Upgrading fills it in from the change log; a device already
  disabled with no record counts as off since its last change.

### Fixed
- **The devices chart showed wrong figures.** Online counted only devices whose
  status had changed before that day, so the line climbed as devices merely
  changed state; a device that changed today vanished from earlier days; and
  disabled devices were dated from change-log entries a bulk edit does not
  write per device, so a bulk disable appeared all on one day. Each day now
  counts each device once, from the day it was added: disabled from when it was
  switched off, offline during the offline periods the monitoring recorded,
  online otherwise.
- **Dark mode: command payloads and output were unreadable** (dark text on a
  dark background), as were other blocks styled for a light page.

## [0.18.1] - 2026-11-22

### Security
- **The old channel address carries over only a valid identifier**: the
  redirect from Message Center to System Settings passed the `uuid` value on
  as received; it now passes it only as a parsed UUID. Reported by CodeQL.

## [0.18.0] - 2026-11-22

### Changed
- **Notifications are configured in System Settings.** System Settings opens
  with HTTPS and Notifications; Notifications holds the mail server, the
  channels - email, ntfy, Telegram - each in its own editor, grouping and the
  daily reports, and the way to the message history, which stays in Message
  Center. The old channel and email pages lead there.
- **The channel editor explains each setting** for each type, with where the
  value comes from and realistic examples. A stored token is never shown again;
  leaving it empty keeps it.

### Added
- **ntfy with your own server**: server URL (the public ntfy.sh by default),
  an optional access token for protected topics - encrypted, and only ever sent
  over HTTPS - and a priority from 1 to 5. A server on RouterFleet's own
  internal networks is refused. Existing ntfy channels keep ntfy.sh.

### Removed
- **CallMeBot (WhatsApp) is no longer supported.** Upgrading switches existing
  CallMeBot channels off and keeps them, with their message history; System
  Settings lists them so they can be recreated as email, ntfy or Telegram.
  Messages still queued for them fail with that reason.

## [0.17.1] - 2026-11-22

### Added
- **Device console: a terminal on any device, in the browser.** SSH - the
  device's stored host key checked first - or Telnet where a device has nothing
  else, with the device's backup account (its secret never reaches the
  browser) or another account (the password goes once, encrypted, to the
  device login; never stored or logged). On a phone, with a bar of Esc, Tab,
  Ctrl-C, Ctrl-D and arrows. For Host Manager and above, from the device page
  or the device list. Security: a one-time ticket valid for 60 seconds, never
  in a URL and stored only as a hash; WebSockets refused from other sites and
  without a signed-in session; the level checked again every few seconds;
  addresses on RouterFleet's own internal networks, loopback and link-local
  refused, so a device address can not reach the database or the application;
  Telnet off per device until switched on, and no stored credential ever sent
  over it; one console per device, three per user. It closes at once when the
  page is closed or left, with Disconnect, or when the device ends it; within
  seconds of signing out; within about 40 seconds of a lost browser; after 15
  minutes idle or 4 hours. Each session is in the change log - who, the device,
  the account and when, never what was typed - and in the device's timeline.
  Served by uvicorn beside gunicorn inside the application container; nginx
  passes `/console/ws/` in every HTTPS mode. CI drives a real session to an
  SSH server through nginx on every push.
- **Email notifications.** Each user sets their address in My Settings and
  chooses what to receive: backup failed, backup succeeded (grouped), the daily
  backup and status reports, device offline and back online, and Fleet
  Commander jobs with failures. The mail server - host, port, STARTTLS, SSL/TLS
  or none, an optional login (the password encrypted, never shown again),
  sender address and name, a test email - is set by administrators in Message
  Center, Email. A shared mailbox is a channel of type Email. Administrators can
  set users' addresses in user management.
- **Two new notification events for every channel type**: backup succeeded
  (grouped like failures) and Fleet Commander job with failures. **Off for
  channels that existed**, so an upgrade sends nothing new.
- **Light and dark mode**: the moon in the top bar, or Automatic (the default,
  following the device), Light or Dark in My Settings; drawn in its theme from
  the start, without a white flash.

### Changed
- **Object pages**: the timeline is one vertical column beside the details,
  kept in view while scrolling; the detail panels start collapsed and stay as
  opened, also through live updates; events of the same minute keep their
  order.
- **The Live pill names what is running** ("Live: job running"); the status
  page refreshes quietly, without it.
- **My Settings in the style of a phone's settings**: grouped rows, the router
  address choice saved on tap, passkeys as rows, appearance and email
  notifications.
- **Devices status is a line graph again.**
- **The device form** has *Allow a Telnet console (unencrypted)* and the Telnet
  port.

### Fixed
- **"configuration Manager" is "Configuration Manager".**
- **The menu's icons and labels are aligned** (Backup's label sat further left).

### Monitoring
- No change to how backup rows are written. The application container also
  listens on port 8002 internally, for the console; nothing new is published.

## [0.16.3] - 2026-11-22

### Security
- **Files written to the certificate volume are readable by their owner
  only** (0600): the HTTPS settings and an uploaded certificate's chain, like
  its private key already was. Reported by CodeQL.

## [0.16.2] - 2026-11-22

### Added
- **System settings → HTTPS**, for administrators: a Let's Encrypt
  certificate obtained and renewed automatically (HTTP-01), a certificate you
  provide (checked before use: format, matching key, expiry, domain, trusted by
  browsers or not), a generated self-signed certificate, or HTTPS off - behind
  a reverse proxy that serves HTTPS, or plain HTTP. The page shows what nginx
  applied, the certificate's expiry and whether passkeys can work (HTTPS, a
  domain name, a trusted certificate), and warns before a change that would
  stop them for users who have one. Switching HTTPS off takes a red button and
  a sum to solve in a popup. nginx renders its configuration from fixed
  templates, tests it and reloads; a configuration it refuses is never used.
  The private key of an uploaded certificate never reaches the database.
  `manage.py https reset` and `HTTPS_MODE_OVERRIDE` in `.env` are the way back
  if a change locks you out. **On upgrade nothing changes**: HTTPS follows
  `HTTPS_REDIRECT_POLICY`, and the certificate already deployed stays.

### Changed
- **Object pages in the "Hero & timeline" layout**: a Fleet Commander job, a
  task, a device and a backup open with a hero - a progress ring (job
  progress, device availability over 7 days, backup phase) or a state icon,
  the name, a state chip, key facts and the actions - four tiles (a job:
  succeeded, running, waiting, failed; a device: last backup, failed backups,
  OS version, downtime; a backup: size, binary, change, retries), and a
  timeline of what happened. The details follow below. They update live.
- **"Devices online" is "Devices status"**: stacked by day - online (green),
  offline but enabled (red), disabled (purple, from the day the change log says
  the device was switched off). A part opens those devices.
- **On/off settings are iPhone-style switches** throughout the interface.
- **The logo on the project's GitHub page is 30% larger** (125 pixels, was 96).
- **A new sign-in page**: a central box on a dark navy background, in the
  colours of the logo, with the logo, the name and a short tagline at the top
  inside the box, and "RouterFleet Improved by Alessio Bravi" beneath it. The
  second-factor, first-administrator and signed-out pages share it. Fields
  carry labels for screen readers and the browser's autocomplete hints, the
  username field is focused on arrival, and the box fits a phone screen.
- **Notifications are a balloon at the top centre of the page**, gone after 5
  seconds - a bar shows the time left, and pauses while the pointer rests on
  it - or on a click. Several stack. They show text only: a device name in a
  notification can no longer insert markup, which the side toasts allowed.

- **Pages update themselves while something on them is running**: a
  Fleet Commander job (with a progress bar: tasks finished out of the total,
  and how many failed) and its tasks, the job list, a backup and the backup
  list, and a device's page while a backup, a command or an information read is
  under way. A "Live" pill shows while it updates; it stops by itself once
  everything has finished, pauses while the tab is hidden, never replaces a
  field being typed in, and stops on an expired session. The status page's
  cards refresh every 30 seconds.

### Monitoring
- No change to how backup rows are written. The compose files now also mount
  the certificate volume in the application container.

### Fixed
- **Two menu entries lit up at once** on a page whose address contains another
  section's name (a device's command page showed Router Manager and Fleet
  Commander). The menu now goes by the start of the address.
- **Saving a router said "it may take a few minutes until monitoring starts"
  whatever was changed**, also when monitoring had just been switched off. The
  message now says what the save means: monitoring off, router disabled,
  monitoring starting, or simply saved.
- **Error messages showed without their red colour** (Django's `error` tag had
  no matching style).

## [0.15.3] - 2026-11-22

### Fixed
- Wording of the 0.15.2 entry and of a test, which named the earlier projects
  outside the places the project's attribution policy allows.

## [0.15.2] - 2026-11-22

### Fixed
- **The upgrade guide's migration counts** for the two earlier projects it
  covers were each two short (11 and 24, not 9 and 22): the two
  system-command migrations of 0.15.0 were missing. Upgrades themselves
  were unaffected.

### Added
- **The upgrade rule is enforced by the test suite**: every released migration -
  all of the predecessors' among them - is pinned by its SHA-256, and a
  migration that is edited, renamed or removed fails the build. The counts in
  the upgrade guide are computed from the real migration plan and checked.

## [0.15.1] - 2026-11-22

### Monitoring
- **Backup rows are a documented contract for integrations** (Backups, "A
  contract for integrations"): every attempt is a row; a backup that kept only
  changes is still a success with its finish time; a failed attempt has no
  finish time; the latest attempt is the highest `id`. Tests enforce it. Future
  changes to it, or to the columns the Zabbix kit reads, are announced under
  this heading. Nothing in how rows are written changed in this release.

### Changed
- **Zabbix kit**: explains why keeping only changes leaves the backup-age
  signal intact, with a query to confirm it after an upgrade; recommends a
  `sudoers` rule scoped to the collector instead of `docker` group membership;
  documents what `-1` and the probe's `0` mean. The column-level grants and the
  `-1` handling stay as they were.

## [0.15.0] - 2026-11-22

### Added
- **System commands.** Everything RouterFleet itself sends to devices - the
  connection test, the inventory and versions read, and each step of a backup
  (create, files to download, remove from the device, clean leftovers) - is a
  Fleet Commander command now, tagged **System**, with one variant per device
  type. Their defaults are exactly what the code sent before. They can not be
  deleted, disabled, scheduled or run by hand. An administrator can edit a
  variant after reviewing the change line by line and confirming it with a red
  button and a sum to solve in the confirmation popup, checked on the server;
  *Restore default* goes back. Every change is recorded in the change log.
- **Test connection**, on the device form (the values typed in, nothing saved)
  and on the device's page (its saved settings).

### Changed
- **Saving a device never connects to it.** The credential test used to run on
  every save, so a device that did not answer could not be disabled, have its
  monitoring switched off, or be added in advance. The import tool does not
  connect either. The address must be a valid IP address or hostname; it no
  longer has to resolve.

### Security
- **The stored password is only sent to the stored address.** Testing a new
  address or port needs the password typed again.

## [0.14.0] - 2026-11-22

### Added
- **Execute a command on one device** from the router list - a terminal icon
  next to edit - or from the device's page, for operators (level 40). Only
  commands with a variant for the device's type are offered.
- **Every manual execution is confirmed first** - on one device, on a
  selection from the list, or on devices and groups from a command's page: a
  confirmation page shows, per device type, the exact commands that will be
  sent, the verification that follows and the devices they go to. Nothing runs
  until it is confirmed, and a command edited after the confirmation page was
  shown is shown again instead of run.
- **The update check is an icon in the top bar**, on every page, next to My
  Settings and sign out: green on the latest release, yellow when an update is
  available (its version in the tooltip, a click opens the release), grey when
  the check is off or has not run yet. It replaces the status page's card.

### Changed
- **The About page** opens with the logo, large and centred.
- **Tools** sits above Audit Log in the menu.
- **Phones**: tables that the list view does not already fold scroll sideways
  inside their card instead of widening the page; chart headers wrap.

### Fixed
- **An up-to-date OpenWrt device was counted as "without version
  information"**: when no newer release was offered, no version was stored to
  compare with. It now counts as up to date; a failed release-list download or
  a snapshot build stays unknown.
- **RouterOS update check waited a fixed 5 seconds** for the device to reach
  MikroTik's update server; a slower answer left the device without
  information. It now reads again every 3 seconds, for up to 15.
- **RouterOS "update available" compared versions as text**: a device ahead of
  its channel was flagged. Versions are compared as numbers, by the rule the
  list colours with.
- **airOS devices**, which can not be checked for updates over SSH, are no
  longer counted as missing information an information update could fill in.

## [0.13.0] - 2026-11-22

### Fixed
- **Retention could delete every backup of a device** offline for longer than
  its retention: an offline device is not attempted, so no failure stopped the
  sweep. A device's newest stored configuration is now never deleted, however
  old.

### Security
- **The first administrator's password is checked like every other.** The
  form that creates the first account - an administrator - accepted any
  password, `1` included; it now applies the same rules as the user manager
  (length, common passwords, entirely numeric, similarity to the username).
- The monitoring container refuses a device address that `fping` would read as
  an option (one starting with `-`), whatever the database holds.

### Added
- **Backup profiles keep only changes.** A scheduled backup whose
  configuration is the same as the device's last stored one is recorded - it
  succeeded, at its time, and counts for backup age - but its text and binary
  are not stored again; it points to the stored backup and is marked
  *unchanged*. The default for new profiles; **profiles that exist when you
  upgrade keep every backup, as before** - an upgrade does not change how
  backups are stored - and can be switched later. *Backup now* backups are
  always stored in full.
- **Tools**, a new section for administrators, with two tools: **Backup
  clean-up** audits the stored backups and frees the duplicates (as unchanged
  backups), removes files no backup refers to, and reconciles the database with
  the disk; **Export latest backups** downloads a zip with each device's latest
  configuration, one file per backup, with a manifest.
- **A redesigned status page.** Charts on top - backups and devices online per
  calendar day, over 7, 15 or 30 days; a click on a day's bar opens that day's
  backups. Below them, modern cards: a coloured edge and a chip saying what to
  do (OK, Look, Act), the number large, the change against the previous 24
  hours, and a sparkline wherever RouterFleet keeps the history - never an
  invented one. A new **Scheduler & monitoring** card turns red when either
  container has stopped working, which otherwise goes unnoticed.
- **Every status card opens what it counts.** Failed backups open the devices
  with a failed backup in the last 24 hours; offline, disabled, unmonitored,
  up to date, missing updates and without information open those devices; the
  backup cards open the last 24 hours of backups. The list says it is filtered,
  and holds exactly as many items as the card shows.
- **Zabbix kit: security and operations items.** Devices without a stored SSH
  host key, host keys learned in the last hour (a rebuilt, replaced - or
  impersonated - device), failed sign-ins over 15 minutes, recent configuration
  changes and failed Fleet Commander jobs, with triggers for the ones worth an
  alarm.
- **A Zabbix integration page** in the documentation (`docs/zabbix.md`): what
  the kit reports, what its role can and cannot read, what it needs, and the
  four setup steps - linked from the README and from its own group in the
  documentation index.

- **A logo.** A controller and three devices inside a hexagon whose rim closes
  on itself with an arrow: continuous improvement of the fleet. It is in the
  application's top-left corner next to the name, in the browser tab, and on
  the README.

### Changed
- **The About page is full width and rewritten**: what RouterFleet Improved by
  Alessio Bravi does, the supported devices, and where it comes from - the
  earlier RouterFleet projects it started from and was inspired by, with their
  authors credited. Its buttons are centred.
- **My Settings has its own menu entry**, at the bottom with About. It used to
  light up User Manager, which is administration.
- The sign-in button, and the code page's, are centred.
- **The Zabbix monitoring role reads only the columns it counts**, also in the
  new tables: whether a sign-in succeeded and when - not who, from where, or the
  keys of open sessions; when a change was recorded - not what it was.
- **A counter the collector cannot read reports `-1`, never `0`**, and one
  trigger fires on it: a missing grant used to look exactly like a healthy
  fleet. This also applies to the existing router and failing-backup counters.

## [0.12.3] - 2026-11-22

### Fixed
- **Command schedules failed with "Server Error (500)" on PostgreSQL.** The
  schedule defaults row is named `schedule_defaults` - 17 characters - in a
  column created for 16, so PostgreSQL refused to store it and every page that
  loads the defaults failed. A migration widens the column; installations
  repair themselves on upgrade, with nothing to do by hand. (The flaw came with
  the schedule defaults of the earlier projects' history.)
- **The change log listed a record's fields in the wrong order on
  PostgreSQL**, which stores JSON without key order. The order is rebuilt from
  the list of fields each model is recorded with, which also corrects the
  entries recorded before this fix.

### Added
- **CI runs the whole test suite on PostgreSQL 16 as well**, including the
  upgrade from both earlier projects' databases. The suite ran on SQLite only,
  which ignores length limits and keeps JSON key order - how both bugs above
  passed every test. A test also checks that every default and choice of every
  text field fits its field.

## [0.12.2] - 2026-11-22

### Fixed
- **Server errors are written to the container log.** With debug mode off,
  Django's default only e-mails a failed request's traceback, so a
  "Server Error (500)" left no trace anywhere an operator looks.
  `docker compose logs routerfleet` now shows the error and its traceback
  (the request line only - never request bodies).

## [0.12.1] - 2026-11-22

### Security
- A notification channel's token and destination are checked again every time a
  message is sent, not only when the channel is saved: a channel saved by an
  earlier version, never checked, could otherwise put an arbitrary path into
  the request to the notification service. Such a channel now fails with a
  message asking to edit it.
- The passkey endpoints answer with a fixed sentence when passkeys cannot be
  used, never with the text of an exception.

## [0.12.0] - 2026-11-22

### Added
- **Second factors.** Each user can turn on an **authenticator app** (TOTP,
  RFC 6238): signing in then asks for the password and a 6-digit code, each code
  is accepted once, and wrong codes count towards the sign-in lockout. And
  **passkeys**: sign in without a password, with the device's fingerprint, face
  or PIN. Both are managed in *My Settings*; an administrator can reset a user's
  second factors, and `manage.py reset_mfa` covers a locked-out sole
  administrator. Passkeys need the installation to be opened by a name with a
  trusted certificate - a browser rule - while the authenticator app works
  everywhere.
- **Update card on the status page:** green when this is the latest release,
  yellow with the version to update to when there is a newer one. The update
  check is now **on by default**: about once an hour it asks GitHub for the
  latest release, with nothing about the installation in the request.
  `UPDATE_CHECK=off` in `.env` turns it off.
- **Zabbix monitoring kit** in `contrib/zabbix/`: per-device backup age and
  result, fleet counters and a self-monitoring collector. Its database role can
  read only the ten columns it needs - not the device passwords, not the
  backed-up configurations - and CI runs it against a real database.

### Changed
- **The home page is the status page.** The former welcome text is the
  **About** page, at the bottom of the menu.
- Status cards are all the same size. **Failed Backups (24h)** and
  **Unmonitored Routers** (renamed from Not Monitored Routers) are green at 0.
  **Routers Up To Date** and **Routers Missing Updates** are hidden while they
  count nothing.
- The title is **RouterFleet Improved** throughout the interface.

### Removed
- **The WireGuard WebAdmin integration**, with its menu entry and pages. An
  upgrade drops its table and the token stored in it.

### Fixed
- A schedule could be saved with an end before its start; it is refused now.

## [0.11.5] - 2026-11-22 - First public release

The first public release of RouterFleet Improved: backup, inventory,
monitoring and remote command management for MikroTik RouterOS, OpenWrt and
Ubiquiti airOS fleets, published as five container images for amd64 and arm64.

An installation of an earlier RouterFleet project upgrades to it in place: its
database is migrated forward on first start, with no export or re-import. The
upgrade paths are tested on every push — see [Upgrading](docs/upgrading.md).

### Features
- **Backups**: daily, weekly, monthly and on demand, with retention per kind,
  text and binary, a line-by-line diff between any two, and a flag on every
  backup that changed the configuration.
- **Fleet Commander**: commands with a payload per device type, run by hand or
  on a schedule, with verified results. A command counts as successful only
  when a verification command proves it.
- **Inventory**: model, serial, OS and firmware version, architecture, CPU,
  and the version each device could be upgraded to.
- **Monitoring**: an independent container pings the fleet, records downtime
  and raises notifications on status changes.
- **Notifications**: Telegram, ntfy.sh and CallMeBot (WhatsApp), with
  concatenation and daily reports.
- **Audit log**: every sign-in attempt, and every configuration change field
  by field, in an append-only trail.
- **Bulk import** of devices from CSV, groups, per-user settings and five
  permission levels.

### Security
- Every stored credential is encrypted at rest — device and import passwords,
  SSH private keys, notification and integration tokens — with a key kept
  outside the database.
- Device SSH host keys are trusted on first use and checked on every
  connection; a different key is refused before any credential is sent.
- Every action that changes something is a POST carrying the CSRF token.
- Sign-in attempts are limited: 5 refused attempts for an account or from an
  address within 15 minutes.
- Session and CSRF cookies are `Secure` when HTTPS is enforced; responses carry
  `nosniff`, `X-Frame-Options: DENY` and a same-origin referrer policy; nginx
  does not announce its version.
- The scheduler endpoints are unreachable from outside; the debug endpoints
  need an administrator; the monitoring key never travels in a URL.
- Notification requests are built with encoded parameters, and a channel's
  token never appears in a stored error.
- No secret key in the repository; every outbound request has a timeout;
  nothing is loaded from another host by the browser.

### Release engineering
- Images based on Alpine (the database on Debian), installed with every
  available update, for amd64 and arm64.
- Every release is gated by a Trivy scan: no critical or high vulnerability in
  RouterFleet's images, none with a published fix in the database image, no
  committed secret. Each release's notes list what the scan still found.
- CodeQL code scanning on every push, on pull requests and weekly.
- Every action in CI is pinned to a commit.
- 490+ tests with a branch-coverage gate, the whole stack booted and checked
  in CI, and the upgrade from earlier databases tested on every push.
- Documentation with diagrams of every subsystem, in `docs/`.
