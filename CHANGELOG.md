# Changelog

Every push to `main` carries a new version. The version is `ROUTERFLEET_VERSION`
in `routerfleet/settings.py`, read as `<major>.<four digits>` (7515 → 0.7515);
each one is tagged `v<version>`, published as a GitHub Release, and its Docker
images are tagged with it. See [Upgrading](docs/upgrading.md).

## [0.7516] - 2026-11-21

### Changed
- The cleanup of untagged image versions uses the GitHub API directly instead of
  a third-party action that still targeted the deprecated Node.js 20. Every
  deleted version is now named in the build log, with the tags that were kept.

## [0.7515] - 2026-11-21

### Added
- **Test suite**: 395 tests, 91% branch coverage, offline (SSH, SCP and HTTP are
  faked). Every page's permission gate is tested from three sides, and the
  upgrade from an original-project database runs on every push.
- **CI gate**: system check, migration-drift check and the suite run on every
  push and pull request; below 90% coverage the build fails, and images are
  only published after the tests pass.
- **Versioned releases**: every push to `main` creates tag `v<version>`, a
  GitHub Release with this changelog's section, and images tagged `:<version>`
  alongside `:latest`. Images carry OCI labels and are linked to the repository.
- Untagged image versions left behind by earlier builds are deleted after each
  release.
- **Documentation** in `docs/`: fourteen pages written from the code, with 23
  Mermaid diagrams. The README is now a short page with a quick install.
- `scripts/version.py` prints the version and its changelog section.

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
- The version is shown as `0.7515` rather than as a float.
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
- A debug `print()` wrote every downloaded backup's filename to the log.

### Removed
- The unreachable telnet code path, whose `telnetlib` import kept the images on
  Python 3.12.
- The screenshots.

## [0.7514] - 2026-11-21

Initial import of the codebase.
