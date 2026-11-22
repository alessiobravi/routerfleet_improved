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

The status page is the home page and the fastest health check. Two charts on
top show backups and the devices' status per calendar day over 7, 15 or 30
days: online (green), offline but enabled (red) and disabled (purple). Each
device is counted once a day, from the day it was added, as it was at the end
of that day (today: now): disabled from when it was last switched off, offline
during the offline periods the monitoring recorded, online otherwise. An
enabled device that is not monitored has no status and is not counted. Clicking a
day's backup bar opens that day's backups; clicking a point of a devices line
opens the devices in that state. Below them, one card per signal,
each with a coloured edge and a chip saying what to do - green *OK*, amber
*Look*, red *Act*, grey for no information - and a sparkline where RouterFleet
keeps the history. **Every card opens the list of exactly what it counts.**

- **Failed / successful backups, last 24 hours** — compared with the 24 hours
  before. Failed opens the devices with a failed backup.
- **Backup queue** — backups neither succeeded nor failed yet. A number that only
  grows means backups are not completing.
- **Backup storage** — disk usage under `MEDIA_ROOT`, amber from 80%, red from
  90%. Binary backups are what fills it.
- **Scheduler & monitoring** — red when the scheduler has not run for 5 minutes
  or the monitoring for 10: that container has stopped, and with it the
  backups or the reachability checks.
- **Devices online, offline, disabled, unmonitored.**
- **OS versions** — missing an update, up to date (both shown only when they
  count something), without information. Counted by the same rule that colours
  the device list. airOS devices, which can not be checked for updates, are not
  counted.

Whether a newer release of RouterFleet Improved exists is shown by the download
icon in the top bar, on every page: green on the latest release, yellow when an
update is available, grey when the check is off (`UPDATE_CHECK`) or has not run
yet.

## Finding your way around the interface

### Object pages

A Fleet Commander job, a task, a device and a backup open the same way:

- a **hero** on top - a progress ring or a state icon, the name, a state chip,
  the key facts, and the actions (abort, edit, execute a command, download);
- **four tiles** with the numbers that matter for that object;
- a **timeline** of what happened, newest first;
- the full details below.

| Page | Ring | Tiles | Timeline |
|---|---|---|---|
| Job | tasks finished out of the total | succeeded, running, waiting, failed | job created, each device started and finished, job completed |
| Task | a state icon instead | runs, started, finished, verification | queued, started, verifying, result |
| Device | availability over the last 7 days | last backup, failed backups in 7 days, OS version, downtime in 7 days | backups, commands, going offline and back |
| Backup | its phase: creating, downloading, stored | configuration size, binary, change, retries | queued, each step sent to the device, result |

### Pages that update themselves

While something on a page is still running, the page keeps itself current: a
job and its tasks, the job list, a backup and the backup list, and a device's
page while a backup, a command or an information read is under way. A
**Live** pill in the corner shows it is updating; it disappears, and the page
stops asking the server, once everything has finished. It pauses while the
browser tab is hidden, never replaces a field you are typing in, backs off when
the server does not answer, and stops on an expired session ("Live updates
stopped - reload the page"). The status page's cards refresh every 30 seconds.

### Notifications

Messages - saved, started, not applied - appear as a balloon at the **top
centre** of the page: teal for success, blue for information, amber for a
warning, red for an error. A balloon closes after **5 seconds** (the bar along
its bottom shows the time left, and pauses while the pointer rests on it) or
when you click it; several stack. They show text only.

### Light and dark

The moon in the top bar switches to **dark mode** - for the night - and the
sun back. *My Settings → Appearance* also offers **Automatic**, the default,
which follows the device: dark when the computer or phone is in dark mode, and
changing with it. The choice is kept by each browser. The page is drawn in its
theme from the start, without a white flash.

### Switches

Every on/off setting is a switch. A real checkbox remains only where it means
consent - accepting the Let's Encrypt agreement.

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

### A page answers "Server Error (500)"

The cause is in the application's log, with its traceback:

```bash
docker compose logs routerfleet --tail 200
```

Look for `ERROR django.request: Internal Server Error:` followed by the path.
Only the request line and the traceback are logged, never what a form sent.

### The update icon stays grey

The download icon in the top bar is green on the latest release, yellow when an
update is available, and grey before the first check (within an hour of
starting) and when `UPDATE_CHECK=off`. Otherwise the check asks GitHub about
once an hour; if the host cannot reach `api.github.com`, the icon keeps its last
answer. An endpoint of your own can be set with `UPDATE_CHECK_URL`.

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
