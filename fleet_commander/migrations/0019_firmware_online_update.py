"""System command for online firmware updates: the device downloads and
installs a release of the chosen channel itself.

Add-only: created with a variant for every device type that exists - the
shipped RouterOS procedure for the RouterOS type, empty for the others.
Frozen here."""
from django.db import migrations

NAME = 'System: firmware - online update'
DESCRIPTION = ('The device downloads and installs a release itself, from its vendor - usually ending in a reboot. '
               '{{ channel }} is the release channel chosen, {{ firmware_version }} the version it offers. '
               'RouterFleet then waits for the device to come back reporting that version. Empty: the device '
               'type has no online updates.')
PAYLOADS = {'routeros': '/system package update set channel={{ channel }}\n'
                        '/system package update check-for-updates once\n'
                        '/system package update install'}


def create(apps, schema_editor):
    Command = apps.get_model('fleet_commander', 'Command')
    CommandVariant = apps.get_model('fleet_commander', 'CommandVariant')
    DeviceType = apps.get_model('router_manager', 'DeviceType')
    command = Command.objects.filter(system_key='firmware_online_update').first()
    if command is None:
        name, number = NAME, 1
        while Command.objects.filter(name=name).exists():
            number += 1
            name = f'{NAME} ({number})'
        command = Command.objects.create(name=name, description=DESCRIPTION, system_key='firmware_online_update',
                                         enabled=True, capture_output=True, max_retry=1)
    for slug, preset in DeviceType.objects.filter(managed=True).values_list('slug', 'preset'):
        if not CommandVariant.objects.filter(command=command, router_type=slug).exists():
            CommandVariant.objects.create(command=command, router_type=slug, enabled=True,
                                          payload=PAYLOADS.get(preset or slug, ''))


class Migration(migrations.Migration):
    dependencies = [('fleet_commander', '0018_routeros_text_only'), ('router_manager', '0033_device_type_update_channels')]

    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
