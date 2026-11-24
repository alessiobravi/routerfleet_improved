"""The system command "neighbours": the device's LLDP/CDP/MNDP neighbour
tables, read with the inventory for Discovered devices. Created with a variant
for every device type: the shipped command for the shipped types, empty for
others. Add-only; frozen here."""
from django.db import migrations

NAME = 'System: neighbours (discovery)'
DESCRIPTION = ("Prints the device's neighbour tables - LLDP, CDP, MikroTik MNDP - read with the inventory, for "
               "Discovered devices. RouterFleet recognises the output of RouterOS, Cisco, Junos, Huawei and lldpd. "
               "Empty: the device type is not asked.")
PAYLOADS = {
    'routeros': '/ip neighbor print terse',
    'openwrt': 'lldpcli -f keyvalue show neighbors details',
    'cisco-ios': 'show cdp neighbors detail\nshow lldp neighbors detail',
    'cisco-ios-classic': 'show cdp neighbors detail\nshow lldp neighbors detail',
    'juniper-junos': 'show lldp neighbors',
    'huawei-vrp': 'display lldp neighbor',
}


def create(apps, schema_editor):
    Command = apps.get_model('fleet_commander', 'Command')
    CommandVariant = apps.get_model('fleet_commander', 'CommandVariant')
    DeviceType = apps.get_model('router_manager', 'DeviceType')
    command = Command.objects.filter(system_key='neighbors').first()
    if command is None:
        name, number = NAME, 1
        while Command.objects.filter(name=name).exists():
            number += 1
            name = f'{NAME} ({number})'
        command = Command.objects.create(name=name, description=DESCRIPTION, system_key='neighbors',
                                         enabled=True, capture_output=True, max_retry=1)
    for slug, preset in DeviceType.objects.filter(managed=True).values_list('slug', 'preset'):
        if not CommandVariant.objects.filter(command=command, router_type=slug).exists():
            CommandVariant.objects.create(command=command, router_type=slug, enabled=True,
                                          payload=PAYLOADS.get(preset or slug, ''))


class Migration(migrations.Migration):
    dependencies = [('fleet_commander', '0021_huawei_firmware'),
                    ('router_manager', '0036_sshkey_passphrase')]

    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
