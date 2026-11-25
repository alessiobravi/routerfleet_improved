"""Neighbours into DiscoveredDevice: merged by MAC address when there is one,
else by address and name; what is seen again is updated, never duplicated."""
import datetime
import json
import logging
import os

from django.conf import settings
from django.utils import timezone

from .parsers import normal_mac

logger = logging.getLogger(__name__)
LAN = 'RouterFleet LAN'


def key_of(record):
    mac = normal_mac(record.get('mac', ''))
    if mac:
        return f'mac:{mac}'
    return f"addr:{(record.get('address') or '').lower()}|{(record.get('identity') or '').lower()}"[:160]


def store(records, seen_by='', router=None):
    """Saves what was seen; returns how many devices."""
    from .models import DiscoveredDevice
    now = timezone.now()
    count = 0
    for record in records:
        key = key_of(record)
        if key in ('addr:|',):
            continue
        # When it was really heard: the listener says so (epoch seconds); a
        # neighbour table read now is seen now
        seen_at = now
        try:
            if record.get('last_seen'):
                seen_at = min(now, datetime.datetime.fromtimestamp(float(record['last_seen']), tz=datetime.timezone.utc))
        except (TypeError, ValueError, OverflowError, OSError):
            seen_at = now
        device, created = DiscoveredDevice.objects.get_or_create(key=key, defaults={'last_seen': seen_at})
        for field in ('identity', 'address', 'mac', 'platform', 'model', 'version', 'interface', 'remote_interface'):
            value = (record.get(field) or '').strip()
            if value:
                setattr(device, field, value[:DiscoveredDevice._meta.get_field(field).max_length])
        known = {p for p in device.protocols.split(',') if p} | set(record.get('protocols') or ())
        device.protocols = ','.join(sorted(known))[:40]
        device.seen_by = seen_by[:200]
        device.seen_by_router = router
        if created or seen_at >= device.last_seen:
            device.last_seen = seen_at
        device.save()
        count += 1
    return count


def read_neighbours(session, router):
    """The neighbour tables of one managed device, through its system command
    "neighbours"; an empty command (or output nobody recognises) finds none."""
    from fleet_commander import system_commands
    from .parsers import parse
    output = []
    for line in system_commands.lines('neighbors', router.router_type):
        try:
            from routerlib.device_io import read_output
            stdin, stdout, stderr = session.exec_command(line)
            output.append(read_output(stdout, 4 * 1024 * 1024))
        except Exception as error:   # noqa: BLE001 - one command failing (lldpd not installed) is not the others'
            logger.info(f'Neighbour command on {router.name} failed: {error}')
    if not output:
        return 0
    return store(parse('\n'.join(output)), seen_by=router.name, router=router)


def import_listener_file(path=None):
    """What the optional listener heard on RouterFleet's LAN (a JSON file on a
    shared volume); returns how many devices, 0 without the file."""
    path = path or getattr(settings, 'DISCOVERY_FILE', '/discovery/seen.json')
    if not os.path.isfile(path):
        return 0
    if os.path.getsize(path) > 8 * 1024 * 1024:
        logger.warning('The discovery listener file is too large to read; skipped.')
        return 0
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        logger.warning('The discovery listener file could not be read.')
        return 0
    records = []
    # Bounded like the listener itself: a flood of announcements on the LAN can
    # not fill the database
    for record in list((data.get('devices') or {}).values())[:MOST_IMPORTED] if isinstance(data, dict) else []:
        if isinstance(record, dict):
            clean = {k: str(v)[:200] for k, v in record.items() if k != 'protocols' and isinstance(v, (str, int))}
            clean['protocols'] = {str(p)[:8] for p in record.get('protocols') or [] if p in ('lldp', 'cdp', 'mndp', 'ubnt')}
            records.append(clean)
    return store(records, seen_by=LAN)


WINDOW_SECONDS = 180
MOST_IMPORTED = 2000   # devices taken from the listener's file at once
ALIVE_SECONDS = 30


def _folder():
    return os.path.dirname(getattr(settings, 'DISCOVERY_FILE', '/discovery/seen.json'))


def listener_state():
    """{'running': bool, 'listening_left': seconds} from the listener's own
    heartbeat; not running when it has not written one for half a minute."""
    import time
    try:
        with open(os.path.join(_folder(), 'listener.json'), encoding='utf-8') as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {'running': False, 'listening_left': 0}
    now = time.time()
    try:   # the file is the listener's: anything else in it is "not running"
        running = now - float(state.get('alive', 0)) < ALIVE_SECONDS
        left = max(0, int(float(state.get('listening_until', 0)) - now)) if running else 0
    except (TypeError, ValueError, AttributeError):
        return {'running': False, 'listening_left': 0}
    return {'running': running, 'listening_left': left}


def ask_listener(seconds=WINDOW_SECONDS):
    """Asks the listener to listen for a while (it reads the request within
    two seconds). False when the shared folder can not be written."""
    import time
    path = os.path.join(_folder(), 'request.json')
    temporary = path + '.tmp'
    try:
        # A link planted where the file goes is never followed (the folder is
        # shared with another container): removed, then created afresh
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o644)
        with os.fdopen(handle, 'w', encoding='utf-8') as writer:
            json.dump({'until': time.time() + seconds}, writer)
        os.replace(temporary, path)
        return True
    except OSError:
        return False
