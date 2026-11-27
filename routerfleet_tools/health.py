"""The state of the installation itself, shown on every page when something
needs attention: the scheduler or the monitoring stopped (backups and
reachability checks then just cease, silently), the backup disk filling up.

The scheduler calls in every minute and the monitoring every few; the disk is
the one holding the backups (MEDIA_ROOT). How fast it fills comes from one
sample a day, taken by the housekeeping run."""
import datetime
import shutil

from django.conf import settings
from django.utils import timezone

SCHEDULER_LATE = datetime.timedelta(minutes=5)
MONITORING_LATE = datetime.timedelta(minutes=10)
DISK_WARN, DISK_BAD = 80, 90          # % of the disk used
DAYS_WARN, DAYS_BAD = 30, 7           # days until full, at the current pace
SAMPLES_KEPT = 30
MIN_SAMPLE_DAYS = 3                   # a pace from fewer days says little


def _settings():
    from .models import WebadminSettings
    return WebadminSettings.objects.get_or_create(name='webadmin_settings')[0]


def scheduler(now=None, webadmin=None):
    """{'cron': last call or None, 'monitoring': ..., 'stopped': [names]}."""
    now = now or timezone.now()
    webadmin = webadmin or _settings()
    cron, monitoring = webadmin.cron_last_run, webadmin.monitoring_last_run
    stopped = [name for name, last, late in (('scheduler', cron, SCHEDULER_LATE),
                                             ('monitoring', monitoring, MONITORING_LATE))
               if last is None or now - last > late]
    return {'cron': cron, 'monitoring': monitoring, 'stopped': stopped}


def pace(samples):
    """Bytes a day the disk fills, from the daily samples - None while there
    are too few days to tell, or when it is not filling."""
    points = []
    for day, used in samples or []:
        try:
            points.append((datetime.date.fromisoformat(day), int(used)))
        except (TypeError, ValueError):
            continue
    if len(points) < 2:
        return None
    points.sort()
    (first_day, first_used), (last_day, last_used) = points[0], points[-1]
    days = (last_day - first_day).days
    if days < MIN_SAMPLE_DAYS or last_used <= first_used:
        return None
    return (last_used - first_used) / days


def disk(webadmin=None):
    """The disk holding the backups: total, used, free (bytes), percent used,
    the pace it fills at and the days left at that pace (None when unknown)."""
    # The backup folder, or the nearest folder above it that exists (before
    # the first backup it may not): the same disk
    import pathlib
    folder = pathlib.Path(settings.MEDIA_ROOT)
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    try:
        total, used, free = shutil.disk_usage(folder)
    except OSError:
        return None
    webadmin = webadmin or _settings()
    per_day = pace(webadmin.disk_samples)
    return {'total': total, 'used': used, 'free': free,
            'percent': round(used * 100 / total, 1) if total else 0.0,
            'per_day': per_day, 'days_left': int(free // per_day) if per_day else None}


def record_sample(today=None):
    """Called by the housekeeping run (every 10 minutes): one sample a day."""
    state = disk()
    if state is None:
        return False
    today = (today or timezone.localdate()).isoformat()
    webadmin = _settings()
    samples = [s for s in (webadmin.disk_samples or []) if isinstance(s, list) and len(s) == 2]
    if samples and samples[-1][0] == today:
        return False
    samples = (samples + [[today, state['used']]])[-SAMPLES_KEPT:]
    type(webadmin).objects.filter(pk=webadmin.pk).update(disk_samples=samples)
    return True


def human(size):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024


def warnings(now=None):
    """What the status bar shows on every page: [{'level', 'text', 'title'}],
    empty when all is well."""
    webadmin = _settings()
    found = []
    state = scheduler(now, webadmin)
    if state['stopped']:
        names = ' and '.join(state['stopped'])
        found.append({'level': 'bad', 'icon': 'fa-clock', 'text': f'{names.capitalize()} stopped',
                      'title': f'The {names} has not called in for minutes: backups, checks and messages do not '
                               'run. See the status page.'})
    space = disk(webadmin)
    if space:
        level = 'bad' if space['percent'] >= DISK_BAD else 'warn' if space['percent'] >= DISK_WARN else ''
        days = space['days_left']
        if days is not None and days <= DAYS_BAD:
            level = 'bad'
        elif days is not None and days <= DAYS_WARN and not level:
            level = 'warn'
        if level:
            text = f'Disk {space["percent"]:.0f}% full'
            if days is not None and days <= DAYS_WARN:
                text += f', full in ~{days} day{"s" if days != 1 else ""}'
            found.append({'level': level, 'icon': 'fa-hard-drive', 'text': text,
                          'title': f'The disk holding the backups: {human(space["free"])} free of '
                                   f'{human(space["total"])}. See the status page.'})
    return found
