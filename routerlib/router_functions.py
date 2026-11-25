import json
import datetime
import logging
import re
import time
from urllib.parse import urlsplit

import requests
from django.utils import timezone
from router_manager.models import RouterInformation

logger = logging.getLogger(__name__)

# A line the device type marks (inventory_repeat) is read again until the
# device has its answer - RouterOS asks MikroTik's update server first
REPEAT_DELAY = 3  # seconds between reads
REPEAT_TIMEOUT = 15  # how long the answer is waited for at most

VENDOR_CACHE_TTL = 6 * 60 * 60  # seconds: a fleet does not ask once per device
_vendor_cache = {}   # url -> (time, data)
_confirm_cache = {}  # url -> bool


def _version_key(version: str) -> tuple:
    """Comparable key for release numbers such as '24.10.8'."""
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r'[.\-]', version or ''))


def _fetch_json(url):
    cached = _vendor_cache.get(url)
    if cached and time.time() - cached[0] < VENDOR_CACHE_TTL:
        return cached[1]
    from routerlib import outbound
    response = outbound.https_get(url, headers={'User-Agent': 'RouterFleet-Improved-update-check'})
    response.raise_for_status()
    data = response.json()
    _vendor_cache[url] = (time.time(), data)
    return data


def _confirmed(url):
    if url not in _confirm_cache:
        try:
            from routerlib import outbound
            response = outbound.https_get(url, headers={'User-Agent': 'RouterFleet-Improved-update-check'})
            _confirm_cache[url] = response.status_code == 200
        except (requests.RequestException, ValueError):
            return False  # not cached: asked again next time
    return _confirm_cache[url]


def _dig(data, path):
    for part in path.split('.'):
        if isinstance(data, dict):
            data = data.get(part)
        else:
            return ''
    return '' if data is None or isinstance(data, (dict, list)) else str(data)


def vendor_available_version(device_type, installed, values, output):
    """(available_version, update_available) from the vendor's published
    releases, as the device type describes them: a JSON document, the keys
    holding release numbers in order of preference, and optionally an address
    that must answer for a release to count for this device (its target).

    A release newer than the installed one is offered; none newer: up to date
    (the installed one). Unknown - no number to compare, the list unreadable -
    gives ('', False)."""
    from router_manager.device_types import source_value
    if not installed or not installed[:1].isdigit() or not device_type.vendor_url:
        return '', False
    try:
        data = _fetch_json(device_type.vendor_url)
    except Exception as error:
        logger.warning(f'Vendor check of {device_type.name} failed: {error}')
        return '', False
    candidates = [_dig(data, key) for key in (device_type.vendor_keys or '').split()]
    candidates = [c for c in candidates if c]
    if not candidates:
        return '', False
    target = source_value(device_type.vendor_target, values, output) if device_type.vendor_target else ''
    installed_key = _version_key(installed)
    for version in candidates:
        if _version_key(version) <= installed_key:
            continue
        if device_type.vendor_confirm_url:
            if device_type.vendor_target and not target:
                continue
            url = device_type.vendor_confirm_url.replace('{version}', version).replace('{target}', target)
            if not _confirmed(url):
                continue
        return version, True
    return installed, False


def _read_line(session, line):
    stdin, stdout, stderr = session.exec_command(line)
    return stdout.read().decode('utf-8', errors='ignore')


def _run_inventory(session, router):
    """Runs the system command "inventory" line by line. A line that fails
    does not fail the others - a failed update check never fails the read.
    A line the device type marks to repeat (inventory_repeat) is read again,
    every few seconds for a bounded time, until the version the device offers
    is known."""
    from fleet_commander import system_commands
    from router_manager import device_types
    device_type = router.device_type
    outputs = {}
    for line in system_commands.lines('inventory', router.router_type):
        try:
            outputs[line] = _read_line(session, line)
        except Exception as e:
            logger.warning(f'Inventory line failed for {router.name} ({line}): {e}')
            outputs[line] = ''
    values = system_commands.parse_values('\n'.join(outputs.values()))
    try:
        repeat = device_types.compile_pattern(device_type.inventory_repeat) if device_type else None
    except ValueError:
        repeat = None

    def offered(found):
        return device_types.read_inventory(device_type, found, '\n'.join(outputs.values())).get('available_version')

    repeat_line = next((line for line in outputs if repeat and repeat.search(line)), None)
    if repeat_line and not offered(values):
        waited = 0
        while waited < REPEAT_TIMEOUT and not offered(values):
            time.sleep(REPEAT_DELAY)
            waited += REPEAT_DELAY
            try:
                outputs[repeat_line] = _read_line(session, repeat_line)
            except Exception as e:
                logger.warning(f'Update check failed for {router.name}: {e}')
                break
            fresh = system_commands.parse_values(outputs[repeat_line])
            if offered(fresh) or 'error' in fresh.get('status', '').lower():
                values.update(fresh)
                break
    return outputs, values


def available_version(device_type, found, values, output):
    """(available_version, update_available) from the checks the device type
    has: what the device itself offers, the vendor's releases, and the
    firmware repository - the newest of them."""
    from router_manager.templatetags.router_tags import compare_versions
    installed = found.get(device_type.vendor_version_field or 'os_version') or found.get('os_version', '')
    offers = []
    if device_type.check_device and found.get('available_version'):
        offers.append(found['available_version'])
    if device_type.check_vendor:
        version, _ = vendor_available_version(device_type, installed, values, output)
        if version:
            offers.append(version)
    if device_type.check_repository:
        try:
            from firmware.models import FirmwareImage
            image = FirmwareImage.newest_for(device_type, found)
            if image:
                offers.append(image.version)
        except Exception as error:  # the repository never fails the inventory
            logger.warning(f'Repository check of {device_type.name} failed: {error}')
    if not offers:
        return '', False
    best = offers[0]
    for offer in offers[1:]:
        if compare_versions(best, offer) == -1:
            best = offer
    return best, compare_versions(installed, best) == -1


def get_router_information(router_information: RouterInformation):
    """
    Connect to the device, run the system command "inventory", and store what
    its output says in RouterInformation - where each value is found is set by
    the device type.
    """
    from router_manager import device_types
    from routerlib import device_session
    router = router_information.router
    field_max_length = 100
    success = False
    error_message = ''
    session = None

    def fit(value):
        return (value or '')[:field_max_length]

    try:
        device_type = router.device_type
        if device_type is None or not device_type.managed:
            return False, f"Device type not supported: {router.get_router_type_display()}"
        session = device_session.connect_router(router)
        outputs, values = _run_inventory(session, router)
        json_data = dict(outputs)
        output = '\n'.join(outputs.values())
        found = device_types.read_inventory(device_type, values, output)
        if not any(found.get(field) for field in device_types.INVENTORY_IDENTITY):
            return False, 'Failed to retrieve device information'
        for field, _ in device_types.INVENTORY_FIELDS:
            if field != 'available_version':
                setattr(router_information, field, fit(found.get(field)))
        try:
            offered, update_available = available_version(device_type, found, values, output)
        except Exception as e:
            logger.warning(f'Update check failed for {router.name}: {e}')
            offered, update_available = '', False
        router_information.available_version = fit(offered)
        router_information.update_available = update_available
        success = True

        router_information.success        = True
        router_information.error          = False
        router_information.retry_count    = 0
        router_information.next_retry     = None
        router_information.error_message = ''
        router_information.last_retrieval = timezone.now()
        router_information.json_data = json.dumps(json_data)
        router_information.save()
        # Neighbour tables are read only on request, from the devices an operator
        # chooses on the discovered devices page (discovery/runs.py)

    except Exception as e:
        success = False
        error_message = str(e)

    finally:
        try:
            if session:
                session.close()
        except Exception:
            pass

    return success, error_message


def update_router_information(router_information: RouterInformation):
    max_retry = 3
    retry_minutes = 5

    success = False
    error_message = ''

    if router_information.retry_count > max_retry:
        router_information.error = True
        router_information.success = False
        router_information.next_retry = None
        router_information.retry_count = 0
        router_information.last_retrieval = timezone.now()
        if router_information.error_message:
            router_information.error_message += f"\nMax retries reached for {router_information.router.name}"
        else:
            router_information.error_message = f"Max retries reached for {router_information.router.name}"
        router_information.save()
        return False, router_information.error_message
    try:
        success, error_message = get_router_information(router_information)
    except Exception as e:
        success = False
        error_message = f"Failed to update device information for {router_information.router.name}. Exception: {e}"

    if not success:
        router_information.error = True
        router_information.success = False
        router_information.next_retry = timezone.now() + datetime.timedelta(minutes=retry_minutes)
        router_information.retry_count += 1
        router_information.last_retrieval = timezone.now()
        if error_message:
            router_information.error_message = error_message
        else:
            router_information.error_message = f"Failed to update device information for {router_information.router.name}"
        router_information.save()

    return success, error_message
