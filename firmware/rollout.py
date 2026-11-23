"""Running a firmware rollout, one step per device per scheduler call.

    a file of the repository:
    waiting -> backing_up -> uploading -> verifying -> installing -> rebooting -> done
    an online update (the device downloads the release itself):
    waiting -> backing_up -> installing -> rebooting -> done

- backing_up: a fresh backup through the normal backup pipeline; the rollout
  waits for it to succeed.
- uploading: the image is copied to the device by SCP, to the path the device
  type gives.
- verifying: the device type's "firmware - verify the upload" prints the
  file's SHA-256 or size; anything but the repository's value stops here,
  before anything is installed.
- installing: "firmware - install". A connection that drops while it runs is
  expected - it usually ends in a reboot.
- rebooting: the inventory is read again until the device reports the new
  version, or the deadline passes.

The first device (the canary) goes alone; the others follow, batch_size at a
time. Any failure stops the rollout: devices not started yet are skipped.
A device is never worked on by two scheduler calls at once (lock).
"""
import logging
import os
import re

from django.db.models import Q
from django.utils import timezone

from .models import FirmwareRollout, RolloutTarget

logger = logging.getLogger(__name__)

BACKUP_WAIT = timezone.timedelta(minutes=30)
REBOOT_WAIT = timezone.timedelta(minutes=20)
UPLOAD_WAIT = timezone.timedelta(minutes=40)
REBOOT_GRACE = timezone.timedelta(seconds=60)   # not asked before the device had time to go down
LOCK_STALE = timezone.timedelta(minutes=35)     # a step interrupted by a restart is not waited for forever
SHA256 = re.compile(r'\b([0-9a-fA-F]{64})\b')
NUMBER = re.compile(r'\b(\d+)\b')


class StepFailed(Exception):
    pass


def online(target):
    return target.rollout.source == 'online'


def expected_version(target):
    """The version the device must come back with."""
    return target.to_version if online(target) else target.rollout.image.version


def firmware_values(target):
    if online(target):
        return {'channel': target.rollout.channel, 'firmware_version': target.to_version}
    image = target.rollout.image
    device_type = target.router.device_type
    name = image.filename
    path = (device_type.firmware_upload_path or '/{{ firmware_name }}').replace('{{ firmware_name }}', name)
    return {'firmware_name': name, 'firmware_path': path, 'firmware_version': image.version}


def install_key(target):
    return 'firmware_online_update' if online(target) else 'firmware_install'


def _session(router):
    from routerlib import device_session
    return device_session.connect_router(router)


def _run(session, command):
    stdin, stdout, stderr = session.exec_command(command)  # nosec B601 - a system command of the device type
    return stdout.read().decode('utf-8', errors='replace')


def _fail(target, message):
    now = timezone.now()
    target.state, target.message, target.finished_at = 'failed', message[:2000], now
    target.save(update_fields=['state', 'message', 'finished_at', 'updated'])
    rollout = target.rollout
    rollout.targets.filter(state='waiting').update(state='skipped', message='Not started: the rollout stopped.')
    rollout.state = 'failed'
    rollout.message = f'Stopped: {target.router.name} - {message}'[:500]
    rollout.finished_at = now
    rollout.save(update_fields=['state', 'message', 'finished_at', 'updated'])


def _move(target, state, message='', deadline=None):
    target.state, target.message = state, message
    if deadline is not None:
        target.deadline = deadline
    target.save(update_fields=['state', 'message', 'deadline', 'updated'])


def check_target(target):
    """Why this device can not take this update, or ''."""
    router, image = target.router, target.rollout.image
    device_type = router.device_type
    if device_type is None or not device_type.managed:
        return 'the device is not reached over SSH'
    from fleet_commander import system_commands
    if online(target):
        if not target.to_version:
            return 'no version to update to'
        if not system_commands.lines('firmware_online_update', router.router_type, **firmware_values(target)):
            return f'{device_type.name} has no online update procedure'
        return ''
    if router.router_type != image.device_type.slug:
        return f'the image is for {image.device_type.name}, the device is {device_type.name}'
    if not image.matches_router(router):
        return 'the image is not for this model or architecture'
    if not system_commands.lines('firmware_install', router.router_type, **firmware_values(target)):
        return f'{device_type.name} has no firmware install procedure'
    return ''


def step_waiting(target):
    problem = check_target(target)
    if problem:
        raise StepFailed(problem)
    from backup_data.models import RouterBackup
    from router_manager.views import create_instant_backup
    info = getattr(target.router, 'routerinformation', None)
    target.from_version = (getattr(info, 'os_version', '') or '')[:100]
    error = create_instant_backup(target.router)
    backup = RouterBackup.objects.filter(router=target.router, success=False, error=False) \
        .order_by('-created').first()
    if error and backup is None:
        raise StepFailed(f'no pre-update backup: {error}')
    target.backup, target.started_at = backup, timezone.now()
    target.save(update_fields=['backup', 'started_at', 'from_version', 'updated'])
    _move(target, 'backing_up', 'Taking a backup before the update.', timezone.now() + BACKUP_WAIT)


def step_backing_up(target):
    backup = target.backup
    if backup is None:
        raise StepFailed('the pre-update backup disappeared')
    backup.refresh_from_db()
    if backup.success and online(target):
        _move(target, 'installing', f'Backup taken. Starting the online update to {target.to_version}.')
    elif backup.success:
        _move(target, 'uploading', 'Backup taken. Uploading the firmware.', timezone.now() + UPLOAD_WAIT)
    elif backup.error:
        raise StepFailed('the pre-update backup failed: ' + (backup.error_message or 'see the backup'))
    elif target.deadline and timezone.now() > target.deadline:
        raise StepFailed('the pre-update backup did not finish within 30 minutes')


def step_uploading(target):
    from scp import SCPClient
    image = target.rollout.image
    # An upload cut off by a restart is tried again, but not past its deadline
    if target.deadline and timezone.now() > target.deadline:
        raise StepFailed('the upload did not complete within 40 minutes')
    values = firmware_values(target)
    session = _session(target.router)
    try:
        SCPClient(session.get_transport(), socket_timeout=300).put(image.file.path, values['firmware_path'])
    finally:
        session.close()
    _move(target, 'verifying', f'Uploaded to {values["firmware_path"]}.')


def step_verifying(target):
    from fleet_commander import system_commands
    image = target.rollout.image
    kind = target.router.device_type.firmware_verify
    commands = system_commands.lines('firmware_verify', target.router.router_type, **firmware_values(target))
    if kind == 'none' or not commands:
        _move(target, 'installing', 'Upload not verified (the device type does not check it).')
        return
    session = _session(target.router)
    try:
        output = '\n'.join(_run(session, command) for command in commands)
    finally:
        session.close()
    if kind == 'sha256':
        found = [m.lower() for m in SHA256.findall(output)]
        if image.sha256.lower() not in found:
            raise StepFailed('the uploaded file is not intact: its SHA-256 does not match the repository')
    else:
        if str(image.size) not in NUMBER.findall(output.replace(' ', '')):
            raise StepFailed(f'the uploaded file is not intact: its size is not {image.size} bytes')
    _move(target, 'installing', 'Upload verified.')


def step_installing(target):
    from fleet_commander import system_commands
    commands = system_commands.lines(install_key(target), target.router.router_type, **firmware_values(target))
    session = _session(target.router)
    try:
        for command in commands:
            try:
                _run(session, command)
            except Exception as error:   # the device went down to install: expected
                logger.info(f'Firmware install on {target.router.name}: connection ended ({error})')
                break
    finally:
        try:
            session.close()
        except Exception:
            pass
    _move(target, 'rebooting', 'Installing; waiting for the device to come back with the new version.',
          timezone.now() + REBOOT_WAIT)


def step_rebooting(target):
    from router_manager.models import RouterInformation
    from router_manager.templatetags.router_tags import compare_versions
    from routerlib.router_functions import get_router_information
    from .options import same_version
    expected = expected_version(target)
    if timezone.now() - target.updated < REBOOT_GRACE and target.message.startswith('Installing'):
        return
    info, _ = RouterInformation.objects.get_or_create(router=target.router)
    ok, error = get_router_information(info)
    if ok:
        info.refresh_from_db()
        field = target.router.device_type.vendor_version_field or 'os_version'
        running = getattr(info, field, '') or info.os_version or ''
        # A file of the repository: the same version number; an online update:
        # exactly the version offered (a beta is not its final release)
        if same_version(running, expected) if online(target) else compare_versions(running, expected) == 0:
            target.state, target.finished_at = 'done', timezone.now()
            target.message = f'Updated: now running {running}.'
            target.save(update_fields=['state', 'finished_at', 'message', 'updated'])
            return
        last = f'it reports {running or "no version"}'
    else:
        last = error or 'not reachable yet'
    if target.deadline and timezone.now() > target.deadline:
        raise StepFailed(f'did not come back with {expected} within 20 minutes ({last})')
    target.message = f'Waiting for {expected}: {last}.'
    target.save(update_fields=['message', 'updated'])


STEPS = {'waiting': step_waiting, 'backing_up': step_backing_up, 'uploading': step_uploading,
         'verifying': step_verifying, 'installing': step_installing, 'rebooting': step_rebooting}


def step(target):
    """One step of one device, under its lock."""
    now = timezone.now()
    locked = RolloutTarget.objects.filter(pk=target.pk).filter(
        Q(lock__isnull=True) | Q(lock__lt=now - LOCK_STALE)).update(lock=now)
    if locked != 1:
        return
    target.refresh_from_db()
    try:
        handler = STEPS.get(target.state)
        if handler is not None:
            handler(target)
    except StepFailed as error:
        _fail(target, str(error))
    except Exception as error:
        logger.warning(f'Firmware rollout step failed for {target.router.name}: {error}')
        _fail(target, f'{target.get_state_display().lower()}: {error}')
    finally:
        RolloutTarget.objects.filter(pk=target.pk).update(lock=None)


def advance(rollout):
    targets = list(rollout.targets.select_related('router').order_by('position'))
    if not targets:
        return
    canary = targets[0]
    if canary.state != 'done':
        candidates = [canary] if canary.state not in ('failed', 'skipped') else []
    else:
        active = [t for t in targets if t.state in RolloutTarget.IN_PROGRESS]
        free = max(rollout.batch_size - len(active), 0)
        candidates = active + [t for t in targets if t.state == 'waiting'][:free]
    for target in candidates:
        step(target)
        rollout.refresh_from_db()
        if rollout.state != 'running':
            return
    if not rollout.targets.exclude(state__in=('done', 'skipped')).exists():
        rollout.state, rollout.finished_at = 'done', timezone.now()
        rollout.message = f'{rollout.targets.filter(state="done").count()} devices updated.'
        rollout.save(update_fields=['state', 'finished_at', 'message', 'updated'])


def advance_all():
    for rollout in FirmwareRollout.objects.filter(state='running'):
        try:
            advance(rollout)
        except Exception as error:   # one rollout never stops the others
            logger.warning(f'Firmware rollout {rollout.pk} could not advance: {error}')


def abort(rollout, user=None):
    if rollout.state != 'running':
        return
    rollout.targets.filter(state='waiting').update(state='skipped', message='Not started: the rollout was aborted.')
    rollout.state, rollout.finished_at = 'aborted', timezone.now()
    who = getattr(user, 'username', '') if user else ''
    rollout.message = f'Aborted{" by " + who if who else ""}. A device already installing finishes on its own.'
    rollout.save(update_fields=['state', 'finished_at', 'message', 'updated'])


def remove_file(image):
    """Deletes the stored file of an image that is removed from the repository."""
    try:
        if image.file and os.path.exists(image.file.path):
            os.remove(image.file.path)
    except (OSError, ValueError):
        logger.warning(f'Firmware file of {image} could not be removed')
