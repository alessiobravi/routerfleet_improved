"""An optional passphrase of an SSH key, encrypted at rest. Add-only."""
from django.db import migrations

import routerlib.encryption


class Migration(migrations.Migration):
    dependencies = [('router_manager', '0035_routerinformation_update_status')]

    operations = [
        migrations.AddField(model_name='sshkey', name='passphrase',
                            field=routerlib.encryption.EncryptedTextField(blank=True, null=True)),
    ]
