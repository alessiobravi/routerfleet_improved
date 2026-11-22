"""System commands: what RouterFleet itself sends to devices.

The connection test, the inventory (versions included) and every step of a
backup used to be written into the code. They are Fleet Commander commands now,
tagged with a system key: visible to everyone who can see Fleet Commander,
protected from deletion and from being disabled, editable only by an
administrator after an explicit confirmation, and run by RouterFleet only -
never by hand or on a schedule of their own.

DEFAULTS is the one definition of what is sent when nothing was edited. The
migration that created the commands holds a frozen copy of it, as migrations
must; "Restore default" uses this one.
"""
import logging
import re

logger = logging.getLogger(__name__)

PLACEHOLDER = re.compile(r'\{\{\s*([a-z_]+)\s*\}\}')


def _presets(key):
    """The commands of every shipped device type for one system key."""
    from router_manager.device_types import PRESET_COMMANDS, PRESETS
    return {slug: PRESET_COMMANDS.get(slug, {}).get(key, '')
            for slug, preset in PRESETS.items() if preset.get('managed', True)}


DEFAULTS = {
    'connection_test': {
        'name': 'System: connection test',
        'description': 'Run by "Test connection" on a device, to check the credentials and that the device is of '
                       'the type selected: the output must match the recognition pattern of the device type.',
        'placeholders': (),
        'required': True,
    },
    'inventory': {
        'name': 'System: inventory and versions',
        'description': 'Reads model, serial number, firmware, OS version and the version the device can update '
                       'to, every 24 hours and on request. Where each value is found is set by the device type '
                       '(key: value lines, or patterns). A line the device type marks to repeat is repeated for '
                       'up to 15 seconds until the device has the answer. A line that fails does not fail the '
                       'others.',
        'placeholders': (),
        'required': True,
    },
    'backup_text': {
        'name': 'System: backup - text',
        'description': 'The text backups, taken from the output of commands - nothing is written on the device. '
                       'One per line, "name: command" (a line without a name is called "configuration"). Each '
                       'named text is kept, compared and downloaded on its own.',
        'placeholders': ('export_parameters',),
        'required': False,
    },
    'backup_create': {
        'name': 'System: backup - create',
        'description': 'Makes backup files on the device, to be downloaded next (RouterOS .backup, OpenWrt '
                       'archive). Empty: no files are made.',
        'placeholders': ('backup_name', 'export_parameters'),
        'required': False,
    },
    'backup_download': {
        'name': 'System: backup - files to download',
        'description': 'The files fetched from the device by SCP, one path per line - not commands. A file with '
                       'the text extension of the device type is stored as the text backup when the device type '
                       'takes no text from commands; any other file is the binary backup. Empty: no files.',
        'placeholders': ('backup_name',),
        'required': False,
    },
    'backup_remove': {
        'name': 'System: backup - remove from device',
        'description': 'After the download: removes the files of this backup from the device. Empty: nothing '
                       'to remove.',
        'placeholders': ('backup_name',),
        'required': False,
    },
    'backup_cleanup': {
        'name': 'System: backup - clean leftovers',
        'description': 'After every successful backup: removes files that earlier, interrupted backups left on '
                       'the device. Empty: nothing to clean.',
        'placeholders': (),
        'required': False,
    },
    'firmware_verify': {
        'name': 'System: firmware - verify the upload',
        'description': 'After a firmware file is uploaded to a device: prints what proves it arrived whole - its '
                       'SHA-256 or its size in bytes, as the device type says. The install starts only when it '
                       'matches the repository. Empty: not verified.',
        'placeholders': ('firmware_name', 'firmware_path', 'firmware_version'),
        'required': False,
    },
    'firmware_install': {
        'name': 'System: firmware - install',
        'description': 'Installs the uploaded firmware - usually ending in a reboot. RouterFleet then waits for '
                       'the device to come back reporting the new version. Empty: the device type has no '
                       'firmware updates.',
        'placeholders': ('firmware_name', 'firmware_path', 'firmware_version'),
        'required': False,
    },
}


def device_types():
    """The slugs of the device types RouterFleet talks to."""
    from router_manager.models import DeviceType
    return list(DeviceType.objects.filter(managed=True).values_list('slug', flat=True))


SYSTEM_KEYS = tuple(DEFAULTS)


class SystemCommandError(Exception):
    pass


def default_payload(key, router_type):
    """The shipped payload for a type: its preset's, empty for a type of the
    installation's own."""
    from router_manager.models import DeviceType
    device_type = DeviceType.objects.filter(slug=router_type).first()
    preset = device_type.preset if device_type else router_type
    return _presets(key).get(preset, '')


def validate_payload(key, router_type, payload):
    """The reasons an edited payload can not be used, empty when it can."""
    problems = []
    allowed = DEFAULTS[key]['placeholders']
    for name in sorted(set(PLACEHOLDER.findall(payload or ''))):
        if name not in allowed:
            problems.append(f'Unknown placeholder {{{{ {name} }}}}: '
                            + (f"only {', '.join('{{ ' + a + ' }}' for a in allowed)}" if allowed else 'none')
                            + ' can be used here.')
    if DEFAULTS[key]['required'] and not (payload or '').strip():
        problems.append('This system command can not be empty: the feature it serves would stop working.')
    return problems


def payload_of(key, router_type):
    """The stored payload of a system command for a device type. Falls back to
    the default when the row is missing, which the interface does not allow:
    a backup is not lost to a missing row."""
    from fleet_commander.models import CommandVariant
    variant = CommandVariant.objects.filter(command__system_key=key, router_type=router_type).first()
    if variant is None:
        logger.warning('System command %s has no variant for the device type %r: using the default',
                       key, str(router_type)[:50].replace('\n', ' '))
        return default_payload(key, router_type)
    return variant.payload or ''


def lines(key, router_type, **values):
    """The lines to send, placeholders filled in. Raises SystemCommandError on
    a placeholder that has no value, rather than sending it to a device."""
    allowed = DEFAULTS[key]['placeholders']
    result = []
    for line in (payload_of(key, router_type) or '').splitlines():
        if not line.strip():
            continue

        def fill(match):
            name = match.group(1)
            if name not in allowed or name not in values:
                raise SystemCommandError(f'System command {key}: no value for {{{{ {name} }}}}')
            return str(values[name])
        result.append(PLACEHOLDER.sub(fill, line))
    return result


def parse_values(output):
    """'key: value' and 'key=value' lines, the first of each key kept."""
    data = {}
    for raw in (output or '').replace('\r', '').splitlines():
        line = raw.strip()
        if not line or line.startswith('['):
            continue
        colon, equals = line.find(':'), line.find('=')
        cut = min(i for i in (colon, equals) if i >= 0) if (colon >= 0 or equals >= 0) else -1
        if cut <= 0:
            continue
        key, value = line[:cut].strip(), line[cut + 1:].strip().strip('"').strip("'")
        if key and ' ' not in key.strip() or key.lower() in ('cpu model', 'system type'):
            data.setdefault(key.lower() if ' ' in key else key, value)
    return data
