import os
import shutil
from datetime import datetime, time, timedelta

from django.conf import settings

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.db.models import Q
from django.utils import timezone

from backup_data.models import RouterBackup
from router_manager.models import Router, RouterStatus
from router_manager.templatetags.router_tags import update_state_counts

ALLOWED_DAYS = [3,5,7,10, 15, 30]  # Define allowed values

def human_readable_size(size):
    """Convert bytes to a human-readable format (KB, MB, GB)."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"  # For sizes larger than GB

def get_directory_statistics(directory_path):
    if not directory_path or not os.path.isdir(directory_path):
        return {
            "total": "0 B",
            "used": "0 B",
            "free": "0 B",
            "usage_percentage": 0,
            "storage_warning": "Invalid path"
        }

    # Get total disk usage
    total, used, free = shutil.disk_usage(directory_path)

    # The size of the backups: every file is read, which grows with the backups
    # kept - so it is reckoned at most every 10 minutes, not at every page
    from django.core.cache import cache
    key = f'rf-directory-size:{directory_path}'
    directory_used = cache.get(key)
    if directory_used is None:
        directory_used = sum(os.path.getsize(os.path.join(root, file))
                             for root, dirs, files in os.walk(directory_path) for file in files)
        cache.set(key, directory_used, 600)

    # Get sizes in a human-readable format
    total_human = human_readable_size(total)
    used_human = human_readable_size(directory_used)
    free_human = human_readable_size(free)

    # Calculate usage percentage
    usage_percentage = (directory_used / total) * 100 if total > 0 else 0

    # Determine storage warning
    storage_warning = ""
    if usage_percentage > 90:
        storage_warning = "Warning: Storage usage is above 90%!"
    elif usage_percentage > 75:
        storage_warning = "Caution: Storage usage is above 75%."

    return {
        "total": total_human,
        "used": used_human,
        "free": free_human,
        "usage_percentage": round(usage_percentage, 2) if total > 0 else 0,
        "storage_warning": storage_warning
    }


@login_required
def view_dashboard(request):
    # The status page is the home page
    return redirect('/status/')


@login_required
def view_about(request):
    # The supported platforms are the device types offered - read here, so the
    # list follows the types an installation has (shipped presets and its own)
    from router_manager.models import DeviceType
    context = {'page_title': 'About', 'routerfleet_version': settings.ROUTERFLEET_RELEASE,
               'device_types': DeviceType.objects.filter(enabled=True).order_by('-managed', 'name')}
    return render(request, 'dashboard/about.html', context=context)


def update_check_state():
    """What the update card shows: off, not checked yet, up to date, or the
    version there is to update to."""
    from routerfleet.versioning import from_number
    from routerfleet_tools.models import WebadminSettings
    from routerfleet_tools.views import check_enabled
    webadmin_settings, _ = WebadminSettings.objects.get_or_create(name='webadmin_settings')
    available = webadmin_settings.update_available and webadmin_settings.latest_version > settings.ROUTERFLEET_VERSION
    return {
        'enabled': check_enabled(),
        'checked': webadmin_settings.last_checked is not None,
        'available': available,
        'latest': from_number(webadmin_settings.latest_version) if available else settings.ROUTERFLEET_RELEASE,
        'releases_url': getattr(settings, 'PROJECT_REPOSITORY_URL', '').rstrip('/') + '/releases/latest',
    }


def _spark_points(values, width=110, height=34):
    """A sparkline as SVG polyline points, drawn on the server."""
    if not values or len(values) < 2:
        return ''
    top = max(max(values), 1)
    step = width / (len(values) - 1)
    return ' '.join(f'{i * step:.1f},{height - 3 - (height - 6) * v / top:.1f}' for i, v in enumerate(values))


def _delta(now, before, unit='previous 24 h'):
    if now == before:
        return f'same as the {unit}'
    sign = '+' if now > before else '\u2212'
    return f'{sign}{abs(now - before)} vs the {unit}'


def _card(title, value, state, chip, href, note='', spark=None, color='--rf-grey', external=False):
    return {'title': title, 'value': value, 'state': state, 'chip': chip, 'href': href, 'note': note,
            'spark': _spark_points(spark) if spark else '', 'color': color, 'external': external}


def _ago(moment, now):
    if moment is None:
        return 'never'
    seconds = int((now - moment).total_seconds())
    return f'{seconds} s ago' if seconds < 120 else f'{seconds // 60} min ago'


def _scheduler_card(now):
    """Whether the scheduler and monitoring containers are working. Either
    stopping is silent otherwise: backups, or reachability checks, just cease."""
    from routerfleet_tools import health
    state = health.scheduler(now)
    cron, monitoring, stopped = state['cron'], state['monitoring'], state['stopped']
    docs = getattr(settings, 'PROJECT_REPOSITORY_URL', '').rstrip('/') + '/blob/main/docs/operations.md#troubleshooting'
    return _card('Scheduler & monitoring', 'Running' if not stopped else 'Stopped',
                 *(('ok', 'OK') if not stopped else ('bad', 'Act now')), docs,
                 f'scheduler {_ago(cron, now)} \u00b7 monitoring {_ago(monitoring, now)}', external=True)


def _disk_card(storage):
    """How full the disk holding the backups is - the disk, not only the
    backups' share of it: a disk filled by something else stops backups just
    the same - and how fast it fills, from one sample a day."""
    from routerfleet_tools import health
    space = health.disk()
    if space is None:
        return _card('Backup disk', '?', 'info', 'Not readable', '/backup/backup_list/', 'the backup folder could not be read')
    pct, days = space['percent'], space['days_left']
    if pct >= health.DISK_BAD or (days is not None and days <= health.DAYS_BAD):
        state = ('bad', 'Act now')
    elif pct >= health.DISK_WARN or (days is not None and days <= health.DAYS_WARN):
        state = ('high', 'Needs attention soon')
    elif pct >= 75:
        state = ('warn', 'Worth a look')
    else:
        state = ('ok', 'OK')
    if space['per_day']:
        pace = f"fills ~{health.human(space['per_day'])} a day, full in ~{days} days"
    elif len(health._settings().disk_samples or []) < 2:
        pace = 'how fast it fills is known after a few days'
    else:
        pace = 'not filling'
    return _card('Backup disk', f'{pct:.0f}%', *state, '/backup/backup_list/',
                 f"{health.human(space['free'])} free of {health.human(space['total'])} \u00b7 backups "
                 f"{storage['used']} \u00b7 {pace}")


def status_cards():
    """The cards of the status page, in order. Each opens the list of exactly
    what it counts; a sparkline only where RouterFleet keeps the history."""
    now = timezone.now()
    day, two_days = now - timedelta(days=1), now - timedelta(days=2)
    errors = RouterBackup.objects.filter(error=True)
    successes = RouterBackup.objects.filter(success=True)
    error_24, error_before = errors.filter(created__gte=day).count(), errors.filter(created__gte=two_days, created__lt=day).count()
    success_24, success_before = (successes.filter(created__gte=day).count(),
                                  successes.filter(created__gte=two_days, created__lt=day).count())
    queue = RouterBackup.objects.filter(success=False, error=False).count()
    storage = get_directory_statistics(settings.MEDIA_ROOT)
    backups = backup_series(7)
    devices = online_series(7)
    online = RouterStatus.objects.filter(status_online=True, router__monitoring=True).count()
    offline = RouterStatus.objects.filter(status_online=False, router__monitoring=True).count()
    disabled = Router.objects.filter(enabled=False).count()
    unmonitored = Router.objects.filter(enabled=True, monitoring=False).count()
    total = Router.objects.count()
    versions = update_state_counts()

    storage_card = _disk_card(storage)
    cards = [
        _card('Failed backups \u00b7 24h', error_24, *(('bad', 'Act now') if error_24 else ('ok', 'OK')),
              '/router/list/?state=backup_failed', _delta(error_24, error_before), backups['error_data'], '--rf-red'),
        _card('Successful backups \u00b7 24h', success_24, *(('ok', 'OK') if success_24 else ('info', 'None in the last 24 hours')),
              '/backup/backup_list/?since=24h', _delta(success_24, success_before), backups['success_data'], '--rf-teal'),
        _card('Backup queue', queue, *(('ok', 'Nothing waiting') if not queue else ('run', 'Running')),
              '/backup/backup_list/?type=queue', 'nothing waiting' if not queue else f'{queue} waiting to run'),
        storage_card,
        _card('Devices online', f'{online} / {online + offline}', *(('ok', 'OK') if not offline else ('bad', 'Act now')),
              '/router/list/?state=online', f'{total} devices in all', devices['online_data'], '--rf-navy'),
        _card('Devices offline', offline, *(('ok', 'OK') if not offline else ('bad', 'Act now')),
              '/router/list/?state=offline', 'all monitored devices reachable' if not offline else 'not answering pings',
              devices['offline_data'] if any(devices['offline_data']) else None, '--rf-red'),
        _card('Disabled devices', disabled, *(('ok', 'None') if not disabled else ('info', 'Switched off on purpose')),
              '/router/list/?state=disabled', 'switched off on purpose' if disabled else 'every device enabled'),
        _card('Unmonitored devices', unmonitored, *(('ok', 'OK') if not unmonitored else ('warn', 'Worth a look')),
              '/router/list/?state=unmonitored', 'every device monitored' if not unmonitored else 'not watched for reachability'),
    ]
    if versions['update_waiting']:
        cards.append(_card('Missing OS updates', versions['update_waiting'], 'warn', 'Worth a look',
                           '/router/list/?state=update_waiting', 'a newer version is available for them'))
    if versions['up_to_date']:
        cards.append(_card('Devices up to date', versions['up_to_date'], 'ok', 'OK',
                           '/router/list/?state=up_to_date', 'running the version they could update to'))
    cards.append(_card('Without version info', versions['no_information'],
                       *(('info', 'Update their device information') if versions['no_information'] else ('ok', 'OK')),
                       '/router/list/?state=no_information',
                       'update their device information' if versions['no_information'] else 'every device read'))
    cards.insert(4, _scheduler_card(now))
    return cards


WEEK_SHOWN = 10   # names per column of "This week"; the rest are counted


def _duration(seconds):
    seconds = int(seconds)
    if seconds < 3600:
        return f'{max(seconds // 60, 1)} min'
    if seconds < 86400:
        return f'{seconds // 3600} h {seconds % 3600 // 60:02d} min'
    return f'{seconds // 86400} d {seconds % 86400 // 3600} h'


def this_week(now=None):
    """What happened in the last 7 days, per device: configuration changes,
    firmware updates, outages - what someone away for a while wants to know."""
    from django.db.models import Count, Max
    from firmware.models import RolloutTarget
    from monitoring.models import RouterDownTime
    now = now or timezone.now()
    since = now - timedelta(days=7)

    changed = list(RouterBackup.objects.filter(config_change_detected=True, created__gte=since)
                   .values('router__uuid', 'router__name').annotate(n=Count('id'), last=Max('created'))
                   .order_by('-last'))
    changes = [{'href': f"/router/details/?uuid={row['router__uuid']}", 'name': row['router__name'],
                'lines': [{'text': f"{row['n']} change{'s' if row['n'] != 1 else ''}"}]} for row in changed]

    # Per device, whether its firmware (the OS) and its bootloader were updated
    # this week - marked as the device list marks them; the versions are on
    # the device's page
    per_device = {}
    for target in (RolloutTarget.objects.filter(state='done', finished_at__gte=since)
                   .select_related('router', 'rollout').order_by('-finished_at')):
        entry = per_device.setdefault(target.router_id, {'router': target.router, 'os': None, 'boot': None})
        bootloader_only = target.rollout.source == 'bootloader'
        if not bootloader_only:
            entry['os'] = 'Firmware'
        if bootloader_only or (target.rollout.then_bootloader and target.to_firmware):
            entry['boot'] = 'Bootloader'
    updates = []
    for entry in per_device.values():
        # One line per kind, each with the mark the device list uses for it
        lines = [{'text': entry['os'], 'icon': 'rf-up-os', 'title': 'Firmware (OS) updated'}] if entry['os'] else []
        if entry['boot']:
            lines.append({'text': entry['boot'], 'icon': 'rf-up-boot', 'title': 'Bootloader updated'})
        updates.append({'href': f"/router/details/?uuid={entry['router'].uuid}", 'name': entry['router'].name,
                        'lines': lines or [{'text': 'Updated'}]})

    downtime = {}
    for outage in (RouterDownTime.objects.filter(start_time__gte=since).select_related('router')
                   .order_by('-start_time')):
        entry = downtime.setdefault(outage.router_id, {'uuid': outage.router.uuid, 'name': outage.router.name,
                                                       'count': 0, 'seconds': 0, 'open': False})
        entry['count'] += 1
        if outage.end_time:
            entry['seconds'] += outage.total_down_time or (outage.end_time - outage.start_time).total_seconds()
        else:
            entry['open'] = True
            entry['seconds'] += (now - outage.start_time).total_seconds()
    outages = []
    for entry in sorted(downtime.values(), key=lambda e: (not e['open'], -e['seconds'])):
        times = f"{entry['count']} outage{'s' if entry['count'] != 1 else ''}"
        detail = f"{times} \u00b7 {_duration(entry['seconds'])}" + (' \u00b7 down now' if entry['open'] else '')
        outages.append({'href': f"/router/availability/?uuid={entry['uuid']}", 'name': entry['name'],
                        'lines': [{'text': detail, 'bad': entry['open']}]})

    def column(title, icon, rows, empty, href):
        return {'title': title, 'icon': icon, 'count': len(rows), 'rows': rows[:WEEK_SHOWN],
                'more': max(len(rows) - WEEK_SHOWN, 0), 'empty': empty, 'href': href}
    return [
        column('Configuration changed', 'fa-pen-to-square', changes, 'No configuration changed.', '/backup/backup_list/'),
        column('Updated', 'fa-arrow-alt-circle-up', updates, 'No device updated.', '/system/firmware/'),
        column('Outages', 'fa-plug-circle-xmark', outages, 'No outage.', '/router/list/?filter_group=all'),
    ]


@login_required
def view_status(request):
    context = {
        'page_title': 'Status',
        'cards': status_cards(),
        'this_week': this_week(),
        'chart_days': [7, 15, 30],
        # Kept for anything that still reads the plain numbers
        'queue': RouterBackup.objects.filter(success=False, error=False).count(),
        'success_backup_last_24h': RouterBackup.objects.filter(success=True, created__gte=timezone.now() - timedelta(days=1)).count(),
        'error_backup_last_24h': RouterBackup.objects.filter(error=True, created__gte=timezone.now() - timedelta(days=1)).count(),
        'total_router_count': Router.objects.all().count(),
        'router_online_count': RouterStatus.objects.filter(status_online=True, router__monitoring=True).count(),
        'router_offline_count': RouterStatus.objects.filter(status_online=False, router__monitoring=True).count(),
        'router_disabled_count': Router.objects.filter(enabled=False).count(),
        'router_enabled_count': Router.objects.filter(enabled=True).count(),
        'router_not_monitored_count': Router.objects.filter(enabled=True, monitoring=False).count(),
        'update_state': update_state_counts(),
        'routerfleet_version': settings.ROUTERFLEET_RELEASE,
        'update_check': update_check_state(),
        # Secrets encrypted with another key than the configured one: said loudly
        'secrets_unreadable': __import__('routerlib.encryption', fromlist=['unreadable_seen']).unreadable_seen(),
    }
    return render(request, 'dashboard/status.html', context=context)


def _calendar_days(days):
    """The last `days` calendar days in the installation's timezone, oldest
    first, today last: [(date, start, end), ...]."""
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    result = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        start = timezone.make_aware(datetime.combine(day, time.min), tz)
        result.append((day, start, start + timedelta(days=1)))
    return result


def backup_series(days):
    """Successful and failed backups per calendar day."""
    calendar = _calendar_days(days)
    return {
        'dates': [day.isoformat() for day, _, _ in calendar],
        'success_data': [RouterBackup.objects.filter(created__gte=s, created__lt=e, success=True).count()
                         for _, s, e in calendar],
        'error_data': [RouterBackup.objects.filter(created__gte=s, created__lt=e, error=True).count()
                       for _, s, e in calendar],
    }


def online_series(days):
    """Devices online, offline and disabled at the end of each calendar day -
    today, as of now - from what RouterFleet recorded:

    - disabled: whether the device was enabled at that moment, from when it was
      last enabled or disabled (Router.enabled_changed_at);
    - offline: whether an offline period the monitoring recorded covers that
      moment (RouterDownTime); a monitored, enabled device outside one was
      online.

    A device counts from the day it was added, in exactly one of the three; an
    enabled device that is not monitored has no status and is in none."""
    from monitoring.models import RouterDownTime
    now = timezone.now()
    calendar = _calendar_days(days)
    first_start = calendar[0][1]
    routers = list(Router.objects.values('id', 'enabled', 'monitoring', 'created', 'enabled_changed_at'))
    periods = {}
    for down in RouterDownTime.objects.filter(start_time__isnull=False, start_time__lte=now) \
            .filter(Q(end_time__isnull=True) | Q(end_time__gte=first_start)).values('router_id', 'start_time', 'end_time'):
        periods.setdefault(down['router_id'], []).append((down['start_time'], down['end_time']))
    online_data, offline_data, disabled_data = [], [], []
    for _, _start, end in calendar:
        moment = min(end, now)
        online = offline = disabled = 0
        for router in routers:
            if router['created'] and router['created'] > moment:
                continue
            changed = router['enabled_changed_at']
            enabled = router['enabled'] if changed is None or changed <= moment else not router['enabled']
            if not enabled:
                disabled += 1
            elif router['monitoring']:
                down = any(start <= moment and (finish is None or finish > moment)
                           for start, finish in periods.get(router['id'], ()))
                if down:
                    offline += 1
                else:
                    online += 1
        online_data.append(online)
        offline_data.append(offline)
        disabled_data.append(disabled)
    return {'dates': [day.isoformat() for day, _, _ in calendar], 'online_data': online_data,
            'offline_data': offline_data, 'disabled_data': disabled_data}


def _days_parameter(request):
    try:
        days = int(request.GET.get('days', 7))
    except ValueError:
        return None
    return days if days in ALLOWED_DAYS else None


@login_required
def backup_statistics_data(request):
    days = _days_parameter(request)
    if days is None:
        return HttpResponseBadRequest("Invalid 'days' parameter")
    return JsonResponse(backup_series(days))


@login_required
def router_status_data(request):
    days = _days_parameter(request)
    if days is None:
        return HttpResponseBadRequest("Invalid 'days' parameter. Must be one of: " + ', '.join(map(str, ALLOWED_DAYS)))
    return JsonResponse(online_series(days))


@login_required
def view_changes(request):
    """When the data the pages show last changed - the devices, their
    information and state, backups, groups, Fleet Commander jobs and tasks.
    An open page asks every half minute and refreshes itself when it moves,
    so a change made elsewhere (another browser, the scheduler) shows."""
    from django.db.models import Max
    from fleet_commander.models import CommandJob, CommandTask
    from router_manager.models import RouterGroup, RouterInformation
    stamps = [model.objects.aggregate(newest=Max('updated'))['newest']
              for model in (Router, RouterInformation, RouterStatus, RouterBackup, RouterGroup, CommandJob, CommandTask)]
    newest = max(filter(None, stamps), default=None)
    response = JsonResponse({'stamp': newest.isoformat() if newest else ''})
    response['Cache-Control'] = 'no-store'
    return response


@login_required
def view_whats_new(request):
    """What's new: what the releases ahead of this installation bring - and
    what the one that runs brought - read in the app, laid out like About."""
    from routerfleet_tools.release_notes import notes
    from user_manager.models import UserAcl
    state = update_check_state()
    context = {'page_title': "What's new", 'state': state, 'notes': notes(),
               'current': settings.ROUTERFLEET_RELEASE,
               'repository': getattr(settings, 'PROJECT_REPOSITORY_URL', '').rstrip('/'),
               'is_administrator': UserAcl.objects.filter(user=request.user, user_level__gte=50).exists()}
    return render(request, 'dashboard/whats_new.html', context)
