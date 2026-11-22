"""System commands for firmware updates: verify the upload, install.

Add-only: created with a variant for every device type that exists, from the
shipped definitions (empty for a type of the installation's own). Frozen here.
"""
from django.db import migrations

DEFINITIONS = {'firmware_install': {'description': 'Installs the uploaded firmware - usually ending in a reboot. '
                                     'RouterFleet then waits for the device to come back reporting the new '
                                     'version. Empty: the device type has no firmware updates.',
                      'name': 'System: firmware - install'},
 'firmware_verify': {'description': 'After a firmware file is uploaded to a device: prints what proves it '
                                    'arrived whole - its SHA-256 or its size in bytes, as the device type '
                                    'says. The install starts only when it matches the repository. Empty: '
                                    'not verified.',
                     'name': 'System: firmware - verify the upload'}}

PAYLOADS = {'firmware_install': {'cisco-ios': '',
                      'huawei-vrp': '',
                      'juniper-junos': '',
                      'openwrt': 'sysupgrade {{ firmware_path }}',
                      'routeros': '/system reboot',
                      'ubiquiti-airos': ''},
 'firmware_verify': {'cisco-ios': '',
                     'huawei-vrp': '',
                     'juniper-junos': '',
                     'openwrt': 'sha256sum {{ firmware_path }}',
                     'routeros': ':put [/file get [find name="{{ firmware_name }}"] size]',
                     'ubiquiti-airos': ''}}


def create(apps, schema_editor):
    Command = apps.get_model('fleet_commander', 'Command')
    CommandVariant = apps.get_model('fleet_commander', 'CommandVariant')
    DeviceType = apps.get_model('router_manager', 'DeviceType')
    types = list(DeviceType.objects.filter(managed=True).values_list('slug', 'preset'))
    for key, definition in DEFINITIONS.items():
        command = Command.objects.filter(system_key=key).first()
        if command is None:
            name, number = definition['name'], 1
            while Command.objects.filter(name=name).exists():
                number += 1
                name = f"{definition['name']} ({number})"
            command = Command.objects.create(name=name, description=definition['description'], system_key=key,
                                             enabled=True, capture_output=True, max_retry=1)
        for slug, preset in types:
            if not CommandVariant.objects.filter(command=command, router_type=slug).exists():
                CommandVariant.objects.create(command=command, router_type=slug, enabled=True,
                                              payload=PAYLOADS[key].get(preset or slug, ''))


class Migration(migrations.Migration):

    dependencies = [
        ('fleet_commander', '0016_device_type_commands'),
    ]

    operations = [
        migrations.RunPython(create, migrations.RunPython.noop),
    ]
