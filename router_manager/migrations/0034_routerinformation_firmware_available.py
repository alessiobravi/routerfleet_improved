"""Firmware updates for more device types.

- The bootloader firmware a device could install (RouterOS: upgrade-firmware),
  read with the inventory; the RouterOS type learns where it is found.
- update_any_major: whether a newer major version is offered (OpenWrt: yes,
  24.10 to 25.12 is the usual upgrade; RouterOS: no).
- The shipped types get their firmware settings - where a file is uploaded,
  how it is verified, OpenWrt's release channels - only where they are still
  empty: an edited type keeps what it has."""
from django.db import migrations, models

OPENWRT_CHANNELS = ('* stable: https://downloads.openwrt.org/.versions.json | "stable_version"\\s*:\\s*"([^"]+)"\n* oldstable: https://downloads.openwrt.org/.versions.json | "oldstable_version"\\s*:\\s*"([^"]+)"')
FIELDS = {
    'openwrt': {'update_channels': OPENWRT_CHANNELS, 'update_any_major': True},
    'ubiquiti-airos': {'firmware_upload_path': '/tmp/fwupdate.bin', 'firmware_verify': 'size'},
    'juniper-junos': {'firmware_upload_path': '/var/tmp/{{ firmware_name }}', 'firmware_verify': 'sha256'},
    'cisco-ios': {'firmware_upload_path': 'flash:{{ firmware_name }}', 'firmware_verify': 'size'},
}


def configure(apps, schema_editor):
    DeviceType = apps.get_model('router_manager', 'DeviceType')
    for device_type in DeviceType.objects.filter(slug='routeros'):
        fields = dict(device_type.inventory_fields or {})
        if 'firmware_available' not in fields:
            fields['firmware_available'] = ['key:upgrade-firmware']
            device_type.inventory_fields = fields
            device_type.save(update_fields=['inventory_fields'])
    for slug, values in FIELDS.items():
        device_type = DeviceType.objects.filter(slug=slug).first()
        if device_type is None:
            continue
        changed = []
        # Decided on the values as they were: a type configured for firmware
        # or channels before keeps its own
        had_channels, had_upload = bool(device_type.update_channels), bool(device_type.firmware_upload_path)
        for field, value in values.items():
            current = getattr(device_type, field)
            if field == 'update_any_major':
                if not had_channels:      # only with the channels it comes with
                    setattr(device_type, field, value)
                    changed.append(field)
            elif field == 'firmware_verify':
                if current in ('', 'none') and not had_upload:
                    setattr(device_type, field, value)
                    changed.append(field)
            elif not current:
                setattr(device_type, field, value)
                changed.append(field)
        if changed:
            device_type.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [('router_manager', '0033_device_type_update_channels')]

    operations = [
        migrations.AddField(model_name='routerinformation', name='firmware_available',
                            field=models.CharField(blank=True, max_length=100, null=True)),
        migrations.AddField(model_name='devicetype', name='update_any_major', field=models.BooleanField(default=False)),
        migrations.RunPython(configure, migrations.RunPython.noop),
    ]
