"""RouterOS backups become text only: /export output, no binary .backup.

Decided by the maintainer (0.23.0): the binary backup is dropped from the
RouterOS type. Converted only where the RouterOS backup commands are exactly
the ones shipped in 0.22 and the text already comes from /export output; any
edited variant is left as it is. Backups already stored keep their files.
Frozen here, as a migration must be.
"""
from django.db import migrations

SHIPPED = {
    'backup_create': '/system backup save name={{ backup_name }}.backup',
    'backup_download': '/{{ backup_name }}.backup',
    'backup_remove': '/file remove "{{ backup_name }}.backup"',
}
TEXT = 'configuration: /export {{ export_parameters }}'


def convert(apps, schema_editor):
    CommandVariant = apps.get_model('fleet_commander', 'CommandVariant')
    DeviceType = apps.get_model('router_manager', 'DeviceType')

    def variant(key):
        return CommandVariant.objects.filter(command__system_key=key, router_type='routeros').first()

    text = variant('backup_text')
    rows = {key: variant(key) for key in SHIPPED}
    if text is None or (text.payload or '').strip() != TEXT:
        return
    if any(row is None or (row.payload or '') != payload for (key, payload), row in zip(SHIPPED.items(), rows.values())):
        return
    for row in rows.values():
        row.payload = ''
        row.save(update_fields=['payload'])
    DeviceType.objects.filter(slug='routeros', binary_extension='backup').update(
        binary_extension='',
        description='RouterOS over SSH. Text backup from /export output - nothing written on the device.')


class Migration(migrations.Migration):

    dependencies = [
        ('fleet_commander', '0017_firmware_commands'),
        ('router_manager', '0031_device_type_presets'),
    ]

    operations = [
        migrations.RunPython(convert, migrations.RunPython.noop),
    ]
