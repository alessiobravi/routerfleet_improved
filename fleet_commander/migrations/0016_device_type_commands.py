"""System commands for configurable device types.

- "Mikrotik (Branded)" variants fold into RouterOS (kept only where a command
  had no RouterOS variant of its own); import rows follow.
- New system command "backup - text": text backups from command output.
- RouterOS variants still on their shipped defaults take the text from
  /export output instead of an exported file; edited ones keep downloading
  their .rsc, which still becomes the text backup.
- OpenWrt took its text from the output of the first "create" line: that line
  becomes the "backup - text" command, exactly what it was, edited or not.
- The new shipped types (Cisco, Juniper, Huawei) get their variants.
Add-only otherwise; frozen here, as a migration must be.
"""
from django.db import migrations

BACKUP_TEXT = {'cisco-ios': 'running-config: show running-config',
 'huawei-vrp': 'current-configuration: display current-configuration',
 'juniper-junos': 'configuration: show configuration\nset: show configuration | display set',
 'openwrt': 'configuration: uci export',
 'routeros': 'configuration: /export {{ export_parameters }}',
 'ubiquiti-airos': ''}

NEW_TYPES = {'cisco-ios': {'backup_cleanup': '',
               'backup_create': '',
               'backup_download': '',
               'backup_remove': '',
               'backup_text': 'running-config: show running-config',
               'connection_test': 'show version',
               'inventory': 'show version\nshow inventory'},
 'huawei-vrp': {'backup_cleanup': '',
                'backup_create': '',
                'backup_download': '',
                'backup_remove': '',
                'backup_text': 'current-configuration: display current-configuration',
                'connection_test': 'display version',
                'inventory': 'display version\ndisplay esn'},
 'juniper-junos': {'backup_cleanup': '',
                   'backup_create': '',
                   'backup_download': '',
                   'backup_remove': '',
                   'backup_text': 'configuration: show configuration\nset: show configuration | display set',
                   'connection_test': 'show version',
                   'inventory': 'show version\nshow chassis hardware'}}

DESCRIPTIONS = {'backup_cleanup': {'description': 'After every successful backup: removes files that earlier, interrupted '
                                   'backups left on the device. Empty: nothing to clean.',
                    'name': 'System: backup - clean leftovers'},
 'backup_create': {'description': 'Makes backup files on the device, to be downloaded next (RouterOS '
                                  '.backup, OpenWrt archive). Empty: no files are made.',
                   'name': 'System: backup - create'},
 'backup_download': {'description': 'The files fetched from the device by SCP, one path per line - not '
                                    'commands. A file with the text extension of the device type is stored '
                                    'as the text backup when the device type takes no text from commands; '
                                    'any other file is the binary backup. Empty: no files.',
                     'name': 'System: backup - files to download'},
 'backup_remove': {'description': 'After the download: removes the files of this backup from the device. '
                                  'Empty: nothing to remove.',
                   'name': 'System: backup - remove from device'},
 'backup_text': {'description': 'The text backups, taken from the output of commands - nothing is written on '
                                'the device. One per line, "name: command" (a line without a name is called '
                                '"configuration"). Each named text is kept, compared and downloaded on its '
                                'own.',
                 'name': 'System: backup - text'},
 'connection_test': {'description': 'Run by "Test connection" on a device, to check the credentials and that '
                                    'the device is of the type selected: the output must match the '
                                    'recognition pattern of the device type.',
                     'name': 'System: connection test'},
 'inventory': {'description': 'Reads model, serial number, firmware, OS version and the version the device '
                              'can update to, every 24 hours and on request. Where each value is found is '
                              'set by the device type (key: value lines, or patterns). A line the device '
                              'type marks to repeat is repeated for up to 15 seconds until the device has '
                              'the answer. A line that fails does not fail the others.',
               'name': 'System: inventory and versions'}}

# (shipped before, shipped now) - converted only when still the one shipped before
CONVERSIONS = {'routeros': {'backup_create': ('/system backup save name={{ backup_name }}.backup\n'
                                '/export file={{ backup_name }}.rsc {{ export_parameters }}',
                                '/system backup save name={{ backup_name }}.backup'),
              'backup_download': ('/{{ backup_name }}.rsc\n/{{ backup_name }}.backup',
                                  '/{{ backup_name }}.backup'),
              'backup_remove': ('/file remove "{{ backup_name }}.rsc"\n'
                                '/file remove "{{ backup_name }}.backup"',
                                '/file remove "{{ backup_name }}.backup"')}}


def migrate(apps, schema_editor):
    Command = apps.get_model('fleet_commander', 'Command')
    CommandVariant = apps.get_model('fleet_commander', 'CommandVariant')
    ImportTask = apps.get_model('import_tool', 'ImportTask')
    DeviceType = apps.get_model('router_manager', 'DeviceType')

    for branded in CommandVariant.objects.filter(router_type='routeros-branded'):
        if CommandVariant.objects.filter(command_id=branded.command_id, router_type='routeros').exists():
            branded.delete()
        else:
            branded.router_type = 'routeros'
            branded.save(update_fields=['router_type'])
    ImportTask.objects.filter(router_type='routeros-branded').update(router_type='routeros')

    for key, text in DESCRIPTIONS.items():
        Command.objects.filter(system_key=key).update(description=text['description'])

    text_command = Command.objects.filter(system_key='backup_text').first()
    if text_command is None:
        name, number = DESCRIPTIONS['backup_text']['name'], 1
        while Command.objects.filter(name=name).exists():
            number += 1
            name = f"{DESCRIPTIONS['backup_text']['name']} ({number})"
        text_command = Command.objects.create(name=name, description=DESCRIPTIONS['backup_text']['description'],
                                              system_key='backup_text', enabled=True, capture_output=True,
                                              max_retry=1)

    def variant(key, router_type):
        return CommandVariant.objects.filter(command__system_key=key, router_type=router_type).first()

    existing = set(DeviceType.objects.values_list('slug', flat=True))
    for router_type in ('routeros', 'openwrt', 'ubiquiti-airos'):
        if variant('backup_text', router_type) is not None:
            continue
        text = ''
        if router_type == 'routeros':
            unedited = all((variant(key, 'routeros') is not None
                            and (variant(key, 'routeros').payload or '') == before)
                           for key, (before, _) in CONVERSIONS['routeros'].items())
            if unedited:
                for key, (_, now) in CONVERSIONS['routeros'].items():
                    row = variant(key, 'routeros')
                    row.payload = now
                    row.save(update_fields=['payload'])
                text = BACKUP_TEXT['routeros']
        elif router_type == 'openwrt':
            create = variant('backup_create', 'openwrt')
            lines = [line for line in (create.payload if create else '').splitlines() if line.strip()]
            if lines:
                # The first line's output was the text backup: it still is
                text = 'configuration: ' + lines[0].strip()
                create.payload = '\n'.join(lines[1:])
                create.save(update_fields=['payload'])
        CommandVariant.objects.create(command=text_command, router_type=router_type, payload=text, enabled=True)

    for router_type, payloads in NEW_TYPES.items():
        if router_type not in existing:
            continue
        for key, payload in payloads.items():
            command = Command.objects.filter(system_key=key).first()
            if command is None or CommandVariant.objects.filter(command=command, router_type=router_type).exists():
                continue
            CommandVariant.objects.create(command=command, router_type=router_type, payload=payload, enabled=True)


class Migration(migrations.Migration):

    dependencies = [
        ('fleet_commander', '0015_variant_device_type'),
        ('router_manager', '0031_device_type_presets'),
        ('import_tool', '0009_import_device_type'),
    ]

    operations = [
        migrations.RunPython(migrate, migrations.RunPython.noop),
    ]
