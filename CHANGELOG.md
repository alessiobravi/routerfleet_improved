# Changelog

Releases are numbered MAJOR.MINOR.PATCH. Every push to `main` is a release: a
push that fixes or maintains raises PATCH (0.8.0 → 0.8.1), a release that brings
a new feature raises MINOR (→ 0.9.0). Each release is tagged `v<version>`,
published as a GitHub Release, and its Docker images are tagged with it. The
release is `ROUTERFLEET_RELEASE` in `routerfleet/settings.py`;
`scripts/version.py bump patch|minor` raises it.

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
