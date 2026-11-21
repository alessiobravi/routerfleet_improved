# Changelog

Releases are numbered MAJOR.MINOR.PATCH. Every push to `main` is a release: a
push that fixes or maintains raises PATCH (0.11.5 → 0.11.6), a release that
brings a new feature raises MINOR (→ 0.12.0). Each release is tagged
`v<version>`, published as a GitHub Release, and its Docker images are tagged
with it. The release is `ROUTERFLEET_RELEASE` in `routerfleet/settings.py`;
`scripts/version.py bump patch|minor` raises it.

Only the newest release is published: each release deletes the older ones,
with their tags and images. Use `:latest`.

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
