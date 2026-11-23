"""Outages left open for hosts that are disabled or not monitored.

From 0.25.0 disabling a host, or switching its monitoring off, ends its open
outage - but only from then on. An outage already open at the upgrade stayed
open and kept counting as downtime: field reports show one opened nineteen
months after its host was disabled (a status report the monitoring should have
ignored, which 0.25.0 also fixed).

Each such outage ends when the host stopped being watched, if that is known
and after the outage began (a disabled host: when it was disabled); otherwise
it ends where it began - with no record of when monitoring stopped, its length
can not be known, and counting it to the upgrade would invent downtime."""
from django.db import migrations
from django.db.models import Q


def close_outages(apps, schema_editor):
    RouterDownTime = apps.get_model('monitoring', 'RouterDownTime')
    unwatched = RouterDownTime.objects.filter(end_time=None).filter(
        Q(router__enabled=False) | Q(router__monitoring=False)).select_related('router')
    for outage in unwatched:
        start = outage.start_time
        stopped = outage.router.enabled_changed_at if not outage.router.enabled else None
        end = stopped if (start and stopped and stopped > start) else start
        if end is None:
            continue
        outage.end_time = end
        outage.total_down_time = int((end - start).total_seconds()) if start else 0
        outage.save(update_fields=['end_time', 'total_down_time'])


class Migration(migrations.Migration):
    dependencies = [('monitoring', '0002_routerdowntime_downtime_router_start_and_more'),
                    ('router_manager', '0032_device_type_export_words_by_version')]

    operations = [migrations.RunPython(close_outages, migrations.RunPython.noop)]
