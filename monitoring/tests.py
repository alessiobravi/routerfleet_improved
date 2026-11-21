from django.test import TestCase, override_settings
from django.utils import timezone

from message_center.models import MessageChannel, Notification
from monitoring.models import RouterDownTime
from router_manager.models import RouterStatus
from routerfleet.testing import make_group, make_router, make_user
from routerfleet_tools.models import WebadminSettings

KEY = 'b6f0f0a8-1111-4222-8333-444455556666'


@override_settings(MONITORING_KEY=KEY)
class MonitoringApiTests(TestCase):
    def get(self, path, **params):
        params.setdefault('key', KEY)
        return self.client.get(path, params)

    def test_every_endpoint_refuses_without_the_key(self):
        for path in ('/monitoring/export_router_list/', '/monitoring/update_router_status/',
                     '/monitoring/router_config_timestamp/', '/monitoring/last_status_change/'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, {'key': 'wrong'}).status_code, 403)

    def test_a_signed_in_user_needs_no_key(self):
        self.client.force_login(make_user(None))
        self.assertEqual(self.client.get('/monitoring/export_router_list/').status_code, 200)

    def test_export_lists_enabled_monitored_devices_only(self):
        watched = make_router(name='w', address='192.0.2.1')
        make_router(name='unmonitored', monitoring=False)
        make_router(name='disabled', enabled=False)
        data = self.get('/monitoring/export_router_list/').json()
        self.assertEqual(list(data['router_list']), [str(watched.uuid)])
        self.assertEqual(data['router_list'][str(watched.uuid)],
                         {'address': '192.0.2.1', 'name': 'w', 'online': True, 'uuid': str(watched.uuid)})
        self.assertTrue(data['router_config'])

    def test_export_can_filter_by_group(self):
        a, b = make_router(), make_router()
        group = make_group(routers=[a])
        data = self.get('/monitoring/export_router_list/', filter_group=str(group.uuid)).json()
        self.assertEqual(list(data['router_list']), [str(a.uuid)])

    def test_going_offline_and_back(self):
        MessageChannel.objects.create(name='c', channel_type='ntfy', destination='t')
        router = make_router()
        self.get('/monitoring/update_router_status/', uuid=str(router.uuid), status='offline')
        status = RouterStatus.objects.get(router=router)
        self.assertFalse(status.status_online)
        self.assertIsNotNone(status.last_status_change)
        downtime = RouterDownTime.objects.get(router=router)
        self.assertIsNone(downtime.end_time)
        self.assertTrue(Notification.objects.filter(notification_type='status_offline').exists())

        self.get('/monitoring/update_router_status/', uuid=str(router.uuid), status='online')
        downtime.refresh_from_db()
        self.assertIsNotNone(downtime.end_time)
        self.assertGreaterEqual(downtime.total_down_time, 0)
        self.assertTrue(RouterStatus.objects.get(router=router).status_online)

    def test_same_state_records_no_change(self):
        router = make_router()
        self.get('/monitoring/update_router_status/', uuid=str(router.uuid), status='online')
        self.assertIsNone(RouterStatus.objects.get(router=router).last_status_change)
        self.assertFalse(RouterDownTime.objects.exists())

    def test_invalid_status(self):
        router = make_router()
        response = self.get('/monitoring/update_router_status/', uuid=str(router.uuid), status='maybe')
        self.assertEqual(response.status_code, 400)

    def test_stray_open_downtimes_are_cleaned_up(self):
        router = make_router()
        RouterDownTime.objects.create(router=router, start_time=timezone.now())
        self.get('/monitoring/update_router_status/', uuid=str(router.uuid), status='offline')
        self.assertEqual(RouterDownTime.objects.filter(router=router, end_time=None).count(), 1)

    def test_config_timestamp_records_the_heartbeat(self):
        data = self.get('/monitoring/router_config_timestamp/').json()
        self.assertTrue(data['router_config'])
        self.assertIsNotNone(WebadminSettings.objects.get().monitoring_last_run)

    def test_status_report_on_an_installation_with_no_config_timestamp(self):
        router = make_router()
        WebadminSettings.objects.all().delete()
        response = self.get('/monitoring/update_router_status/', uuid=str(router.uuid), status='offline')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['router_config'])

    def test_last_status_change(self):
        self.assertEqual(self.get('/monitoring/last_status_change/').json(), {'last_status_change': '0'})
        router = make_router()
        RouterStatus.objects.filter(router=router).update(last_status_change=timezone.now())
        self.assertNotEqual(self.get('/monitoring/last_status_change/').json()['last_status_change'], '0')
