# Operations

## Management commands

Run them inside the application container (they run as the application's
unprivileged user, as the application does):

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

### `http_access`

Shows the [HTTP access list](security.md#http-access-lists-and-banning), its
final policy and the current bans; `--reset` removes every entry and every
ban and sets the final policy to Allow - for when the web interface shut you
out.

```bash
python manage.py http_access            # the list, the final policy, the bans
python manage.py http_access --reset    # no entry, no ban, every address allowed
```

### `https`

The HTTPS mode, from the host - for when a change in System settings locked
you out ([HTTPS](https.md#if-a-change-locks-you-out)):

```bash
python manage.py https status            # what is set, and what nginx applied
python manage.py https reset             # back to HTTPS_REDIRECT_POLICY in .env
python manage.py https mode selfsigned   # the self-signed certificate
```

### `reset_mfa`

Turns off a user's authenticator app and removes their passkeys - for a lost
phone, also for the only administrator ([Security](security.md#second-factors-and-passkeys)):

```bash
python manage.py reset_mfa <username>
```

### `optimize_database`

Compacts the database (PostgreSQL `VACUUM ANALYZE`, SQLite `VACUUM`); `--full`
runs `VACUUM FULL` on PostgreSQL, which locks tables while it works
([Tools](tools.md)):

```bash
python manage.py optimize_database
python manage.py optimize_database --full
```

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
| Backups past their retention | per profile | Deleted, with their binary files |
| Messages | 30 days | Deleted |
| Discovery runs | 30 days | Deleted |
| Discovered devices not heard | 90 days | Deleted (hidden ones kept, so they stay hidden) |
| Bans | 30 days after they ended | Deleted |

The retention sweep writes **one** audit entry naming how many rows it removed,
and a pass that removes nothing writes nothing. A backup's binary file goes
with its row however the row goes - retention, or deleting the device. The
login history and the command history are kept until pruned on purpose
(`prune_audit_log`, see [Management commands](#management-commands)): they are the audit trail.

## System settings

*System settings* - for **administrators only**, and in the menu only for them -
gathers what belongs to the whole installation, in collapsible sections:

| Section | What |
|---|---|
| Media and messaging | notification channels, the mail server, grouping and daily reports |
| Devices | device types, the firmware repository |
| Display | default rows per table: what every list opens with, for everyone (10 until changed) |
| Backups and jobs | backup profiles, Fleet Commander schedule defaults |
| Security | HTTPS, HTTP access (access list and banning after failed sign-ins), updates |

Message Center shows the messages only. Configuration Managers still start
and follow device updates from the device list and the device page.

What each person chooses for themselves - light or dark mode, their own email
notifications, sign-in security - is in *My Settings*.

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
each with a coloured edge and an icon saying what to do - a green tick when all
is well; the warning sign in yellow (worth a look), orange (needs attention
soon) or red (act now); grey for information - and a sparkline where
RouterFleet keeps the history. Pointing at the icon says it in words. The same
icons mark states everywhere: beside the words of a state on object pages and
in the state badges of lists. **Every card opens the list of exactly what it counts.**

- **Failed / successful backups, last 24 hours** — compared with the 24 hours
  before. Failed opens the devices with a failed backup.
- **Backup queue** — backups neither succeeded nor failed yet. A number that only
  grows means backups are not completing.
- **Backup disk** — how full the disk holding the backups (`MEDIA_ROOT`) is:
  the whole disk, since a disk filled by something else stops backups just the
  same. Yellow from 75%, orange from 80% or when it would be full within 30
  days, red from 90% or within 7 days. The note says how much is free, how much
  the backups take, and how fast the disk fills: one sample a day, taken by the
  housekeeping run, so the pace is known after a few days.
- **Scheduler & monitoring** — red when the scheduler has not run for 5 minutes
  or the monitoring for 10: that container has stopped, and with it the
  backups or the reachability checks.
- **Devices online, offline, disabled, unmonitored.**
- **OS versions** — missing an update, up to date (both shown only when they
  count something), without information. Counted by the same rule that colours
  the device list. airOS devices, which can not be checked for updates, are not
  counted.

**This week**, below the cards, is what happened in the last 7 days, per
device, in three cards - side by side, stacked on a phone: **Configuration
changed** (how many changes), **Updated** and **Outages** (how many, how long - a
device down right now first, in red). An update says what was updated, with the
mark the device list uses: **Firmware** (green, the OS) and **Bootloader**
(purple), both when a rollout did both; the versions are on the device's page.
Each card shows its first 10 devices and counts the rest. Each name opens the
device - an outage its availability page.

**On every page**, the status bar shows the installation's own problems: the
scheduler or the monitoring stopped (red), the backup disk 80% full or filling
up within 30 days (yellow; red from 90% or within 7 days). Each opens the status
page. Nothing is shown when all is well.

Whether a newer release of RouterFleet Improved exists is shown by the download
icon in the top bar, on every page: green on the latest release, yellow when an
update is available, grey when the check is off (`UPDATE_CHECK`) or has not run
yet.

## Finding your way around the interface

### Search

**One search for everything**: the magnifying glass in the top bar, or
**Ctrl+K** (⌘K on a Mac), or **/** anywhere outside a text field. It finds
devices (by name or address), device groups, backup profiles, Fleet Commander
commands, SSH keys, users and the pages of the menu (by their name and what
they are about: "certificate" finds HTTPS), and opens each where it is shown.
↑ and ↓ move, Enter opens, Esc closes. It shows only what you may open: commands
from level 20, users and System settings for administrators.

### Lists and objects

Every collection of objects opens the same way: the list on the left, the
chosen item on the right - **Devices**, **Device Groups**, **Discovered**,
the **Backup list** and **Backup Profiles**, and **Fleet Commander** commands
(with their schedules and variants). The address bar keeps the chosen item,
so a link or a reload opens it again; on a phone the list comes first.
**Users**, **SSH Keys** and the **Message Center** are lists whose rows open
on a click: nothing about a user, key or message shows until its row is
opened. An open page keeps itself current: every half minute, and when its
tab comes back to the front, it checks whether devices, backups or jobs
changed - in another browser, by the scheduler, after a firmware update -
and refreshes what it shows, keeping what was ticked or typed. Histories - **Jobs**,
the **Audit Log**, a device's **availability**, **imports** - are one calm
table under the same header and tabs. A state is a dot and a word
everywhere; colour is kept for what needs attention. Action buttons are coloured by what they
do, the same on every page - teal console and backup, blue command, green
system update, purple bootloader, amber edit, red abort or delete - and on a
phone show their icon alone (its name in a tooltip).

### Sections

Settings, forms and tool pages are made of **sections that start closed**:
each shows its title and a line saying what it holds, and opens with a click,
so nothing is changed by accident. What you open stays open while you move
around in the same browser session, also while the page updates itself. A
section opens by itself when it holds an error, when a required field in it
is left empty on saving, or when a link points into it. The progress and the
findings of a running discovery are always shown, as is a page made of a
single form or list. Creating a device type, step by step, shows each step
open.

### Object pages

A Fleet Commander job, a task, a device and a backup open the same way:

- a **hero** on top - a progress ring or a state icon, the name, the state (icon and words),
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
**status bar** at the bottom of every page says so - "Live: …" with what is
running, in green; it goes back to "Ready", and the page stops asking the
server, once everything has finished. The version is on its right, with
"Update available" when there is one. It pauses while the
browser tab is hidden, never replaces a field you are typing in, backs off when
the server does not answer, and stops on an expired session ("Live updates
stopped - reload the page"). The status page's cards refresh every 30 seconds.

### Notifications

Messages - saved, started, not applied - appear as a balloon at the **top
centre** of the page: teal for success, blue for information, amber for a
warning, red for an error. A balloon closes after **5 seconds** (the bar along
its bottom shows the time left, and pauses while the pointer rests on it) or
when you click it; several stack. They show text only.

### Confirmations

An action that can not be undone - deleting, aborting, starting a firmware
update, switching HTTPS off, changing a system command - asks first, **inline,
right below what was clicked**: the question and a small sum to answer, then
the action's own button (*Delete*, *Abort*, *Update*...) or *Cancel*. Enter
confirms, Escape cancels; a wrong answer asks a new sum. Lighter actions (a
passkey's removal, disabling hosts) ask a plain *Confirm*. The server checks
the confirmation again, so it can not be skipped. No browser popup is used,
except the console's question before opening a link the device printed - a
browser opens a new tab only straight from a click.

### Light and dark

The moon in the top bar switches to **dark mode** - for the night - and the
sun back. *My Settings → Visualization options* also offers **Automatic**, the default,
which follows the device: dark when the computer or phone is in dark mode, and
changing with it. The choice is kept by each browser. The page is drawn in its
theme from the start, without a white flash.

### Colours

Every section of the menu has a tone, and the icons of its pages carry it:
Status sky, Devices Manager blue, Device Discovery sky, Backup teal, Fleet Commander green, User
Manager rose, Message Center orange, Tools indigo, System settings purple,
Audit Log amber, My Settings cyan. Where an icon tells a state, the state
decides: green up to date, amber to be set up, red locked or failing.

Adding something - a user, a command, a device type, firmware, a channel - is
the last row of its list, with a coloured "+".

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

- Check that the device type is right: the connection test says when the device
  does not match the type's recognition pattern. *Try it* on the device type
  shows what each command returns and what was found in it.
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

### Pages are slow

Every request is in the same log with how long it took, the last field:

```text
172.18.0.6 [24/Nov/2026:10:31:02 +0000] "GET /status/ HTTP/1.0" 200 41237 23ms
```

```bash
docker compose logs routerfleet --since 10m | grep -E ' [0-9]{4,}ms$'   # a second or more
```

Fast lines but a slow page in the browser point at the network between the
browser and the server: static files are sent compressed and kept by the
browser for a year (their names change when they do), so only the first page
after an update loads them. Slow lines on `/cron/` are the scheduler's work
with devices and do not hold pages up (each worker serves several requests at
once on PostgreSQL).

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

CI runs the lint (`ruff check`, rules in `ruff.toml`), the system check, the
migration-drift check and the suite on every push and pull request, and fails
below 97% branch coverage. Tests never reach the internet: what a page would
fetch is replaced in the test, and a test run keeps its own temporary cache. Docker images are only
built and published once the tests have passed.
