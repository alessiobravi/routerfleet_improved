"""System command for the bootloader firmware upgrade (RouterOS: RouterBOOT),
and the file and online procedures of more device types.

Add-only: created with a variant for every device type that exists - the
shipped RouterOS procedure for the RouterOS type, empty for the others.
Frozen here."""
from django.db import migrations

NAME = 'System: firmware - bootloader upgrade'
DESCRIPTION = ('Upgrades the bootloader firmware to the one the device can install (RouterOS: the RouterBOOT '
               'firmware of the running RouterOS) - it takes effect at the reboot this command ends with. '
               'RouterFleet then waits for the device to report it. Empty: the device type has no bootloader '
               'upgrade.')
PAYLOADS = {'routeros': '/system routerboard upgrade\n/system reboot'}
# The file and online procedures of more device types, filled where still empty
MORE = {'ubiquiti-airos': {'firmware_verify': 'wc -c < /tmp/fwupdate.bin', 'firmware_install': '/sbin/fwupdate -m'}, 'juniper-junos': {'firmware_verify': 'file checksum sha-256 /var/tmp/{{ firmware_name }}', 'firmware_install': 'request system software add /var/tmp/{{ firmware_name }} reboot'}, 'cisco-ios': {'firmware_verify': 'dir flash:{{ firmware_name }}', 'firmware_install': 'install add file flash:{{ firmware_name }} activate commit prompt-level none'}, 'openwrt': {'firmware_online_update': 'owut upgrade -V {{ firmware_version }}'}}


def create(apps, schema_editor):
    Command = apps.get_model('fleet_commander', 'Command')
    CommandVariant = apps.get_model('fleet_commander', 'CommandVariant')
    DeviceType = apps.get_model('router_manager', 'DeviceType')
    command = Command.objects.filter(system_key='firmware_bootloader_upgrade').first()
    if command is None:
        name, number = NAME, 1
        while Command.objects.filter(name=name).exists():
            number += 1
            name = f'{NAME} ({number})'
        command = Command.objects.create(name=name, description=DESCRIPTION, system_key='firmware_bootloader_upgrade',
                                         enabled=True, capture_output=True, max_retry=1)
    for slug, preset in DeviceType.objects.filter(managed=True).values_list('slug', 'preset'):
        if not CommandVariant.objects.filter(command=command, router_type=slug).exists():
            CommandVariant.objects.create(command=command, router_type=slug, enabled=True,
                                          payload=PAYLOADS.get(preset or slug, ''))


def fill_more(apps, schema_editor):
    Command = apps.get_model('fleet_commander', 'Command')
    CommandVariant = apps.get_model('fleet_commander', 'CommandVariant')
    DeviceType = apps.get_model('router_manager', 'DeviceType')
    presets = dict(DeviceType.objects.filter(managed=True).values_list('slug', 'preset'))
    for slug, preset in presets.items():
        for key, payload in MORE.get(preset or slug, {}).items():
            command = Command.objects.filter(system_key=key).first()
            if command is None:
                continue
            variant = CommandVariant.objects.filter(command=command, router_type=slug).first()
            if variant is None:
                CommandVariant.objects.create(command=command, router_type=slug, enabled=True, payload=payload)
            elif not (variant.payload or '').strip():
                variant.payload = payload
                variant.save(update_fields=['payload'])


class Migration(migrations.Migration):
    dependencies = [('fleet_commander', '0019_firmware_online_update'),
                    ('router_manager', '0034_routerinformation_firmware_available')]

    operations = [migrations.RunPython(create, migrations.RunPython.noop),
                  migrations.RunPython(fill_more, migrations.RunPython.noop)]
