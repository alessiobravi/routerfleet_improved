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

ROUTEROS = ('routeros', 'routeros-branded')
DEVICE_TYPES = ROUTEROS + ('openwrt', 'ubiquiti-airos')

PLACEHOLDER = re.compile(r'\{\{\s*([a-z_]+)\s*\}\}')


def _per_type(routeros, openwrt, airos):
    return {'routeros': routeros, 'routeros-branded': routeros, 'openwrt': openwrt, 'ubiquiti-airos': airos}


DEFAULTS = {
    'connection_test': {
        'name': 'System: connection test',
        'description': 'Run by "Test connection" on a router, to check the credentials and that the device is '
                       'of the type selected. RouterOS: the output must show the platform; OpenWrt: it must '
                       'name OpenWrt; airOS: it must start with the firmware version (XW.v6...).',
        'placeholders': (),
        'required': True,
        'variants': _per_type('/system resource print', 'ubus call system board', 'cat /etc/version'),
    },
    'inventory': {
        'name': 'System: inventory and versions',
        'description': 'Reads model, serial number, firmware, OS version and the version the device can update '
                       'to, every 24 hours and on request. RouterFleet reads "key: value" and "key=value" lines '
                       'from the output; a line that prints the update status is repeated for up to 15 seconds '
                       'until the device has the answer. A line that fails does not fail the others.',
        'placeholders': (),
        'required': True,
        'variants': _per_type(
            '/system resource print\n'
            '/system routerboard print\n'
            '/system package update check-for-updates\n'
            '/system package update print',
            'cat /etc/os-release\n'
            'echo "hostname=$(uci get system.@system[0].hostname)"\n'
            'echo "architecture=$(uname -m)"\n'
            'echo "mac=$(cat /sys/class/net/eth0/address)"',
            'echo "firmware=$(cat /etc/version)"\n'
            'cat /etc/board.info\n'
            'echo "kernel=$(uname -r)"\n'
            'cat /proc/cpuinfo'),
    },
    'backup_create': {
        'name': 'System: backup - create',
        'description': 'First phase of a backup: makes the backup files on the device. For OpenWrt, the output '
                       'of the first line is stored as the text backup. Empty: nothing is created (airOS keeps '
                       'its configuration in a file already).',
        'placeholders': ('backup_name', 'export_parameters'),
        'required': False,
        'variants': _per_type(
            '/system backup save name={{ backup_name }}.backup\n'
            '/export file={{ backup_name }}.rsc {{ export_parameters }}',
            'uci export\n'
            'sysupgrade --create-backup /tmp/{{ backup_name }}.tar.gz',
            ''),
    },
    'backup_download': {
        'name': 'System: backup - files to download',
        'description': 'Second phase: the files fetched from the device by SCP, one path per line - not '
                       'commands. A file with the text extension of the device type (.rsc, .cfg) is stored as '
                       'the text backup, any other as the binary backup.',
        'placeholders': ('backup_name',),
        'required': True,
        'variants': _per_type('/{{ backup_name }}.rsc\n/{{ backup_name }}.backup',
                              # Paths on the device, sent as text - not local temporary files
                              '/tmp/{{ backup_name }}.tar.gz',  # nosec B108
                              '/tmp/system.cfg'),  # nosec B108
    },
    'backup_remove': {
        'name': 'System: backup - remove from device',
        'description': 'After the download: removes the files of this backup from the device. Empty: nothing '
                       'to remove.',
        'placeholders': ('backup_name',),
        'required': False,
        'variants': _per_type('/file remove "{{ backup_name }}.rsc"\n/file remove "{{ backup_name }}.backup"',
                              'rm /tmp/{{ backup_name }}.tar.gz',
                              ''),
    },
    'backup_cleanup': {
        'name': 'System: backup - clean leftovers',
        'description': 'After every successful backup: removes files that earlier, interrupted backups left on '
                       'the device. Empty: nothing to clean.',
        'placeholders': (),
        'required': False,
        'variants': _per_type('file remove [find where name~"routerfleet-backup-"]',
                              'rm /tmp/routerfleet-backup-*',
                              ''),
    },
}

SYSTEM_KEYS = tuple(DEFAULTS)


class SystemCommandError(Exception):
    pass


def default_payload(key, router_type):
    return DEFAULTS[key]['variants'].get(router_type, '')


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
        logger.warning(f'System command {key} has no variant for {router_type}: using the default')
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
