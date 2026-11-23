"""Online firmware updates: where each release channel's newest version is
published, per device type. The RouterOS type gets the channels RouterOS itself
asks (RouterOS 7: NEWESTa7.*, RouterOS 6: NEWEST6.*); other types none."""
from django.db import migrations, models

ROUTEROS = ("7 long-term: https://upgrade.mikrotik.com/routeros/NEWESTa7.long-term\n7 stable: https://upgrade.mikrotik.com/routeros/NEWESTa7.stable\n7 testing: https://upgrade.mikrotik.com/routeros/NEWESTa7.testing\n7 development: https://upgrade.mikrotik.com/routeros/NEWESTa7.development\n6 long-term: https://upgrade.mikrotik.com/routeros/NEWEST6.long-term\n6 stable: https://upgrade.mikrotik.com/routeros/NEWEST6.stable")


def routeros_channels(apps, schema_editor):
    DeviceType = apps.get_model('router_manager', 'DeviceType')
    DeviceType.objects.filter(slug='routeros', update_channels='').update(update_channels=ROUTEROS)


class Migration(migrations.Migration):
    dependencies = [('router_manager', '0032_device_type_export_words_by_version')]

    operations = [
        migrations.AddField(model_name='devicetype', name='update_channels', field=models.TextField(blank=True)),
        migrations.AddField(model_name='devicetype', name='update_channel_pattern',
                            field=models.CharField(blank=True, max_length=200)),
        migrations.RunPython(routeros_channels, migrations.RunPython.noop),
    ]
