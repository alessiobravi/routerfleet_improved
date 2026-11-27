# Run with "python manage.py shell": the template rows seed.sql clones -
# devices (addresses in 198.18.0.0/15, the benchmarking range), one backup and one availability record each, a few audit entries.
# Created through the app, so every column holds what the app would write.
import os
from django.utils import timezone
from audit_log.models import ChangeRecord
from backup_data.models import RouterBackup
from monitoring.models import RouterDownTime
from router_manager.models import Router

devices = int(os.environ.get('RF_DEVICES', '200'))
now = timezone.now()
for n in range(devices):
    router = Router.objects.create(name=f'timing-{n:04d}', address=f'198.18.{n // 256}.{n % 256}',
                                   router_type='routeros', password=os.environ['RF_TEST_DEVICE_PW'])
    RouterBackup.objects.create(router=router, schedule_type='daily', success=True, schedule_time=now,
                                finish_time=now, backup_text='/system identity\nset name=timing\n')
    RouterDownTime.objects.create(router=router, start_time=now, end_time=now, total_down_time=60)
for action in ('created', 'changed', 'deleted', 'bulk'):
    ChangeRecord.objects.create(action=action, model_label='router_manager.router')
print(f'templates: {devices} devices')
