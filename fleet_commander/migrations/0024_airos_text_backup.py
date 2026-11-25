"""airOS: the text backup from command output, like RouterOS - no file
download (SCP) for it any more.

Converted only where the airOS backup commands are exactly the shipped ones -
no text command, nothing created, /tmp/system.cfg downloaded; any edited
setup is left as it is. The text is the same file, read with cat. Also: the
neighbours command is now run only on request (Device Discovery), and its
description says so where it is still the shipped one. Frozen here, as a
migration must be.
"""
from django.db import migrations

AIROS = 'ubiquiti-airos'
DOWNLOAD = '/tmp/system.cfg'
TEXT = 'configuration: cat /tmp/system.cfg'
OLD_AIROS_DESCRIPTION = 'airOS over SSH. Its configuration file is downloaded as the text backup.'
NEW_AIROS_DESCRIPTION = 'airOS over SSH. Its configuration file is read as the text backup - nothing downloaded.'
OLD_NEIGHBOURS = ("Prints the device's neighbour tables - LLDP, CDP, MikroTik MNDP - read with the inventory, for "
                  "Discovered devices. RouterFleet recognises the output of RouterOS, Cisco, Junos, Huawei and lldpd. "
                  "Empty: the device type is not asked.")
NEW_NEIGHBOURS = ("Prints the device's neighbour tables - LLDP, CDP, MikroTik MNDP - read when an operator chooses "
                  "the device in Device Discovery. RouterFleet recognises the output of RouterOS, Cisco, Junos, "
                  "Huawei and lldpd. Empty: the device type is not offered there.")


def convert(apps, schema_editor):
    Command = apps.get_model('fleet_commander', 'Command')
    CommandVariant = apps.get_model('fleet_commander', 'CommandVariant')
    DeviceType = apps.get_model('router_manager', 'DeviceType')

    def payload(key, slug):
        row = CommandVariant.objects.filter(command__system_key=key, router_type=slug).first()
        return row, (row.payload or '').strip() if row else ''

    slugs = list(DeviceType.objects.filter(slug=AIROS).values_list('slug', flat=True)) + \
        list(DeviceType.objects.filter(preset=AIROS).exclude(slug=AIROS).values_list('slug', flat=True))
    text_command = Command.objects.filter(system_key='backup_text').first()
    for slug in slugs:
        download, download_payload = payload('backup_download', slug)
        text, text_payload = payload('backup_text', slug)
        _, create_payload = payload('backup_create', slug)
        if download is None or download_payload != DOWNLOAD or text_payload or create_payload:
            continue   # edited: left as it is
        if text is None:
            if text_command is None:
                continue
            CommandVariant.objects.create(command=text_command, router_type=slug, enabled=True, payload=TEXT)
        else:
            text.payload = TEXT
            text.save(update_fields=['payload'])
        download.payload = ''
        download.save(update_fields=['payload'])
        DeviceType.objects.filter(slug=slug, description=OLD_AIROS_DESCRIPTION).update(description=NEW_AIROS_DESCRIPTION)
    Command.objects.filter(system_key='neighbors', description=OLD_NEIGHBOURS).update(description=NEW_NEIGHBOURS)


class Migration(migrations.Migration):

    dependencies = [
        ('fleet_commander', '0023_device_wording'),
        ('router_manager', '0036_sshkey_passphrase'),
    ]

    operations = [
        migrations.RunPython(convert, migrations.RunPython.noop),
    ]
