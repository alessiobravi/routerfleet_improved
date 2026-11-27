"""Device types: everything RouterFleet knows about a kind of device is data.

A device type says how to talk to a device (one SSH command per channel, or an
interactive shell with a prompt), how to recognise it, where its inventory
values are found, which text and file backups it has, how to find out which
version it could run, and how firmware is installed. Nothing about a vendor is
written into the code that uses it.

Devices and Fleet Commander variants refer to a type by its slug, which never
changes. The commands themselves are the Fleet Commander system commands of the
type (fleet_commander/system_commands.py), edited there with the same
protection as before; the rest is on the DeviceType row.

PRESETS is what ships. The migration that created the types holds a frozen copy,
as migrations must; "Restore preset" uses this one.
"""
import json
import re
import threading

INVENTORY_FIELDS = (
    ('model_name', 'Model'),
    ('model_version', 'Model version'),
    ('serial_number', 'Serial number'),
    ('os_version', 'OS version'),
    ('firmware_version', 'Firmware version'),
    ('architecture', 'Architecture'),
    ('cpu', 'CPU'),
    ('available_version', 'Version offered by the device'),
    ('firmware_available', 'Firmware the device can install (bootloader)'),
    ('update_status', 'Update check status (why no version is known)'),
)
# At least one of these must be found, or reading the inventory failed
INVENTORY_IDENTITY = ('model_name', 'model_version', 'os_version')

# Commands per system key, per preset. Keys missing for a preset are empty.
PRESET_COMMANDS = {
    'routeros': {
        'neighbors': '/ip neighbor print terse',
        'connection_test': '/system resource print',
        'inventory': '/system resource print\n/system routerboard print\n'
                     '/system package update check-for-updates\n/system package update print',
        'backup_text': 'configuration: /export {{ export_parameters }}',
        # Leftover files of the binary backups RouterFleet took before 0.23
        'backup_cleanup': 'file remove [find where name~"routerfleet-backup-"]',
        'firmware_verify': ':put [/file get [find name="{{ firmware_name }}"] size]',
        'firmware_install': '/system reboot',
        # The device downloads and installs the newest release of the chosen channel itself
        # RouterBOOT: takes effect at the next reboot
        'firmware_bootloader_upgrade': '/system routerboard upgrade\n/system reboot',
        'firmware_online_update': '/system package update set channel={{ channel }}\n'
                                  '/system package update check-for-updates once\n'
                                  '/system package update install',
    },
    'openwrt': {
        'neighbors': 'lldpcli -f keyvalue show neighbors details',
        'firmware_online_update': 'owut upgrade -V {{ firmware_version }}',
        'connection_test': 'ubus call system board',
        'inventory': 'cat /etc/os-release\necho "hostname=$(uci get system.@system[0].hostname)"\n'
                     'echo "architecture=$(uname -m)"\necho "mac=$(cat /sys/class/net/eth0/address)"',
        'backup_text': 'configuration: uci export',
        'backup_create': 'sysupgrade --create-backup /tmp/{{ backup_name }}.tar.gz',
        'backup_download': '/tmp/{{ backup_name }}.tar.gz',  # nosec B108 - a path on the device
        'backup_remove': 'rm /tmp/{{ backup_name }}.tar.gz',
        'backup_cleanup': 'rm /tmp/routerfleet-backup-*',
        'firmware_verify': 'sha256sum {{ firmware_path }}',
        'firmware_install': 'sysupgrade {{ firmware_path }}',
    },
    'ubiquiti-airos': {
        'firmware_verify': 'wc -c < /tmp/fwupdate.bin',
        'firmware_install': '/sbin/fwupdate -m',
        'connection_test': 'cat /etc/version',
        'inventory': 'echo "firmware=$(cat /etc/version)"\ncat /etc/board.info\necho "kernel=$(uname -r)"\n'
                     'cat /proc/cpuinfo',
        # The configuration file read over the command channel: no download
        'backup_text': 'configuration: cat /tmp/system.cfg',
    },
    'cisco-ios': {
        'neighbors': 'show cdp neighbors detail\nshow lldp neighbors detail',
        'firmware_verify': 'dir flash:{{ firmware_name }}',
        'firmware_install': 'install add file flash:{{ firmware_name }} activate commit prompt-level none',
        'connection_test': 'show version',
        'inventory': 'show version\nshow inventory',
        'backup_text': 'running-config: show running-config',
    },
    'juniper-junos': {
        'neighbors': 'show lldp neighbors detail\nshow lldp neighbors',
        'firmware_verify': 'file checksum sha-256 /var/tmp/{{ firmware_name }}',
        'firmware_install': 'request system software add /var/tmp/{{ firmware_name }} reboot',
        'connection_test': 'show version',
        'inventory': 'show version\nshow chassis hardware',
        'backup_text': 'configuration: show configuration\nset: show configuration | display set',
    },
    'huawei-vrp': {
        'neighbors': 'display lldp neighbor\ndisplay cdp neighbor',
        'connection_test': 'display version',
        'inventory': 'display version\ndisplay esn',
        'backup_text': 'current-configuration: display current-configuration',
        # The .cc file in the root of the default storage; the startup software
        # is kept apart from the configuration, so nothing needs saving. reboot
        # fast asks one "Continue? [Y/N]" (answered) and keeps unsaved changes
        # out - the backup taken before the update holds them
        'firmware_verify': 'dir {{ firmware_name }}',
        'firmware_install': 'startup system-software {{ firmware_name }}\nreboot fast <<< y',
    },
    'cisco-ios-classic': {
        'neighbors': 'show cdp neighbors detail\nshow lldp neighbors detail',
        'connection_test': 'show version',
        'inventory': 'show version\nshow inventory',
        'backup_text': 'running-config: show running-config',
        'firmware_verify': 'dir flash:{{ firmware_name }}',
        # Bundle mode: the boot variable, saved (write memory does not ask),
        # then the reload's one "[confirm]" answered
        'firmware_install': 'configure terminal\nno boot system\nboot system flash:{{ firmware_name }}\nend\n'
                            'write memory\nreload <<< y',
    },
}

PRESETS = {
    'monitoring': {
        'name': 'Monitoring only', 'vendor': '', 'managed': False, 'position': 0,
        'description': 'Watched for availability only: no SSH, no backups, no commands.',
    },
    'routeros': {
        'name': 'Mikrotik (RouterOS)', 'vendor': 'MikroTik', 'position': 10,
        'description': 'RouterOS over SSH. Text backup from /export output - nothing written on the device.',
        'session_mode': 'exec',
        'test_pattern': r'platform: MikroTik',
        'inventory_fields': {
            'model_name': ['key:board-name'], 'os_version': ['key:version'],
            'architecture': ['key:architecture-name'], 'cpu': ['key:cpu'], 'model_version': ['key:model'],
            'serial_number': ['key:serial-number'], 'firmware_version': ['key:current-firmware'],
            'available_version': ['key:latest-version'], 'firmware_available': ['key:upgrade-firmware'],
            'update_status': ['key:status'],
        },
        'inventory_repeat': r'package update print',
        # The account's group policies: checking for updates needs read only,
        # installing one needs write and reboot
        'rights_command': ':put [/user group get [/user get [find name="{{ username }}"] group] policy]',
        'update_rights': 'write,reboot',
        'text_extension': 'rsc', 'binary_extension': '',
        'text_ignore_pattern': r'^\s*#',
        'option_sensitive': 'show-sensitive', 'option_terse': 'terse',
        # RouterOS 6 exports passwords unless told hide-sensitive, and has no show-sensitive
        'legacy_before_version': '7', 'legacy_option_hide_sensitive': 'hide-sensitive',
        # The newest release of each channel, as RouterOS itself asks for it. RouterOS 7
        # asks NEWESTa7.*; RouterOS 6 NEWEST6.* (its "testing" answers 7.x: left out)
        'update_channels': '7 long-term: https://upgrade.mikrotik.com/routeros/NEWESTa7.long-term\n7 stable: https://upgrade.mikrotik.com/routeros/NEWESTa7.stable\n7 testing: https://upgrade.mikrotik.com/routeros/NEWESTa7.testing\n7 development: https://upgrade.mikrotik.com/routeros/NEWESTa7.development\n6 long-term: https://upgrade.mikrotik.com/routeros/NEWEST6.long-term\n6 stable: https://upgrade.mikrotik.com/routeros/NEWEST6.stable',
        'management_links': 'Winbox: winbox://{address}',
        'check_device': True,
        'firmware_upload_path': '/{{ firmware_name }}', 'firmware_verify': 'size',
    },
    'openwrt': {
        # Firmware (see docs/firmware.md for sources and caveats)
        'update_channels': '* stable: https://downloads.openwrt.org/.versions.json | "stable_version"\\s*:\\s*"([^"]+)"\n* oldstable: https://downloads.openwrt.org/.versions.json | "oldstable_version"\\s*:\\s*"([^"]+)"',
        'update_any_major': True,
        'name': 'OpenWrt', 'vendor': 'OpenWrt', 'position': 20,
        'description': 'OpenWrt over SSH. Text backup from uci export, archive from sysupgrade by SCP.',
        'session_mode': 'exec',
        'test_pattern': r'OpenWrt',
        'inventory_fields': {
            'model_name': ['key:OPENWRT_DEVICE_MODEL'], 'model_version': ['key:VERSION_ID'],
            'serial_number': ['key:mac'], 'os_version': ['key:VERSION'],
            'firmware_version': ['key:OPENWRT_RELEASE'], 'architecture': ['key:architecture'],
        },
        'text_extension': 'txt', 'binary_extension': 'tar.gz',
        'check_vendor': True,
        'vendor_url': 'https://downloads.openwrt.org/.versions.json',
        'vendor_keys': 'stable_version oldstable_version',
        'vendor_confirm_url': 'https://downloads.openwrt.org/releases/{version}/targets/{target}/profiles.json',
        'vendor_target': 'key:OPENWRT_BOARD',
        'vendor_version_field': 'model_version',
        'firmware_upload_path': '/tmp/{{ firmware_name }}', 'firmware_verify': 'sha256',  # nosec B108
    },
    'ubiquiti-airos': {
        # Firmware (see docs/firmware.md for sources and caveats)
        'firmware_upload_path': '/tmp/fwupdate.bin',  # nosec B108 - a path on the device
        'firmware_verify': 'size',
        'name': 'Ubiquiti airOS', 'vendor': 'Ubiquiti', 'position': 30,
        'description': 'airOS over SSH. Its configuration file is read as the text backup - nothing downloaded.',
        'session_mode': 'exec',
        'test_pattern': r'(?m)^\s*([A-Z]{2}\.v\d\S*)',
        'inventory_fields': {
            'model_name': ['key:board.name'], 'model_version': ['key:board.model', 'key:board.shortname'],
            'serial_number': ['key:board.device_id', 'key:board.hwaddr'], 'firmware_version': ['key:firmware'],
            'os_version': ['key:kernel'], 'architecture': [r're:(?mi)^cpu model\s*:\s*(\S+)'],
            'cpu': ['key:system type', 'key:cpu model', 'key:board.cpurevision'],
        },
        'text_extension': 'cfg', 'binary_extension': '',
    },
    'cisco-ios': {
        # Firmware (see docs/firmware.md for sources and caveats)
        'firmware_upload_path': 'flash:{{ firmware_name }}',
        'firmware_verify': 'size',
        'name': 'Cisco IOS / IOS-XE', 'vendor': 'Cisco', 'position': 40,
        'description': 'Cisco IOS and IOS-XE through an interactive SSH shell. Text backup: show running-config. '
                       'Firmware: IOS-XE install mode (classic IOS: the Cisco IOS (classic) preset). '
                       'Set an enable password on the device when the account does not land in privileged mode.',
        'session_mode': 'shell',
        'prompt_pattern': r'(?m)^[\w.\-@/:()]{1,63}[>#]\s*$',
        'session_setup': 'terminal length 0\nterminal width 0',
        'enable_command': 'enable',
        'test_pattern': r'Cisco IOS|IOS-XE|Cisco Internetwork Operating System',
        'inventory_fields': {
            'os_version': [r're:Version ([\w.()]+)'],
            'model_name': [r're:(?mi)^cisco (\S+) .*(?:processor|bytes of memory)'],
            'serial_number': [r're:Processor board ID (\S+)'],
            'model_version': [r're:PID: (\S+?)\s*,'],
            'firmware_version': [r're:(?m)^ROM: (.+)$'],
        },
        'text_extension': 'cfg', 'binary_extension': '',
        'text_ignore_pattern': r'^(! Last configuration change|! NVRAM config last updated|Building configuration'
                               r'|Current configuration :|ntp clock-period)',
    },
    'cisco-ios-classic': {
        'firmware_upload_path': 'flash:{{ firmware_name }}',
        'firmware_verify': 'size',
        'name': 'Cisco IOS (classic)', 'vendor': 'Cisco', 'position': 41,
        'description': 'Classic Cisco IOS in bundle mode (ISR G2, Catalyst 2960 and the like) through an interactive '
                       'SSH shell. Firmware: the boot variable and a reload. For IOS-XE use Cisco IOS / IOS-XE.',
        'session_mode': 'shell',
        'prompt_pattern': r'(?m)^[\w.\-@/:()]{1,63}[>#]\s*$',
        'session_setup': 'terminal length 0\nterminal width 0',
        'enable_command': 'enable',
        # Classic IOS only: an IOS-XE device says so, and numbers its versions 16.x / 17.x
        'test_pattern': r'(?s)^(?!.*IOS[ -]?XE).*Cisco IOS Software.*Version \d+\.\d+\(',
        'inventory_fields': {
            'os_version': [r're:Version ([\w.()]+)'],
            'model_name': [r're:(?mi)^cisco (\S+) .*(?:processor|bytes of memory)'],
            'serial_number': [r're:Processor board ID (\S+)'],
            'model_version': [r're:PID: (\S+?)\s*,'],
            'firmware_version': [r're:(?m)^ROM: (.+)$'],
        },
        'text_extension': 'cfg', 'binary_extension': '',
        'text_ignore_pattern': r'^(! Last configuration change|! NVRAM config last updated|Building configuration'
                               r'|Current configuration :|ntp clock-period)',
    },
    'juniper-junos': {
        # Firmware (see docs/firmware.md for sources and caveats)
        'firmware_upload_path': '/var/tmp/{{ firmware_name }}',  # nosec B108 - a path on the device
        'firmware_verify': 'sha256',
        'name': 'Juniper Junos', 'vendor': 'Juniper', 'position': 50,
        'description': 'Junos through an interactive SSH shell (CLI). Two text backups: the configuration and the '
                       'same in set format.',
        'session_mode': 'shell',
        'prompt_pattern': r'(?m)^[\w.\-@]{1,63}[>#%]\s*$',
        'session_setup': 'set cli screen-length 0\nset cli screen-width 0',
        'test_pattern': r'Junos:|JUNOS',
        'inventory_fields': {
            'os_version': [r're:Junos: (\S+)', r're:JUNOS .*\[(\S+)\]'],
            'model_name': [r're:Model: (\S+)'],
            'serial_number': [r're:(?m)^Chassis\s+(?:\S+\s+)??([A-Z0-9]{6,})\s'],
        },
        'text_extension': 'conf', 'binary_extension': '',
        'text_ignore_pattern': r'^## Last (commit|changed)',
    },
    'huawei-vrp': {
        # Firmware (see docs/firmware.md for sources and caveats)
        'firmware_upload_path': '{{ firmware_name }}',
        'firmware_verify': 'size',
        'name': 'Huawei VRP', 'vendor': 'Huawei', 'position': 60,
        'description': 'Huawei VRP through an interactive SSH shell. Text backup: display current-configuration.',
        'session_mode': 'shell',
        'prompt_pattern': r'(?m)^[<\[][\w.\-~]{1,63}[>\]]\s*$',
        'session_setup': 'screen-length 0 temporary',
        'test_pattern': r'Huawei Versatile Routing Platform|VRP \(R\) software',
        'inventory_fields': {
            'os_version': [r're:VRP \(R\) software, Version [\d.]+ \((?:[\w-]+ )?(V\d{3}R\d{3}\w*)\)',
                           r're:VRP \(R\) software, Version ([\d.]+(?: \([^)]+\))?)'],
            'model_name': [r're:(?mi)^(?:huawei) (\S+) .*uptime'],
            'serial_number': [r're:ESN of (?:slot \S+|master|device)\s*:\s*(\S+)', r're:(?mi)^ESN\s*:\s*(\S+)'],
        },
        'text_extension': 'cfg', 'binary_extension': '',
        'text_ignore_pattern': r'^!(Last configuration was|Software Version|Time:)',
    },
}

# What a preset field means when a preset does not set it
FIELD_DEFAULTS = {
    'vendor': '', 'description': '', 'managed': True, 'position': 100, 'session_mode': 'exec', 'prompt_pattern': '',
    'session_setup': '', 'enable_command': '', 'management_links': '', 'test_pattern': '', 'inventory_fields': {}, 'inventory_repeat': '',
    'text_extension': 'txt', 'binary_extension': '', 'text_ignore_pattern': '', 'option_sensitive': '',
    'option_terse': '', 'option_hide_sensitive': '', 'legacy_before_version': '', 'legacy_option_sensitive': '',
    'legacy_option_hide_sensitive': '', 'update_channels': '', 'update_channel_pattern': '', 'update_any_major': False,
    'check_device': False, 'check_vendor': False, 'check_repository': False, 'vendor_url': '',
    'vendor_keys': '', 'vendor_confirm_url': '', 'vendor_target': '', 'vendor_version_field': 'os_version',
    'firmware_upload_path': '', 'firmware_verify': 'none', 'rights_command': '', 'update_rights': '',
}


def preset_values(slug):
    preset = PRESETS[slug]
    values = {field: preset.get(field, default) for field, default in FIELD_DEFAULTS.items()}
    values['name'] = preset['name']
    values['preset'] = slug
    return values


# --- Using a type -----------------------------------------------------------------

# Read on every row of a device list: kept for the duration of one request
# only (router_manager/apps.py clears it when a request starts and ends, and
# when a type is saved or deleted). Outside a request nothing is kept.

_local = threading.local()


def clear_cache(*args, **kwargs):
    _local.cache = None


def reset_cache(*args, **kwargs):
    """A type was saved or deleted: forget what this request kept."""
    if getattr(_local, 'cache', None) is not None:
        _local.cache = {}


def start_request_cache(*args, **kwargs):
    _local.cache = {}


def get(slug):
    """The DeviceType of a slug, or None. A shipped preset whose row is
    missing is created again from its definition, so devices of that type
    never stop being backed up over a missing row."""
    if not slug:
        return None
    cache = getattr(_local, 'cache', None)
    if cache is not None and slug in cache:
        return cache[slug]
    device_type = _load(slug)
    if cache is not None and device_type is not None:
        cache[slug] = device_type
    return device_type


def _load(slug):
    from .models import DeviceType
    device_type = DeviceType.objects.filter(slug=slug).first()
    if device_type is None and slug in PRESETS:
        values = preset_values(slug)
        if DeviceType.objects.filter(name=values['name']).exists():
            values['name'] = f"{values['name']} ({slug})"
        device_type, _ = DeviceType.objects.get_or_create(slug=slug, defaults=values)
    return device_type


def choices(include_monitoring=True, managed_only=False, include=None):
    """(slug, name) of the types offered, in their order. `include` adds a
    slug even when its type is switched off - the one a device already has."""
    from .models import DeviceType
    rows = DeviceType.objects.all().order_by('position', 'name')
    result = []
    for row in rows:
        if not row.enabled and row.slug != include:
            continue
        if managed_only and not row.managed:
            continue
        if not include_monitoring and row.slug == 'monitoring':
            continue
        result.append((row.slug, row.name))
    return result


def name_of(slug):
    device_type = get(slug)
    return device_type.name if device_type else (slug or '')


def compile_pattern(pattern):
    """A pattern from a type definition, or None when empty. Raises ValueError
    with a readable reason on an invalid one."""
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as error:
        raise ValueError(f'{pattern!r} is not a valid regular expression: {error}') from None


def test_result(device_type, output):
    """(success, message) of a connection test, from the type's pattern."""
    try:
        pattern = compile_pattern(device_type.test_pattern)
    except ValueError as error:
        return False, f'The device type has an invalid test pattern: {error}'
    if pattern is None:
        return True, f'Success: connected ({device_type.name} has no recognition pattern)'
    match = pattern.search(output or '')
    if not match:
        return False, f'Device is not {device_type.name}'
    detail = f' ({match.group(1)})' if match.groups() and match.group(1) else ''
    return True, f'Success: {device_type.name} device confirmed{detail}'


def source_value(source, values, output):
    """One inventory source: 'key:NAME' from the parsed key/value lines, or
    're:PATTERN' from the raw output (its first group, or the whole match)."""
    kind, _, spec = (source or '').partition(':')
    if kind == 'key':
        return values.get(spec.strip(), '') or ''
    if kind == 're':
        try:
            match = re.search(spec, output or '')
        except re.error:
            return ''
        if not match:
            return ''
        return (match.group(1) if match.groups() else match.group(0)).strip()
    return ''


def read_inventory(device_type, values, output):
    """{field: value} for every inventory field the type defines."""
    result = {}
    fields = device_type.inventory_fields or {}
    for field, _ in INVENTORY_FIELDS:
        for source in fields.get(field, []) or []:
            value = source_value(source, values, output)
            if value:
                result[field] = value
                break
    return result


def validate_inventory_fields(data):
    """The reasons an inventory definition can not be used."""
    problems = []
    if not isinstance(data, dict):
        return ['The inventory fields must be a mapping of field to sources.']
    known = dict(INVENTORY_FIELDS)
    for field, sources in data.items():
        if field not in known:
            problems.append(f'Unknown inventory field "{field}".')
            continue
        if not isinstance(sources, list):
            problems.append(f'{known[field]}: the sources must be a list.')
            continue
        for source in sources:
            kind, _, spec = str(source).partition(':')
            if kind not in ('key', 're') or not spec.strip():
                problems.append(f'{known[field]}: "{source}" is neither key:NAME nor re:PATTERN.')
            elif kind == 're':
                try:
                    re.compile(spec)
                except re.error as error:
                    problems.append(f'{known[field]}: invalid pattern {spec!r} ({error}).')
    return problems


def clean_text(device_type, text):
    """A text backup as it is stored and compared: lines the type marks as
    noise (timestamps, counters) are dropped, so they are not a change."""
    pattern = None
    try:
        pattern = compile_pattern(device_type.text_ignore_pattern) if device_type else None
    except ValueError:
        pattern = None
    if pattern is None:
        return text
    return '\n'.join(line for line in text.split('\n') if not pattern.search(line))


def parse_named_lines(payload):
    """'name: command' lines of the backup_text system command, as
    [(name, command)]. A line without a name is called 'configuration'."""
    result = []
    seen = set()
    for raw in (payload or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.match(r'^([A-Za-z0-9][A-Za-z0-9_.\-]{0,39}):\s+(.+)$', line)
        name, command = (match.group(1), match.group(2)) if match else ('configuration', line)
        base, number = name, 2
        while name in seen:
            name, number = f'{base}-{number}', number + 1
        seen.add(name)
        result.append((name, command))
    return result


LINK_LINE = re.compile(r'^\s*([^:]{1,40}):\s*([a-z][a-z0-9+.\-]{1,20}://\S+)\s*$')
LINK_PLACEHOLDERS = ('address', 'port', 'username')


def parse_links(text):
    """[(label, template)] of "Label: scheme://..." lines. javascript:, data:
    and anything without scheme:// never pass the pattern."""
    links = []
    for line in (text or '').splitlines():
        match = LINK_LINE.match(line)
        if match and match.group(2).split(':', 1)[0].lower() not in ('javascript', 'data', 'vbscript', 'file'):
            links.append((match.group(1).strip(), match.group(2)))
    return links


def validate_links(text):
    problems = []
    for line in (text or '').splitlines():
        if line.strip() and not parse_links(line):
            problems.append(f'"{line.strip()}" is not "Label: scheme://..." (javascript:, data: and file: are not allowed).')
    for _, template in parse_links(text):
        for name in re.findall(r'\{([a-z_]+)\}', template):
            if name not in LINK_PLACEHOLDERS:
                problems.append(f'Unknown placeholder {{{name}}}: only {{address}}, {{port}} and {{username}}.')
    return problems


def links_for(router):
    """[(label, url)] of the management links of a device's type."""
    from urllib.parse import quote
    device_type = router.device_type
    if device_type is None or not router.username:
        return []
    address = router.address or ''
    if ':' in address and not address.startswith('['):
        address = f'[{address}]'   # an IPv6 address in a URL
    values = {'address': quote(address, safe='.:[]'), 'port': str(router.port or ''),
              'username': quote(router.username or '', safe='')}
    result = []
    for label, template in parse_links(device_type.management_links):
        result.append((label, re.sub(r'\{([a-z_]+)\}', lambda m: values.get(m.group(1), ''), template)))
    return result


def to_json(value):
    return json.dumps(value, indent=2, sort_keys=True)
