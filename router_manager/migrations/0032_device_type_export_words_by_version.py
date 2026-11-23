"""Export words per OS version: RouterOS 6 has no show-sensitive (it exports
passwords and keys unless told hide-sensitive), RouterOS 7 the reverse.

The RouterOS type gets the RouterOS 6 words, where its own words are still the
shipped ones; devices on RouterOS 7 export exactly as before. On RouterOS 6
the export stops failing ("expected end of command") with passwords and keys
switched on, and leaves them out - as the backup profile says - when off."""
from django.db import migrations, models


def routeros_6_words(apps, schema_editor):
    DeviceType = apps.get_model('router_manager', 'DeviceType')
    DeviceType.objects.filter(slug='routeros', option_sensitive='show-sensitive').update(
        option_hide_sensitive='', legacy_before_version='7', legacy_option_sensitive='',
        legacy_option_hide_sensitive='hide-sensitive')


class Migration(migrations.Migration):
    dependencies = [('router_manager', '0031_device_type_presets')]

    operations = [
        migrations.AddField(model_name='devicetype', name='option_hide_sensitive',
                            field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name='devicetype', name='legacy_before_version',
                            field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name='devicetype', name='legacy_option_sensitive',
                            field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name='devicetype', name='legacy_option_hide_sensitive',
                            field=models.CharField(blank=True, max_length=100)),
        migrations.RunPython(routeros_6_words, migrations.RunPython.noop),
    ]
