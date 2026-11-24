"""A change-log action for a secret shown to somebody (nothing changes; who
saw it is recorded). Only the choices change - no column. Add-only."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('audit_log', '0002_changerecord_change_object_and_more')]

    operations = [
        migrations.AlterField(model_name='changerecord', name='action',
                              field=models.CharField(choices=[('created', 'Created'), ('changed', 'Changed'), ('deleted', 'Deleted'),
                                                              ('bulk', 'Bulk change'), ('revealed', 'Secret shown')], max_length=16)),
    ]
