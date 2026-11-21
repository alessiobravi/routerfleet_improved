# Operations

## Management commands

Run them inside the application container:

```bash
docker compose exec routerfleet python manage.py <command>
```

### `schedule_defaults`

Reads or sets the start time and repeat interval that **new** command schedules
are created with. Existing schedules are untouched.

```bash
python manage.py schedule_defaults                           # show current values
python manage.py schedule_defaults --start-time 03:00 --repeat 7d
python manage.py schedule_defaults --repeat 0                # new schedules run once
python manage.py schedule_defaults --reset                   # back to 03:00 / 7d
```

Intervals are written as `7d`, `24h` or `30m`; empty, `0`, `none`, `off` and
`one time` all mean a single run.

### `prune_audit_log`

The only way to remove audit entries. `--older-than` is a number of days and is
required, so the command cannot wipe the history by accident.

```bash
python manage.py prune_audit_log --older-than 365
python manage.py prune_audit_log --older-than 365 --dry-run
python manage.py prune_audit_log --older-than 90 --login-history
python manage.py prune_audit_log --older-than 90 --change-log
```

With neither flag, both logs are pruned.

Changes made from a management command appear in the change log as
**command line**, and changes made by the scheduler container as **cron**.

## Housekeeping

`/cron/housekeeping/` runs every 10 minutes and is the installation's garbage
collector:

| What | Threshold | Action |
|---|---|---|
| Backup `task_lock` | 6 hours | Cleared, warning written to the task console, notification sent |
| Unfinished backup row | 18 hours | Marked `error` with `Backup task expired`, notification sent |
| Device `backup_lock` with nothing pending | 18 hours | Cleared, device marked last-backup-failed |
| Device `command_lock` | 2 hours | Cleared |
| Backups past their retention | per profile | Deleted |
| Messages | 30 days | Deleted |

The retention sweep writes **one** audit entry naming how many rows it removed,
and a pass that removes nothing writes nothing.

## Reading the status page

The status page is the fastest health check:

- **Scheduler and monitoring last run** — if either is stale, that container is
  not running, and the corresponding work has stopped.
- **Backup queue** — rows neither succeeded nor errored. A number that only
  grows means backups are not completing.
- **Backups in the last 24 hours**, succeeded and failed.
- **Storage** — disk usage under `MEDIA_ROOT`, with a warning above 75% and
  above 90%. Binary backups are what fills it.
- **Fleet version state** — up to date, missing an update, no information.
  Counted by the same rule that colours the device list.

## Troubleshooting

The images carry only what they need, so there is no editor and no `ping` or
`netstat` package inside them. A shell and BusyBox's basic tools are there:
`docker compose exec routerfleet sh`. To test reachability from the
installation's network, `docker compose exec routerfleet-monitoring fping <address>`.

### Nothing is happening — no backups, no commands

Check the cron container. The application has no internal scheduler; the cron
container is the only clock.

```bash
docker compose ps routerfleet-cron
docker compose logs routerfleet-cron --tail 50
```

### `400 Bad Request` with no explanation

The address you used is not in `ALLOWED_HOSTS`. Set `SERVER_ADDRESS` to the
address you actually reach the server at and restart. `localhost` and
`127.0.0.1` always work, so compare against those to confirm.

### CSRF errors when signing in

Same cause — `SERVER_ADDRESS` does not match the address in the browser's
address bar, including the port if you changed it.

### Everyone was signed out after a restart

Expected. `SECRET_KEY` is regenerated on every container start, which
invalidates all sessions.

### A device's backups fail but SSH works by hand

- Check that the device type is right — a branded MikroTik declared as
  `routeros` is rejected, and vice versa.
- Check reachability. A monitored device that pings as offline is **skipped**
  rather than attempted; if it drops ICMP but accepts SSH, switch monitoring off
  for it.
- Read the task console output on the backup — every command, its output and its
  exit code are recorded there.

### A command task sits in "pending" for a long time

If its variant has a verification, that is the verification loop waiting. It
retries every `verify_interval` seconds until `verify_timeout` passes — 600
seconds for the shipped update commands, so an updating device has time to
reboot. The task details page shows every attempt.

### A command reports success but nothing changed

Without a verification, a command is judged on its exit code alone. Add
verification commands and an expected result to the variant — see
[Fleet Commander](fleet-commander.md#verified-results).

### The "update available" badge never appears

The application update check is **off by default** and makes no outbound
request. Set `UPDATE_CHECK_URL` to an endpoint of your own to enable it.

## Local development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`routerfleet/settings.py` ends with `from routerfleet.production_settings import *`,
and that file is generated by the container entrypoint — so outside Docker you
must create it yourself. A minimal version:

```python
DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1']
SECRET_KEY = 'dev-only'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': 'db.sqlite3'}}
MONITORING_KEY = 'dev-monitoring-key'
```

It is gitignored. With no `ENCRYPTION_KEY` set, the encryption key is derived
from `SECRET_KEY`, so development works without further setup — but a database
written that way cannot be read by a container with its own key.

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py check
.venv/bin/python manage.py runserver
```

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python manage.py test                 # the whole suite, a few seconds
.venv/bin/coverage run manage.py test && .venv/bin/coverage report
```

The suite needs the same `production_settings.py` as above. It never touches the
network: SSH, SCP and every HTTP call are replaced by fakes from
`routerfleet/testing.py`, so it runs anywhere, offline included.

What it covers:

- **Every page's permission gate**, from three sides — anonymous, one level
  below, and exactly at the level (`routerfleet/tests.py`). That table is the
  permission model; [Users and permissions](users.md) describes the same one.
- **The scheduler endpoints** refuse anything that came through a proxy.
- **Fleet Commander**: the verification loop, the payload running once, abort
  between lines, retry counting, missed runs, exclusions.
- **Backups**: both phases for every device type, online gating, retention,
  change detection.
- **The upgrade from the original project's database**
  (`routerfleet/test_upgrade.py`): the database is taken back to that state,
  filled through the models as they were then, and migrated forward — passwords
  must come out encrypted and readable, change flags backfilled, and a
  user-written update command kept line for line.

CI runs the system check, the migration-drift check and the suite on every push
and pull request, and fails below 93% branch coverage. Docker images are only
built and published once the tests have passed.
