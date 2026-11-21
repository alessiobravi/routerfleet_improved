# Changelog

Releases are numbered MAJOR.MINOR.PATCH. Every push to `main` is a release: a
push that fixes or maintains raises PATCH (0.8.0 → 0.8.1), a release that brings
a new feature raises MINOR (→ 0.9.0). Each release is tagged `v<version>`,
published as a GitHub Release, and its Docker images are tagged with it. The
release is `ROUTERFLEET_RELEASE` in `routerfleet/settings.py`;
`scripts/version.py bump patch|minor` raises it.

Releases before 0.10.0 were withdrawn in 0.11.0 (see there); their entries
below are kept as the record of what changed.

## [0.11.2] - 2026-11-21

### Security
- **`/debug/run_backups/` answered anyone** — no sign-in, no permission — and
  ran every pending backup whenever `DEBUG_MODE` was `True`. Both debug
  endpoints now need an administrator (level 50), and answer 404 outside debug
  mode.

### Changed
- The 0.11.1 entry describes the CSRF exposure precisely: a cross-site
  navigation, not an image tag (the session cookie is `SameSite=Lax`), and most
  actions needed the object's identifier.

## [0.11.1] - 2026-11-21

0.11.0 was never released: its CI run stopped before publishing, on a check of
its own — the stack test looked for the `Secure` cookie flag with a pattern
that GNU grep reads differently. The cookie was correct.

A security review of the code inherited from earlier projects, and what it
found.

### Security
- **Cross-site request forgery on every destructive action.** Deleting devices,
  groups, SSH keys, backups, users, commands, schedules and channels; aborting
  tasks; starting backups and imports; refreshing inventory; signing out — all
  were GET requests, which Django's CSRF protection does not check: another
  site could trigger them by sending a signed-in user's browser to the address
  (most also needed the object's identifier, which every signed-in user can
  see). Each is now a POST that needs the installation's CSRF token, and
  the same request as a GET changes nothing. Tests check all three: a GET does
  nothing, a POST without the token is refused, a POST with it works.
- **Device SSH host keys are trusted on first use and checked on every
  connection.** Any key used to be accepted, so whoever could intercept the
  traffic to a device could impersonate it and receive its password. The first
  connection now stores the key; a different key — of any algorithm — is
  refused before a credential is sent, with both fingerprints in the error. The
  device page shows the fingerprint and a **Forget host key** action for a
  device that was reset or replaced.
- **Sign-in attempts are limited**: after 5 refused attempts for an account or
  from an address within 15 minutes, further attempts are turned away without
  checking the password, and recorded in the login history with a lock icon.
- **The session and CSRF cookies are `Secure`** when HTTPS is enforced. The
  compose files pass `HTTPS_REDIRECT_POLICY` to the application for this, and
  the CI stack test checks the cookie.
- `settings.py` no longer carries a hard-coded `SECRET_KEY`; start-up is refused
  with an explanation if `production_settings.py` does not set one.
- Every outbound HTTP request has a timeout: an integration or notification
  service that stopped answering could hold a worker indefinitely.
- Backup files fetched from a device go through a private temporary directory
  that is removed afterwards — also when the retrieval fails halfway, which used
  to leave the device's configuration in the shared `/tmp`.

### Removed
- **Releases 0.8.0 to 0.9.3**, with their tags and images. They predate the
  vulnerability gate and their Debian-based images carried hundreds of known
  vulnerabilities. CI now keeps only releases from `RELEASE_FLOOR`
  (`routerfleet/versioning.py`) up.
- Empty leftovers of `startapp` in `accounts`, `dashboard` and `routerlib`,
  which are not installed apps.

### Fixed
- Bare `except:` clauses, which also swallowed shutdown signals, catch
  `Exception`; re-raised errors keep their cause. Unused imports and variables
  removed.

### Upgrading
- One migration: the table of device host keys. Coming from RouterFleet
  Extended 4 migrations apply, from the original 17 — see
  [Upgrading](docs/upgrading.md#changes-in-0110-that-you-may-notice).

## [0.10.1] - 2026-11-21

### Changed
- The vulnerability section of the release notes lists each vulnerability once,
  with every package it was found in, and folds long lists away: the database
  image's 433 findings are 157 vulnerabilities in one collapsible block instead
  of 433 lines.

## [0.10.0] - 2026-11-21

### Added
- **Vulnerability gate with Trivy.** Before anything is published, CI scans the
  images it has just built and booted: RouterFleet's images may have no critical
  or high vulnerability, the database image none with a published fix, and the
  repository no critical or high finding in its dependencies and no committed
  secret. A release that fails is not published. Each release's notes list what
  the scan still found.
- **`routerfleet-postgres`**, a fifth image: the official `postgres:16` with
  every available update and `gosu` rebuilt with the current Go. Same Debian
  base, so existing databases upgrade in place.
- `third_party/browser-libraries/` lists the browser libraries with the versions
  served, so the scan checks them; a test keeps it equal to the files.

### Changed
- **The application, scheduler, monitoring and nginx images are based on
  Alpine** and installed with every available update at build time. They no
  longer carry editors, network tools or `pip`.
- The vendored AdminLTE theme is reduced from 2,434 files (124 MB) to the 34
  pages use. Its demo pages, documentation and unused plugins — including old
  jQuery copies with known XSS vulnerabilities, served from the installation's
  own origin — are gone, as are Chosen's demo pages and archive.
- Pages no longer load scripts they never use: jQuery UI, moment,
  daterangepicker, tempusdominus, sparklines, jqvmap, jquery-knob, the table
  export buttons with jszip and pdfmake, and AdminLTE's demo dashboard script.
- jQuery 3.6.0 → 3.7.1, Bootstrap 4.6.1 → 4.6.2, diff2html → 3.4.56, each
  verified against npm's published checksum.
- CI checkouts no longer keep the job token in the working tree, and images no
  longer contain the repository's `.git`.

### Fixed
- **The status page charts ran on Chart.js 2.9.4.** The page loads Chart.js 4,
  but the base template loaded 2.9.4 after it and replaced it before the charts
  were drawn; the charts now use the version they were written for.
- A local image build would have copied `.git`, local secrets and working notes
  into the image; `.dockerignore` excludes them.

## [0.9.3] - 2026-11-21

### Added
- **Disclaimer**: the README, the installation specification and the
  documentation index state that the software is provided as is, without
  warranty, and used at your own risk, and spell out what that means for a tool
  that acts on network devices.
- **The upgrade from a RouterFleet Extended database is tested on every push**,
  next to the one from the original project: passwords it encrypted stay
  readable, the SSH keys and tokens it stored in cleartext are encrypted, and
  its audit trail and schedules come through untouched.

## [0.9.2] - 2026-11-21

0.9.1 was never released: its CI run stopped before publishing, on the race
fixed below.

### Fixed
- **nginx has a healthcheck.** On a first start nginx generates its self-signed
  certificate before it listens, so a container reported as running could still
  refuse connections for a few seconds — which is how CI's stack test lost a
  race. nginx now reports healthy only once it listens on 443.

### Added
- 33 tests for branches the feature tests did not reach: every delete
  confirmation, empty and invalid selections, every bulk-edit field, the weekly
  and monthly backup schedules, daily reports without a channel or with an
  unreadable time, the storage warnings, and `scripts/prune_images.py` against a
  faked GitHub API and registry. 446 tests, 94.6% branch coverage.

### Changed
- CI fails below 93% branch coverage, up from 90%.

## [0.9.0] - 2026-11-21

### Added
- **arm64 images.** Every image is published for amd64 and 64-bit ARM under the
  same tags, so RouterFleet installs on a Raspberry Pi 4/5 running a 64-bit OS,
  on ARM servers and on ARM cloud instances with the same compose file; Docker
  picks the right build.

### Changed
- The cleanup of old image versions understands multi-platform images: it keeps
  every release-tagged version together with the per-platform manifests it
  references, which are untagged versions of their own, and deletes only what
  no release needs. It deletes nothing when the release just published is
  missing or a manifest cannot be read. The logic lives in
  `scripts/prune_images.py` and is covered by tests.

## [0.8.3] - 2026-11-21

### Added
- **Healthchecks.** PostgreSQL reports healthy once it accepts connections, the
  application once gunicorn answers a real page. The application waits for the
  database, and the scheduler, monitoring and nginx wait for the application,
  so nothing calls it while it is still migrating.
- **CI boots the whole stack** from the commit before any image is published:
  it waits for the healthchecks, then checks the HTTPS redirect, the sign-in
  page, that `/cron/` is unreachable from outside, that static files are served
  with `nosniff`, that nginx hides its version, and that the scheduler's calls
  reach the application. All four compose files are validated.
- `docs/upgrading.md` describes moving the bundled PostgreSQL to a newer major
  version with a dump and restore, including the data path that changed with
  PostgreSQL 18. The default stays on `postgres:16`, because changing the image
  under an existing installation would leave it unable to start.

### Fixed
- Building with `docker-compose-dev.yml` tagged the RouterFleet nginx image as
  `nginx:alpine`, replacing the official image of that name on the machine.

## [0.8.2] - 2026-11-21

### Changed
- **Every stored credential is now encrypted at rest.** SSH private keys,
  notification channel tokens and the WireGuard integration's API key join
  device and import passwords. Existing values are encrypted by a migration when
  you upgrade; reads return them as they were written, so nothing that uses them
  changed.

### Added
- The upgrade test covers the three new encryptions on a database from the
  original project.

## [0.8.1] - 2026-11-21

### Changed
- **Pages load nothing from another host.** The Source Sans Pro font is bundled
  (Adobe's release 3.006R, unmodified, SIL Open Font License 1.1) instead of
  fetched from Google Fonts; Chart.js 4.6.1 (MIT) is bundled for the status page
  instead of an unpinned CDN copy that followed whatever version was newest; and
  the Ionicons stylesheet, which no template used, is gone. No page view tells a
  third party who is looking, and the interface renders completely offline.
- nginx no longer announces its version, and sends `nosniff` and a same-origin
  referrer policy for the static files it serves.

### Fixed
- A malformed `?uuid=` was a server error on 25 pages; it is now a 404.

### Added
- Tests that fail if a template references an external host or a static file
  that does not exist, and a CI check of both nginx configurations in the real
  image.

## [0.8.0] - 2026-11-21

First release of RouterFleet Improved.

### Added
- **Test suite**: 401 tests, 91% branch coverage, offline (SSH, SCP and HTTP are
  faked). Every page's permission gate is tested from three sides, and the
  upgrade from an original-project database runs on every push.
- **CI gate**: system check, migration-drift check and the suite run on every
  push and pull request; below 90% coverage the build fails, and images are
  only published after the tests pass.
- **Versioned releases**: every push to `main` creates tag `v<version>`, a
  GitHub Release with this changelog's section, and images tagged `:<version>`
  alongside `:latest`. Images carry OCI labels and are linked to the repository.
- After each release, image versions that are untagged or carry no release tag
  are deleted through the GitHub API, each one named in the build log; releases
  and tags that are not `vMAJOR.MINOR.PATCH` are removed.
- **Documentation** in `docs/`: fourteen pages written from the code, with 23
  Mermaid diagrams. The README is now a short page with a quick install.
- `scripts/version.py` prints the release, checks it is above every released
  one, prints its changelog section, and raises it (`bump patch|minor`).

### Changed
- Dependencies brought current: Django 6.1.3, paramiko 5.1.0, cryptography
  50.0.2 and the rest of the pinned set. `pytz`, `packaging` and
  `typing_extensions` are no longer needed.
- Images build on `python:3.14-slim`; the scheduler image on `ubuntu:26.04`
  instead of the floating `ubuntu:latest`, and runs cron as PID 1.
- CI actions moved off the deprecated Node.js 20 versions.
- The application update check is **off by default** and contacts nothing
  unless `UPDATE_CHECK_URL` is set; interface links come from
  `PROJECT_REPOSITORY_URL` and `PROJECT_SUPPORT_URL`.
- Default timezone is `Etc/UTC`.
- Releases are numbered MAJOR.MINOR.PATCH. The version stored in the database
  is derived from it and rises above every earlier one, so upgrades never see
  it go backwards.
- The warning logged when a stored value does not decrypt names both causes,
  instead of claiming the key is wrong on every upgrade.
- `build_and_push.sh` no longer runs `docker system prune -a`, which removed
  every unused image and container on the machine it ran on.

### Fixed
- Status chart: a non-numeric `?days=` was a server error.
- Monitoring: a status report failed when the device-list timestamp had never
  been set, after the change had already been recorded.
- Message center: editing a channel by an unknown id was a server error.
- Daily backup report: the warning about an unreadable report time was
  overwritten and never sent.
- First-user form and WireGuard integration form: submitting them with a
  required field empty was a server error.
- A database upgraded from an installation that had once been told of an
  update kept showing "Update Available" and appending an update notice to
  every message, because nothing cleared the flag once the check was off.
- A debug `print()` wrote every downloaded backup's filename to the log.

### Removed
- The unreachable telnet code path, whose `telnetlib` import kept the images on
  Python 3.12.
- The screenshots.

## Initial import

The codebase as imported, carrying version 0.7514.
