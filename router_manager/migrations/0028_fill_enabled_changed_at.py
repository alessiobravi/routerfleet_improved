"""When each existing device was last enabled or disabled, from what is known.

The change log's own entry for the device, where one exists; for a device that
is disabled without such an entry (a bulk edit records one summary for all its
devices), the last time it was saved. Devices it says nothing about stay
unknown: the status page then treats them as they are now on every day.
"""
from django.db import migrations


def fill(apps, schema_editor):
    Router = apps.get_model('router_manager', 'Router')
    ChangeRecord = apps.get_model('audit_log', 'ChangeRecord')
    routers = {r.uuid: r for r in Router.objects.all()}
    latest = {}
    for record in ChangeRecord.objects.filter(model_label='router_manager.Router',
                                              object_uuid__in=list(routers)).order_by('created'):
        if isinstance(record.changes, dict) and 'Enabled' in record.changes:
            latest[record.object_uuid] = record.created
    for uuid, router in routers.items():
        moment = latest.get(uuid) or (None if router.enabled else router.updated)
        if moment:
            Router.objects.filter(pk=router.pk).update(enabled_changed_at=moment)


class Migration(migrations.Migration):

    dependencies = [
        ('router_manager', '0027_router_enabled_changed_at'),
        ('audit_log', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
