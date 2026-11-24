"""Neighbours into DiscoveredDevice: merged by MAC address when there is one,
else by address and name; what is seen again is updated, never duplicated."""
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
        device, created = DiscoveredDevice.objects.get_or_create(key=key, defaults={'last_seen': now})
        for field in ('identity', 'address', 'mac', 'platform', 'model', 'version', 'interface', 'remote_interface'):
            value = (record.get(field) or '').strip()
            if value:
                setattr(device, field, value[:DiscoveredDevice._meta.get_field(field).max_length])
        known = {p for p in device.protocols.split(',') if p} | set(record.get('protocols') or ())
        device.protocols = ','.join(sorted(known))[:40]
        device.seen_by = seen_by[:200]
        device.seen_by_router = router
        device.last_seen = now
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
            stdin, stdout, stderr = session.exec_command(line)
            output.append(stdout.read().decode('utf-8', errors='replace'))
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
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        logger.warning('The discovery listener file could not be read.')
        return 0
    records = []
    for record in (data.get('devices') or {}).values() if isinstance(data, dict) else []:
        if isinstance(record, dict):
            clean = {k: str(v)[:200] for k, v in record.items() if k != 'protocols' and isinstance(v, (str, int))}
            clean['protocols'] = {str(p)[:8] for p in record.get('protocols') or [] if p in ('lldp', 'cdp', 'mndp')}
            records.append(clean)
    return store(records, seen_by=LAN)
