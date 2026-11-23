"""What a device says about its own update check (RouterOS: the status line of
/system package update) - why no version is known when the check did not
answer. The RouterOS type learns where it is found, unless its inventory fields
were edited to name it already."""
from django.db import migrations, models


def routeros_field(apps, schema_editor):
    DeviceType = apps.get_model('router_manager', 'DeviceType')
    for device_type in DeviceType.objects.filter(slug='routeros'):
        fields = dict(device_type.inventory_fields or {})
        if 'update_status' not in fields:
            fields['update_status'] = ['key:status']
            device_type.inventory_fields = fields
            device_type.save(update_fields=['inventory_fields'])


class Migration(migrations.Migration):
    dependencies = [('router_manager', '0034_routerinformation_firmware_available')]

    operations = [
        migrations.AddField(model_name='routerinformation', name='update_status',
                            field=models.CharField(blank=True, max_length=200, null=True)),
        migrations.RunPython(routeros_field, migrations.RunPython.noop),
    ]
