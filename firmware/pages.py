"""The rollout page in the "hero & timeline" layout of the object pages: what
is updated and how far it is, a tile per state, every device with its step,
and the timeline of every step of every device - including the reachability
checks after a reboot."""
from django.utils import timezone

from .models import RolloutEvent, RolloutTarget

CHIP = {'running': ('run', 'Running'), 'done': ('ok', 'Done'), 'failed': ('bad', 'Stopped by a failure'),
        'aborted': ('info', 'Aborted')}
TARGET_CHIP = {'waiting': 'info', 'skipped': 'info', 'done': 'ok', 'failed': 'bad'}


def rollout_page(rollout, can_abort=True):
    targets = list(rollout.targets.select_related('router', 'backup').order_by('position'))
    total = len(targets)
    counts = {state: sum(1 for t in targets if t.state == state) for state, _ in RolloutTarget.STATES}
    in_progress = sum(counts[s] for s in RolloutTarget.IN_PROGRESS)
    meta = [f'Started by {rollout.created_by_name or "unknown"}',
            f'{timezone.localtime(rollout.created):%Y-%m-%d %H:%M}']
    if total > 1:
        meta.append(f'{rollout.batch_size} at a time after the first')
    if rollout.then_bootloader:
        meta.append('then the bootloader firmware')
    if rollout.finished_at:
        meta.append(f'finished {timezone.localtime(rollout.finished_at):%H:%M}')
    actions = []
    if rollout.active and can_abort:
        actions.append({'label': 'Abort', 'icon': 'fa-hand-paper', 'post': '', 'word': 'abort', 'style': 'danger',
                        'confirm': 'Abort this update? Devices not started are skipped; a device already '
                                   'installing finishes on its own.'})
    hero = {'title': rollout.label, 'subtitle': f'{total} device{"s" if total != 1 else ""}',
            'chip': CHIP.get(rollout.state, ('info', rollout.get_state_display())),
            'ring': round(100 * counts['done'] / total) if total else 0, 'ring_caption': 'updated',
            'meta': meta, 'actions': actions}
    tiles = [{'value': counts['done'], 'label': 'Updated', 'state': 'ok'},
             {'value': in_progress, 'label': 'In progress', 'state': 'run'},
             {'value': counts['waiting'], 'label': 'Waiting', 'state': 'info'},
             {'value': counts['failed'] + counts['skipped'], 'label': 'Failed or not started',
              'state': 'bad' if counts['failed'] else 'info'}]
    rows = []
    for target in targets:
        version = ('the bootloader firmware' if rollout.source == 'bootloader'
                   else target.to_version or (rollout.image.version if rollout.image else ''))
        rows.append({'target': target, 'chip': (TARGET_CHIP.get(target.state, 'run'), target.get_state_display()),
                     'version': version, 'first': target.position == 0 and total > 1})
    events = RolloutEvent.objects.filter(target__rollout=rollout).order_by('-created', '-id')[:300]
    timeline = [{'when': e.created, 'text': e.text, 'state': e.state} for e in events]
    timeline.append({'when': rollout.created, 'text': f'Update started by {rollout.created_by_name or "unknown"}: '
                                                      f'{rollout.label}', 'state': 'info'})
    return {'hero': hero, 'tiles': tiles, 'rows': rows, 'timeline': timeline}
