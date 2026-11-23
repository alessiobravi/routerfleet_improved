"""The "Hero & timeline" layout of object pages: what each object page shows on
top - a hero (progress ring or state icon, title, state, key facts, actions),
four tiles, and a timeline of what happened - computed from what RouterFleet
already records. One shape for every object, rendered by
templates/includes/object_hero.html.

    hero     {'title', 'subtitle', 'chip': (state, label), 'ring': percent | None,
              'icon': fa-class | None, 'meta': [text], 'actions': [action]}
    action   {'label', 'icon', 'href' | 'post' + 'confirm' + 'word', 'style': 'danger' | 'primary' | ''}
             post: sent as a POST after the confirm question with a small sum
    tiles    [{'value', 'label', 'state': ok | run | wait | bad | info, 'href'}]
    timeline [{'when': datetime, 'text', 'state'}], newest first
"""
import re

from django.utils import timezone

TIMELINE_LENGTH = 12


def _ago(moment):
    if not moment:
        return 'never'
    seconds = int((timezone.now() - moment).total_seconds())
    if seconds < 90:
        return 'just now'
    for unit, size in (('day', 86400), ('hour', 3600), ('minute', 60)):
        if seconds >= size:
            count = seconds // size
            return f'{count} {unit}{"s" if count != 1 else ""} ago'
    return 'just now'


def _newest(events):
    """Newest first. Events of the same moment - a task that started and
    finished within the same minute - keep the order they happened in, so the
    later one is above: they are listed in that order by the builders."""
    indexed = [(e['when'], i, e) for i, e in enumerate(events) if e['when']]
    return [e for _, _, e in sorted(indexed, key=lambda x: (x[0], x[1]), reverse=True)][:TIMELINE_LENGTH]


# --- Fleet Commander job ----------------------------------------------------------

def job_page(job, tasks):
    running = [t for t in tasks if t.status == 'pending' and t.started_at]
    waiting = [t for t in tasks if t.status == 'pending' and not t.started_at]
    succeeded = [t for t in tasks if t.status == 'success']
    failed = [t for t in tasks if t.status == 'error']
    aborted = [t for t in tasks if t.status == 'aborted']
    if not job.completed:
        chip = ('run', 'Running')
    elif failed:
        chip = ('bad', 'Completed with failures')
    elif aborted and not succeeded:
        chip = ('info', 'Aborted')
    else:
        chip = ('ok', 'Completed')
    meta = [f'{job.get_exec_source_display()}{" · " + job.user_source_name if job.user_source_name else ""}',
            f'Created {timezone.localtime(job.created):%Y-%m-%d %H:%M}']
    if job.completed:
        meta.append(f'Finished {timezone.localtime(job.completed):%H:%M}')
    if job.command.variants.filter(verify_payload__gt='').exists():
        meta.append('Result verified on the device')
    actions = []
    if not job.completed:
        actions.append({'label': 'Abort', 'icon': 'fa-hand-paper', 'post': f'/fleet_commander/job/abort/?uuid={job.uuid}', 'confirm': 'Abort this job? A command already running on a device finishes; nothing after it runs.', 'word': 'abort', 'style': 'danger'})
    hero = {'title': job.command.name, 'subtitle': f'job #{job.id}', 'chip': chip,
            'ring': job.progress_percentage, 'meta': meta, 'actions': actions}
    tiles = [
        {'value': len(succeeded), 'label': 'Succeeded', 'state': 'ok'},
        {'value': len(running), 'label': 'Running', 'state': 'run'},
        {'value': len(waiting), 'label': 'Waiting', 'state': 'wait'},
        {'value': len(failed) + len(aborted), 'label': 'Failed' if not aborted else 'Failed or aborted', 'state': 'bad'},
    ]
    events = [{'when': job.created, 'text': f'Job created{" by " + job.user_source_name if job.user_source_name else ""} '
                                            f'for {len(tasks)} device{"s" if len(tasks) != 1 else ""}', 'state': ''}]
    for task in tasks:
        name = task.router.name if task.router else (task.router_name or 'a device')
        if task.started_at:
            events.append({'when': task.started_at, 'text': f'{name} started', 'state': 'run'})
        if task.finished_at:
            text = {'success': f'{name} succeeded{" and was verified" if task.verified else ""}',
                    'error': f'{name} failed{": " + task.error_message.splitlines()[0][:80] if task.error_message else ""}',
                    'aborted': f'{name} aborted'}.get(task.status, f'{name} finished')
            events.append({'when': task.finished_at, 'text': text,
                           'state': {'success': 'ok', 'error': 'bad'}.get(task.status, '')})
    if job.completed:
        events.append({'when': job.completed, 'text': 'Job completed', 'state': chip[0]})
    return {'hero': hero, 'tiles': tiles, 'timeline': _newest(events)}


def task_state(task):
    if task.status == 'success':
        return ('ok', 'Succeeded')
    if task.status == 'error':
        return ('bad', 'Failed')
    if task.status == 'aborted':
        return ('info', 'Aborted')
    if task.started_at:
        return ('run', 'Verifying' if task.verify_deadline and task.payload_executed else 'Running')
    return ('wait', 'Waiting')


# --- Fleet Commander task ---------------------------------------------------------

def task_page(task):
    chip = task_state(task)
    name = task.router.name if task.router else (task.router_name or str(task.router_uuid))
    meta = [f'{task.job.command.name} · job #{task.job.id}']
    if task.router:
        meta.append(f'{task.router.address} · {task.router.get_router_type_display()}')
    actions = [{'label': 'Job', 'icon': 'fa-list', 'href': f'/fleet_commander/job/details/?uuid={task.job.uuid}', 'style': ''}]
    if task.status == 'pending':
        actions.append({'label': 'Abort', 'icon': 'fa-hand-paper', 'post': f'/fleet_commander/task/abort/?uuid={task.uuid}', 'confirm': 'Abort this task?', 'word': 'abort', 'style': 'danger'})
    icon = {'ok': 'fa-check', 'bad': 'fa-times', 'info': 'fa-hand-paper', 'run': 'fa-circle-notch fa-spin',
            'wait': 'fa-hourglass-half'}[chip[0]]
    hero = {'title': name, 'subtitle': 'task', 'chip': chip, 'ring': None, 'icon': icon, 'meta': meta, 'actions': actions}
    verification = ('verified' if task.verified else
                    f'{task.verification_attempts} attempt{"s" if task.verification_attempts != 1 else ""}'
                    if task.verify_deadline else 'none')
    tiles = [
        {'value': task.retry_count, 'label': 'Runs', 'state': 'info'},
        {'value': timezone.localtime(task.started_at).strftime('%H:%M') if task.started_at else '—', 'label': 'Started', 'state': 'run'},
        {'value': timezone.localtime(task.finished_at).strftime('%H:%M') if task.finished_at else '—', 'label': 'Finished',
         'state': chip[0] if task.finished_at else 'wait'},
        {'value': verification, 'label': 'Verification', 'state': 'ok' if task.verified else 'info'},
    ]
    events = [{'when': task.job.created, 'text': 'Queued with the job', 'state': ''},
              {'when': task.started_at, 'text': 'Started on the device', 'state': 'run'}]
    if task.payload_executed and task.verify_deadline:
        events.append({'when': task.started_at, 'text': 'Commands sent; verifying the result', 'state': 'run'})
    if task.finished_at:
        events.append({'when': task.finished_at, 'text': {'ok': 'Succeeded', 'bad': 'Failed', 'info': 'Aborted'}.get(chip[0], 'Finished'),
                       'state': chip[0]})
    return {'hero': hero, 'tiles': tiles, 'timeline': _newest(events)}


# --- Device -----------------------------------------------------------------------

def router_page(router, router_status, router_information, availability, can_execute, can_console=False,
                can_update_firmware=False):
    from backup_data.models import RouterBackup
    from monitoring.models import RouterDownTime
    from router_manager.templatetags.router_tags import os_version_state
    if not router.enabled:
        chip = ('purple', 'Disabled')
    elif not router.monitoring:
        chip = ('info', 'Not monitored')
    elif router_status.status_online:
        chip = ('ok', 'Online')
    else:
        chip = ('bad', 'Offline')
    groups = ', '.join(router.routergroup_set.values_list('name', flat=True))
    meta = [f'{router.address}{":" + str(router.port) if router.port and router.port != 22 else ""}',
            router.get_router_type_display()]
    if groups:
        meta.append(groups)
    actions = [{'label': 'Edit', 'icon': 'fa-edit', 'href': f'/router/manage/?uuid={router.uuid}', 'style': ''}]
    if can_console and router.is_managed:
        actions.append({'label': 'Console', 'icon': 'fa-keyboard', 'href': f'/router/console/?uuid={router.uuid}', 'style': ''})
    if can_execute and router.is_managed:
        actions.append({'label': 'Execute command', 'icon': 'fa-terminal',
                        'href': f'/fleet_commander/router/execute/?uuid={router.uuid}', 'style': 'primary'})
    if can_update_firmware and router.is_managed and router.enabled and getattr(router_information, 'update_available', False):
        actions.append({'label': 'Update', 'icon': 'fa-arrow-alt-circle-up',
                        'href': f'/system/firmware/update/?uuid={router.uuid}', 'style': 'primary'})
    hero = {'title': router.name, 'subtitle': '', 'chip': chip,
            'ring': round(availability) if router.monitoring else None,
            'ring_caption': 'availability, 7 days', 'icon': 'fa-network-wired', 'meta': meta, 'actions': actions}

    week = timezone.now() - timezone.timedelta(days=7)
    backups = RouterBackup.objects.filter(router=router)
    failed_week = backups.filter(error=True, created__gte=week).count()
    version = os_version_state(router_information) if router_information else {'css_class': ''}
    running = (getattr(router_information, 'os_version', '') or '') if router_information else ''
    offered = (getattr(router_information, 'available_version', '') or '') if router_information else ''
    # The version itself, and what the device is offered beside it
    version_state = {
        'text-success': (running, 'ok', 'OS version, up to date'),
        'text-danger': (running, 'bad', f'OS version, {offered} available'),
        'text-warning': (running or 'Unknown', 'wait', 'OS version, update not checked'),
    }.get(version.get('css_class'), (running or '—', 'info', 'OS version'))
    downtime = sum(d.total_down_time for d in RouterDownTime.objects.filter(router=router, start_time__gte=week))
    newest = backups.filter(success=True).order_by('-created').values_list('finish_time', 'created').first()
    last_backup = max(filter(None, [router_status.last_backup] + list(newest or [])), default=None)
    tiles = [
        {'value': _ago(last_backup), 'label': 'Last backup',
         'state': 'ok' if last_backup and last_backup > timezone.now() - timezone.timedelta(days=2) else 'wait'},
        {'value': failed_week, 'label': 'Failed backups, 7 days', 'state': 'bad' if failed_week else 'ok'},
        {'value': version_state[0], 'label': version_state[2], 'state': version_state[1]},
        {'value': f'{downtime // 3600}h {downtime % 3600 // 60}m' if downtime else 'none', 'label': 'Downtime, 7 days',
         'state': 'bad' if downtime else 'ok'},
    ]
    events = []
    for backup in backups.order_by('-created')[:8]:
        if backup.success:
            text = f'{backup.get_schedule_type_display()} backup ' + ('unchanged' if backup.same_as_id else
                                                                       'stored, configuration changed' if backup.config_change_detected else 'stored')
            events.append({'when': backup.finish_time or backup.created, 'text': text, 'state': 'ok'})
        elif backup.error:
            events.append({'when': backup.created, 'text': f'{backup.get_schedule_type_display()} backup failed', 'state': 'bad'})
        else:
            events.append({'when': backup.created, 'text': f'{backup.get_schedule_type_display()} backup in progress', 'state': 'run'})
    for task in router.commandtask_set.select_related('job__command').order_by('-created')[:6]:
        state = task_state(task)
        events.append({'when': task.finished_at or task.started_at or task.created,
                       'text': f'Command "{task.job.command.name}": {state[1].lower()}',
                       'state': {'ok': 'ok', 'bad': 'bad', 'run': 'run'}.get(state[0], '')})
    for console in router.console_sessions.filter(connected_at__isnull=False).order_by('-connected_at')[:5]:
        events.append({'when': console.connected_at, 'text': f'Console opened by {console.user_name} '
                                                             f'({console.get_transport_display()}{", " + console.username if console.username else ""})',
                       'state': 'run'})
        if console.ended_at:
            events.append({'when': console.ended_at, 'text': f'Console closed: {console.end_reason}', 'state': ''})
    for down in RouterDownTime.objects.filter(router=router).order_by('-start_time')[:6]:
        events.append({'when': down.start_time, 'text': 'Went offline', 'state': 'bad'})
        if down.end_time:
            events.append({'when': down.end_time, 'text': 'Back online', 'state': 'ok'})
    return {'hero': hero, 'tiles': tiles, 'timeline': _newest(events)}


# --- Backup -----------------------------------------------------------------------

CONSOLE_LINE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.+)$')


def backup_page(backup):
    import datetime
    if backup.success:
        chip = ('ok', 'Unchanged' if backup.same_as_id else 'Stored')
        ring = 100
    elif backup.error:
        chip = ('bad', 'Failed')
        ring = None
    elif backup.backup_pending_retrieval:
        chip, ring = ('run', 'Downloading'), 66
    else:
        chip, ring = ('wait' if not backup.task_console_output else 'run', 'Queued' if not backup.task_console_output else 'Creating'), 33
    content = backup.content
    size = len((content.backup_text or '').encode())
    try:
        binary = content.backup_binary.size if content.backup_binary else 0
    except (OSError, ValueError):
        binary = 0
    from django.template.defaultfilters import filesizeformat
    meta = [f'{backup.get_schedule_type_display()} backup', f'{timezone.localtime(backup.created):%Y-%m-%d %H:%M}']
    if backup.finish_time:
        meta.append(f'took {int((backup.finish_time - (backup.schedule_time or backup.created)).total_seconds())} s')
    actions = []
    if content.backup_text or content.backup_binary:
        actions.append({'label': 'Download', 'icon': 'fa-download', 'href': f'/backup/download/?uuid={backup.uuid}&type=text', 'style': ''})
    hero = {'title': backup.router.name, 'subtitle': f'backup #{backup.id}', 'chip': chip, 'ring': ring,
            'ring_caption': None, 'icon': 'fa-times' if backup.error else 'fa-hdd', 'meta': meta, 'actions': actions}
    lines = len((content.backup_text or '').splitlines())
    tiles = [
        {'value': f'{lines} lines' if lines else '—', 'label': f'Configuration, {filesizeformat(size)}', 'state': 'info'},
        {'value': filesizeformat(binary) if binary else '—', 'label': 'Binary backup', 'state': 'info'},
        {'value': 'Unchanged' if backup.same_as_id else ('Changed' if backup.config_change_detected else 'No change'),
         'label': 'Compared with the previous', 'state': 'run' if backup.config_change_detected else 'ok'},
        {'value': backup.retry_count, 'label': 'Retries', 'state': 'bad' if backup.retry_count else 'ok'},
    ]
    events = [{'when': backup.created, 'text': 'Backup queued', 'state': ''}]
    for raw in (backup.task_console_output or '').splitlines():
        match = CONSOLE_LINE.match(raw)
        if not match:
            continue
        text = match.group(2).strip()
        if not (text.startswith('$ ') or text.startswith('===') or text.startswith('Backup failed')):
            continue
        try:
            when = timezone.make_aware(datetime.datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S'))
        except ValueError:
            continue
        events.append({'when': when, 'text': text.strip('= ').replace('$ ', 'Sent: ', 1)[:120],
                       'state': 'bad' if text.startswith('Backup failed') else 'run'})
    if backup.finish_time:
        events.append({'when': backup.finish_time, 'text': f'Backup {chip[1].lower()}', 'state': chip[0]})
    return {'hero': hero, 'tiles': tiles, 'timeline': _newest(events)}
