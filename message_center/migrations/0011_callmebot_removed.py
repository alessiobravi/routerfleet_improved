"""CallMeBot is no longer supported (the maintainer's decision for 0.18.0).

Existing CallMeBot channels are kept, with their message history, and switched
off: System Settings lists them so they can be recreated as email, ntfy or
Telegram. Nothing is deleted.
"""
from django.db import migrations


def switch_off(apps, schema_editor):
    apps.get_model('message_center', 'MessageChannel').objects.filter(channel_type='callmebot').update(enabled=False)


class Migration(migrations.Migration):

    dependencies = [
        ('message_center', '0010_ntfy_server_and_priority'),
    ]

    operations = [
        migrations.RunPython(switch_off, migrations.RunPython.noop),
    ]
