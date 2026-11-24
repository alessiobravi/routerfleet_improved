from datetime import datetime, time, timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.db.models import Q
from django.utils import timezone

from backup_data.models import RouterBackup
from router_manager.models import Router, RouterStatus
from router_manager.templatetags.router_tags import update_state_counts

ALLOWED_DAYS = [3,5,7,10, 15, 30]  # Define allowed values

import os
import shutil
from django.conf import settings

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
    context = {'page_title': 'About', 'routerfleet_version': settings.ROUTERFLEET_RELEASE}
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
    from routerfleet_tools.models import WebadminSettings
    webadmin_settings, _ = WebadminSettings.objects.get_or_create(name='webadmin_settings')
    cron, monitoring = webadmin_settings.cron_last_run, webadmin_settings.monitoring_last_run
    cron_ok = cron is not None and now - cron < timedelta(minutes=5)
    monitoring_ok = monitoring is not None and now - monitoring < timedelta(minutes=10)
    stopped = [name for name, ok in (('scheduler', cron_ok), ('monitoring', monitoring_ok)) if not ok]
    docs = getattr(settings, 'PROJECT_REPOSITORY_URL', '').rstrip('/') + '/blob/main/docs/operations.md#troubleshooting'
    return _card('Scheduler & monitoring', 'Running' if not stopped else 'Stopped',
                 *(('ok', 'OK') if not stopped else ('bad', 'Act now')), docs,
                 f'scheduler {_ago(cron, now)} \u00b7 monitoring {_ago(monitoring, now)}', external=True)


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

    pct = storage['usage_percentage']
    storage_state = ('bad', 'Act now') if pct >= 90 else ('high', 'Needs attention soon') if pct >= 85 \
        else ('warn', 'Worth a look') if pct >= 75 else ('ok', 'OK')
    cards = [
        _card('Failed backups \u00b7 24h', error_24, *(('bad', 'Act now') if error_24 else ('ok', 'OK')),
              '/router/list/?state=backup_failed', _delta(error_24, error_before), backups['error_data'], '--rf-red'),
        _card('Successful backups \u00b7 24h', success_24, *(('ok', 'OK') if success_24 else ('info', 'None in the last 24 hours')),
              '/backup/backup_list/?since=24h', _delta(success_24, success_before), backups['success_data'], '--rf-teal'),
        _card('Backup queue', queue, *(('ok', 'Nothing waiting') if not queue else ('run', 'Running')),
              '/backup/backup_list/?type=queue', 'nothing waiting' if not queue else f'{queue} waiting to run'),
        _card('Backup storage', f'{pct}%', *storage_state, '/backup/backup_list/',
              f"{storage['used']} of {storage['total']}" + (f" \u00b7 {storage['storage_warning']}" if storage['storage_warning'] else '')),
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
                       *(('info', 'Update their router information') if versions['no_information'] else ('ok', 'OK')),
                       '/router/list/?state=no_information',
                       'update their router information' if versions['no_information'] else 'every device read'))
    cards.insert(4, _scheduler_card(now))
    return cards


@login_required
def view_status(request):
    context = {
        'page_title': 'Status',
        'cards': status_cards(),
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
