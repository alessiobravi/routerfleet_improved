"""When the mail server last accepted the connection and the login - email
works only with settings checked against the server. Settings already switched
on count as checked: they were in use. Add-only."""
from django.db import migrations, models
from django.utils import timezone


def in_use_counts_as_checked(apps, schema_editor):
    SmtpSettings = apps.get_model('message_center', 'SmtpSettings')
    SmtpSettings.objects.filter(enabled=True).exclude(host='').exclude(sender_address='').update(verified_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [('message_center', '0013_firmware_update_event')]

    operations = [
        migrations.AddField(model_name='smtpsettings', name='verified_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(in_use_counts_as_checked, migrations.RunPython.noop),
    ]
