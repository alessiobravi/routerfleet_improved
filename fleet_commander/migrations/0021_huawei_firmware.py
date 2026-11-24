"""Firmware updates for the shipped Huawei VRP type, where still unset:

- the procedures: verify (dir) and install (startup system-software, then
  reboot fast with its one confirmation answered);
- the type's firmware settings: the file in the root of the default storage,
  checked by its size;
- its OS version read as the release (V200R010C00SPC600) instead of the VRP
  number shared by many releases - only where the patterns are still the
  shipped ones.

Add-only, and nothing a user edited is touched. Frozen here."""
from django.db import migrations

PAYLOADS = {
    'firmware_verify': 'dir {{ firmware_name }}',
    'firmware_install': 'startup system-software {{ firmware_name }}\nreboot fast <<< y',
}
SETTINGS = {'firmware_upload_path': '{{ firmware_name }}', 'firmware_verify': 'size'}
OLD_VERSION = [r're:VRP \(R\) software, Version ([\d.]+(?: \([^)]+\))?)']
NEW_VERSION = [r're:VRP \(R\) software, Version [\d.]+ \((?:[\w-]+ )?(V\d{3}R\d{3}\w*)\)',
               r're:VRP \(R\) software, Version ([\d.]+(?: \([^)]+\))?)']


def fill(apps, schema_editor):
    Command = apps.get_model('fleet_commander', 'Command')
    CommandVariant = apps.get_model('fleet_commander', 'CommandVariant')
    DeviceType = apps.get_model('router_manager', 'DeviceType')
    for device_type in DeviceType.objects.filter(managed=True):
        if (device_type.preset or device_type.slug) != 'huawei-vrp':
            continue
        for key, payload in PAYLOADS.items():
            command = Command.objects.filter(system_key=key).first()
            if command is None:
                continue
            variant = CommandVariant.objects.filter(command=command, router_type=device_type.slug).first()
            if variant is None:
                CommandVariant.objects.create(command=command, router_type=device_type.slug, enabled=True,
                                              payload=payload)
            elif not (variant.payload or '').strip():
                variant.payload = payload
                variant.save(update_fields=['payload'])
        changed = []
        # Decided on the values as they were
        if not device_type.firmware_upload_path:
            device_type.firmware_upload_path = SETTINGS['firmware_upload_path']
            changed.append('firmware_upload_path')
        if device_type.firmware_verify in ('', 'none'):
            device_type.firmware_verify = SETTINGS['firmware_verify']
            changed.append('firmware_verify')
        fields = dict(device_type.inventory_fields or {})
        if fields.get('os_version') == OLD_VERSION:
            fields['os_version'] = NEW_VERSION
            device_type.inventory_fields = fields
            changed.append('inventory_fields')
        if changed:
            device_type.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [('fleet_commander', '0020_firmware_bootloader_upgrade'),
                    ('router_manager', '0035_routerinformation_update_status')]

    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
