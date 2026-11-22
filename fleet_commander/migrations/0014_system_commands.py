"""The system commands: what RouterFleet itself sends to devices, as protected
Fleet Commander commands. Add-only: creates them, changes nothing that exists.

The definitions are frozen here, as a migration must be; a later release that
changes a default does so in a migration of its own, for unedited variants only.
"""
from django.db import migrations

SYSTEM_COMMANDS = {'connection_test': {'name': 'System: connection test',
                     'description': 'Run by "Test connection" on a router, to check the credentials and that '
                                    'the device is of the type selected. RouterOS: the output must show the '
                                    'platform; OpenWrt: it must name OpenWrt; airOS: it must start with the '
                                    'firmware version (XW.v6...).',
                     'variants': {'routeros': '/system resource print',
                                  'routeros-branded': '/system resource print',
                                  'openwrt': 'ubus call system board',
                                  'ubiquiti-airos': 'cat /etc/version'}},
 'inventory': {'name': 'System: inventory and versions',
               'description': 'Reads model, serial number, firmware, OS version and the version the device '
                              'can update to, every 24 hours and on request. RouterFleet reads "key: value" '
                              'and "key=value" lines from the output; a line that prints the update status '
                              'is repeated for up to 15 seconds until the device has the answer. A line that '
                              'fails does not fail the others.',
               'variants': {'routeros': '/system resource print\n'
                                        '/system routerboard print\n'
                                        '/system package update check-for-updates\n'
                                        '/system package update print',
                            'routeros-branded': '/system resource print\n'
                                                '/system routerboard print\n'
                                                '/system package update check-for-updates\n'
                                                '/system package update print',
                            'openwrt': 'cat /etc/os-release\n'
                                       'echo "hostname=$(uci get system.@system[0].hostname)"\n'
                                       'echo "architecture=$(uname -m)"\n'
                                       'echo "mac=$(cat /sys/class/net/eth0/address)"',
                            'ubiquiti-airos': 'echo "firmware=$(cat /etc/version)"\n'
                                              'cat /etc/board.info\n'
                                              'echo "kernel=$(uname -r)"\n'
                                              'cat /proc/cpuinfo'}},
 'backup_create': {'name': 'System: backup - create',
                   'description': 'First phase of a backup: makes the backup files on the device. For '
                                  'OpenWrt, the output of the first line is stored as the text backup. '
                                  'Empty: nothing is created (airOS keeps its configuration in a file '
                                  'already).',
                   'variants': {'routeros': '/system backup save name={{ backup_name }}.backup\n'
                                            '/export file={{ backup_name }}.rsc {{ export_parameters }}',
                                'routeros-branded': '/system backup save name={{ backup_name }}.backup\n'
                                                    '/export file={{ backup_name }}.rsc {{ export_parameters '
                                                    '}}',
                                'openwrt': 'uci export\n'
                                           'sysupgrade --create-backup /tmp/{{ backup_name }}.tar.gz',
                                'ubiquiti-airos': ''}},
 'backup_download': {'name': 'System: backup - files to download',
                     'description': 'Second phase: the files fetched from the device by SCP, one path per '
                                    'line - not commands. A file with the text extension of the device type '
                                    '(.rsc, .cfg) is stored as the text backup, any other as the binary '
                                    'backup.',
                     'variants': {'routeros': '/{{ backup_name }}.rsc\n/{{ backup_name }}.backup',
                                  'routeros-branded': '/{{ backup_name }}.rsc\n/{{ backup_name }}.backup',
                                  'openwrt': '/tmp/{{ backup_name }}.tar.gz',
                                  'ubiquiti-airos': '/tmp/system.cfg'}},
 'backup_remove': {'name': 'System: backup - remove from device',
                   'description': 'After the download: removes the files of this backup from the device. '
                                  'Empty: nothing to remove.',
                   'variants': {'routeros': '/file remove "{{ backup_name }}.rsc"\n'
                                            '/file remove "{{ backup_name }}.backup"',
                                'routeros-branded': '/file remove "{{ backup_name }}.rsc"\n'
                                                    '/file remove "{{ backup_name }}.backup"',
                                'openwrt': 'rm /tmp/{{ backup_name }}.tar.gz',
                                'ubiquiti-airos': ''}},
 'backup_cleanup': {'name': 'System: backup - clean leftovers',
                    'description': 'After every successful backup: removes files that earlier, interrupted '
                                   'backups left on the device. Empty: nothing to clean.',
                    'variants': {'routeros': 'file remove [find where name~"routerfleet-backup-"]',
                                 'routeros-branded': 'file remove [find where name~"routerfleet-backup-"]',
                                 'openwrt': 'rm /tmp/routerfleet-backup-*',
                                 'ubiquiti-airos': ''}}}


def create(apps, schema_editor):
    Command = apps.get_model('fleet_commander', 'Command')
    CommandVariant = apps.get_model('fleet_commander', 'CommandVariant')
    for key, definition in SYSTEM_COMMANDS.items():
        if Command.objects.filter(system_key=key).exists():
            continue
        # A command of the installation's own may already carry the name
        name, number = definition['name'], 1
        while Command.objects.filter(name=name).exists():
            number += 1
            name = f"{definition['name']} ({number})"
        command = Command.objects.create(name=name, description=definition['description'], system_key=key,
                                         enabled=True, capture_output=True, max_retry=1)
        for router_type, payload in definition['variants'].items():
            CommandVariant.objects.create(command=command, router_type=router_type, payload=payload, enabled=True)


class Migration(migrations.Migration):

    dependencies = [
        ('fleet_commander', '0013_command_system_key'),
    ]

    operations = [
        migrations.RunPython(create, migrations.RunPython.noop),
    ]
