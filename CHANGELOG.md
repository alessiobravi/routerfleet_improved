# Changelog

Releases are numbered MAJOR.MINOR.PATCH. Every push to `main` is a release: a
push that fixes or maintains raises PATCH (0.11.5 → 0.11.6), a release that
brings a new feature raises MINOR (→ 0.12.0). Each release is tagged
`v<version>`, published as a GitHub Release, and its Docker images are tagged
with it. The release is `ROUTERFLEET_RELEASE` in `routerfleet/settings.py`;
`scripts/version.py bump patch|minor` raises it.

Only the newest release is published: each release deletes the older ones,
with their tags and images. Use `:latest`.

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
