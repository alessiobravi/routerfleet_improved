import json
import datetime
import logging
import re
import time
import requests
from django.utils import timezone
from router_manager.models import RouterInformation
from routerlib.functions import connect_to_ssh

logger = logging.getLogger(__name__)

ROUTEROS_UPDATE_CHECK_DELAY = 3  # seconds between reads while the router asks MikroTik's update server
ROUTEROS_UPDATE_CHECK_TIMEOUT = 15  # how long the answer is waited for at most

OPENWRT_VERSIONS_URL = 'https://downloads.openwrt.org/.versions.json'
OPENWRT_TARGET_URL = 'https://downloads.openwrt.org/releases/{version}/targets/{board}/profiles.json'
OPENWRT_RELEASE_CACHE_TTL = 6 * 60 * 60  # seconds

_openwrt_release_cache = {'time': 0, 'stable': '', 'oldstable': ''}
_openwrt_target_cache = {}


def _parse_routeros_key_value_output(output: str) -> dict:
    """
    Parse lines like "key:    value" into a dict.
    Skip blank lines or lines starting with '[' (the prompt).
    """
    data = {}
    # Normalize and split
    for raw_line in output.replace('\r', '').splitlines():
        line = raw_line.strip()
        # skip empty or prompt lines
        if not line or line.startswith('['):
            continue
        if ':' not in line:
            continue
        key, val = line.split(':', 1)
        data[key.strip()] = val.strip()
    return data


def _parse_routeros_update_info(update_info: dict, resource_info: dict) -> tuple:
    """Return (available_version, update_available) for a RouterOS device."""
    from router_manager.templatetags.router_tags import compare_versions
    latest_version = update_info.get('latest-version', '')
    installed_version = update_info.get('installed-version', '') or resource_info.get('version', '')
    # Compared as numbers, by the rule the router list colours with: a device
    # ahead of its channel, or '7.16.2' against '7.16.2 (stable)', is not behind
    update_available = compare_versions(installed_version, latest_version) == -1
    return latest_version, update_available


def _openwrt_release_versions() -> tuple:
    """
    Return the (stable, oldstable) OpenWrt release versions.

    Cached for a few hours so that a large fleet does not query the download
    server once per router.
    """
    if time.time() - _openwrt_release_cache['time'] > OPENWRT_RELEASE_CACHE_TTL:
        # Remember the attempt even when it fails, so an unreachable download
        # server is not retried for every single router
        _openwrt_release_cache['time'] = time.time()
        try:
            response = requests.get(OPENWRT_VERSIONS_URL, timeout=15)
            response.raise_for_status()
            versions = response.json()
            _openwrt_release_cache['stable'] = versions.get('stable_version', '')
            _openwrt_release_cache['oldstable'] = versions.get('oldstable_version', '')
        except Exception as e:
            logger.warning(f'Could not retrieve the OpenWrt release list: {e}')
    return _openwrt_release_cache['stable'], _openwrt_release_cache['oldstable']


def _openwrt_target_available(version: str, board: str) -> bool:
    """Whether a release still ships images for the given OpenWrt board."""
    cache_key = (version, board)
    if cache_key not in _openwrt_target_cache:
        available = False
        try:
            response = requests.get(
                OPENWRT_TARGET_URL.format(version=version, board=board), timeout=15, stream=True
            )
            available = response.status_code == 200
            response.close()
        except Exception as e:
            logger.warning(f'Could not check OpenWrt release "{version}" for board "{board}": {e}')
        _openwrt_target_cache[cache_key] = available
    return _openwrt_target_cache[cache_key]


def _version_key(version: str) -> tuple:
    """Comparable key for release numbers such as '24.10.8'."""
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r'[.\-]', version or ''))


def get_openwrt_available_version(os_release: dict) -> tuple:
    """
    Return (available_version, update_available) for an OpenWrt device.

    Only releases newer than the installed one and that still ship images for
    the device's board are offered.
    """
    installed_version = os_release.get('VERSION_ID', '')
    board = os_release.get('OPENWRT_BOARD', '')
    # A snapshot build has no release number to compare with
    if not installed_version or not board or not installed_version[:1].isdigit():
        return '', False

    stable_version, oldstable_version = _openwrt_release_versions()
    if not stable_version:
        # The release list could not be read: unknown, not up to date
        return '', False
    installed_key = _version_key(installed_version)
    for version in (stable_version, oldstable_version):
        if not version or _version_key(version) <= installed_key:
            continue
        if _openwrt_target_available(version, board):
            return version, True
    # Checked, and nothing newer is offered for this board: up to date. Left
    # empty before, which counted an up-to-date device as having no information
    return installed_version, False


def _read_line(ssh, line):
    stdin, stdout, stderr = ssh.exec_command(line)
    return stdout.read().decode('utf-8', errors='ignore')


def _run_inventory(ssh, router):
    """Runs the system command "inventory" line by line. A line that fails
    does not fail the others - a failed update check never fails the read.
    A line that prints the RouterOS update status is read again, every few
    seconds for a bounded time, until the device has its answer."""
    from fleet_commander import system_commands
    outputs = {}
    for line in system_commands.lines('inventory', router.router_type):
        try:
            outputs[line] = _read_line(ssh, line)
        except Exception as e:
            logger.warning(f'Inventory line failed for {router.name} ({line}): {e}')
            outputs[line] = ''
    values = system_commands.parse_values('\n'.join(outputs.values()))
    status_line = next((line for line in outputs if 'package update print' in line.replace('/', ' ')), None)
    if status_line and not values.get('latest-version'):
        waited = 0
        while waited < ROUTEROS_UPDATE_CHECK_TIMEOUT and not values.get('latest-version'):
            time.sleep(ROUTEROS_UPDATE_CHECK_DELAY)
            waited += ROUTEROS_UPDATE_CHECK_DELAY
            try:
                outputs[status_line] = _read_line(ssh, status_line)
            except Exception as e:
                logger.warning(f'Update check failed for {router.name}: {e}')
                break
            fresh = system_commands.parse_values(outputs[status_line])
            if fresh.get('latest-version') or 'error' in fresh.get('status', '').lower():
                values.update(fresh)
                break
    return outputs, values


def get_router_information(router_information: RouterInformation):
    """
    Connect to the router, run the system command "inventory", and store what
    its output says in RouterInformation.
    """
    router = router_information.router
    field_max_length = 100
    success = False
    error_message = ''
    ssh = None

    def fit(value):
        return (value or '')[:field_max_length]

    try:
        if router.router_type not in ('routeros', 'routeros-branded', 'openwrt', 'ubiquiti-airos'):
            return False, f"Router type not supported: {router.get_router_type_display()}"
        ssh = connect_to_ssh(router.address, router.port, router.username, router.password, router.ssh_key)
        outputs, values = _run_inventory(ssh, router)
        json_data = dict(outputs)

        if router.router_type in ('routeros', 'routeros-branded'):
            if not (values.get('board-name') or values.get('version') or values.get('model')):
                return False, 'Failed to retrieve router information'
            router_information.model_name = fit(values.get('board-name'))
            router_information.os_version = fit(values.get('version'))
            router_information.architecture = fit(values.get('architecture-name'))
            router_information.cpu = fit(values.get('cpu'))
            router_information.model_version = fit(values.get('model'))
            router_information.serial_number = fit(values.get('serial-number'))
            router_information.firmware_version = fit(values.get('current-firmware'))
            try:
                available_version, update_available = _parse_routeros_update_info(values, values)
            except Exception as e:
                logger.warning(f'Update check failed for {router.name}: {e}')
                available_version, update_available = '', False
            router_information.available_version = fit(available_version)
            router_information.update_available = update_available

        elif router.router_type == 'openwrt':
            if not values.get('VERSION_ID') and not values.get('OPENWRT_DEVICE_MODEL'):
                return False, 'Failed to retrieve router information'
            router_information.model_name = fit(values.get('OPENWRT_DEVICE_MODEL'))
            router_information.model_version = fit(values.get('VERSION_ID'))
            router_information.serial_number = fit(values.get('mac'))
            router_information.os_version = fit(values.get('VERSION'))
            router_information.firmware_version = fit(values.get('OPENWRT_RELEASE'))
            router_information.architecture = fit(values.get('architecture'))
            # A failed update check must not fail the information update itself
            try:
                available_version, update_available = get_openwrt_available_version(values)
            except Exception as e:
                logger.warning(f'Update check failed for {router.name}: {e}')
                available_version, update_available = '', False
            router_information.available_version = fit(available_version)
            router_information.update_available = update_available

        else:  # ubiquiti-airos
            if not any(key.startswith('board.') for key in values):
                return False, 'Failed to retrieve router information from /etc/board.info'
            cpu_model = values.get('cpu model', '')
            router_information.model_name = fit(values.get('board.name'))
            router_information.model_version = fit(values.get('board.model') or values.get('board.shortname'))
            router_information.serial_number = fit(values.get('board.device_id') or values.get('board.hwaddr'))
            router_information.firmware_version = fit(values.get('firmware'))
            router_information.os_version = fit(values.get('kernel'))
            router_information.architecture = fit(cpu_model.split()[0] if cpu_model else '')
            router_information.cpu = fit(values.get('system type') or cpu_model or values.get('board.cpurevision'))
            # airOS has no reliable update check over SSH
            router_information.available_version = ''
            router_information.update_available = False
        success = True

        router_information.success        = True
        router_information.error          = False
        router_information.retry_count    = 0
        router_information.next_retry     = None
        router_information.error_message = ''
        router_information.last_retrieval = timezone.now()
        router_information.json_data = json.dumps(json_data)
        router_information.save()

    except Exception as e:
        success = False
        error_message = str(e)

    finally:
        try:
            if ssh:
                ssh.close()
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
        error_message = f"Failed to update router information for {router_information.router.name}. Exception: {e}"

    if not success:
        router_information.error = True
        router_information.success = False
        router_information.next_retry = timezone.now() + datetime.timedelta(minutes=retry_minutes)
        router_information.retry_count += 1
        router_information.last_retrieval = timezone.now()
        if error_message:
            router_information.error_message = error_message
        else:
            router_information.error_message = f"Failed to update router information for {router_information.router.name}"
        router_information.save()

    return success, error_message
