from unittest import mock

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
        # As the monitoring container sends it: in a header, never in the URL
        return self.client.get(path, params, HTTP_X_MONITORING_KEY=KEY)

    def test_every_endpoint_refuses_without_the_key(self):
        for path in ('/monitoring/export_router_list/', '/monitoring/update_router_status/',
                     '/monitoring/router_config_timestamp/', '/monitoring/last_status_change/'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, HTTP_X_MONITORING_KEY='wrong').status_code, 403)

    def test_a_signed_in_user_needs_no_key_to_read_but_can_not_report_a_status(self):
        self.client.force_login(make_user(None))
        self.assertEqual(self.client.get('/monitoring/export_router_list/').status_code, 200)
        router = make_router()
        answer = self.client.get('/monitoring/update_router_status/', {'uuid': str(router.uuid), 'status': 'offline'})
        self.assertEqual(answer.status_code, 403)
        self.assertFalse(RouterDownTime.objects.filter(router=router).exists())

    def test_an_unknown_device_is_not_found(self):
        import uuid
        answer = self.get('/monitoring/update_router_status/', uuid=str(uuid.uuid4()), status='offline')
        self.assertEqual(answer.status_code, 404)

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
        a, _other = make_router(), make_router()
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


@override_settings(MONITORING_KEY=KEY)
class MonitoringKeyTransportTests(TestCase):
    PATH = '/monitoring/router_config_timestamp/'

    def test_the_key_is_accepted_in_a_header(self):
        self.assertEqual(self.client.get(self.PATH, HTTP_X_MONITORING_KEY=KEY).status_code, 200)
        self.assertEqual(self.client.get(self.PATH, HTTP_X_MONITORING_KEY='wrong').status_code, 403)

    def test_a_key_in_the_url_is_not_accepted(self):
        self.assertEqual(self.client.get(self.PATH, {'key': KEY}).status_code, 403)

    @override_settings(MONITORING_KEY='')
    def test_an_unset_key_admits_nobody(self):
        self.assertEqual(self.client.get(self.PATH, {'key': ''}).status_code, 403)
        self.assertEqual(self.client.get(self.PATH, HTTP_X_MONITORING_KEY='').status_code, 403)

    def test_the_monitoring_container_never_puts_the_key_in_a_url(self):
        import importlib.util
        import pathlib
        import requests
        path = pathlib.Path(__file__).resolve().parent.parent / 'containers' / 'monitoring' / 'monitoring.py'
        spec = importlib.util.spec_from_file_location('monitoring_container', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.api_key = KEY
        module.host_list = {'u1': {'address': '10.0.0.1', 'online': True}}
        calls = []

        def fail(url, **kwargs):
            calls.append((url, kwargs))
            # What requests really raises: its message names the URL
            raise requests.exceptions.ConnectionError(f'Max retries exceeded with url: {url}')
        printed = []
        with mock.patch.object(module.requests, 'get', side_effect=fail), \
                mock.patch('builtins.print', side_effect=lambda *a, **k: printed.append(' '.join(map(str, a)))):
            module.update_router_config_timestamp()
            module.fetch_host_list()
            module.update_host_status('u1', True)
        self.assertEqual(len(calls), 3)
        for url, kwargs in calls:
            self.assertNotIn(KEY, url)
            self.assertNotIn(KEY, str(kwargs.get('params')))
            self.assertEqual(kwargs['headers'], {'X-Monitoring-Key': KEY})
        self.assertTrue(printed)
        self.assertFalse([line for line in printed if KEY in line], 'the key reached the log')


class FpingArgumentTests(TestCase):
    """An address is never handed to fping as an option."""

    def load(self):
        import importlib.util
        import pathlib
        path = pathlib.Path(__file__).resolve().parent.parent / 'containers' / 'monitoring' / 'monitoring.py'
        spec = importlib.util.spec_from_file_location('monitoring_container_fping', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_option_like_addresses_are_refused(self):
        module = self.load()
        for address in ('-f/etc/passwd', '--version', '', 'a b'):
            with self.subTest(address=address):
                module.host_list = {'u': {'address': address, 'online': True}}
                with mock.patch.object(module, 'Popen') as popen, mock.patch('builtins.print'):
                    module.check_host_status('u')
                popen.assert_not_called()

    def test_a_normal_address_is_pinged_as_a_host(self):
        module = self.load()
        module.host_list = {'u': {'address': '192.0.2.1', 'online': True}}
        process = mock.Mock(returncode=0, communicate=lambda: (b'', b''))
        with mock.patch.object(module, 'Popen', return_value=process) as popen, mock.patch('builtins.print'):
            module.check_host_status('u')
        self.assertEqual(popen.call_args.args[0], ['fping', '192.0.2.1'])


class UnwatchedOutageMigrationTests(TestCase):
    """0003: outages left open for hosts that are disabled or unmonitored end
    when the host stopped being watched - or where they began, if unknown."""

    def test_open_outages_of_unwatched_hosts_end(self):
        import datetime
        import importlib
        from django.apps import apps
        from django.utils import timezone
        from monitoring.models import RouterDownTime
        from router_manager.models import Router
        from routerfleet.testing import make_router
        now = timezone.now()
        disabled_later = make_router(name='off-after')
        Router.objects.filter(pk=disabled_later.pk).update(enabled=False, enabled_changed_at=now - datetime.timedelta(hours=2))
        a = RouterDownTime.objects.create(router=disabled_later, start_time=now - datetime.timedelta(hours=5))
        disabled_before = make_router(name='off-before')
        Router.objects.filter(pk=disabled_before.pk).update(enabled=False, enabled_changed_at=now - datetime.timedelta(days=500))
        b = RouterDownTime.objects.create(router=disabled_before, start_time=now - datetime.timedelta(days=340))
        unmonitored = make_router(name='quiet')
        Router.objects.filter(pk=unmonitored.pk).update(monitoring=False)
        c = RouterDownTime.objects.create(router=unmonitored, start_time=now - datetime.timedelta(days=3))
        watched = make_router(name='watched')
        d = RouterDownTime.objects.create(router=watched, start_time=now - datetime.timedelta(minutes=10))
        importlib.import_module('monitoring.migrations.0003_close_outages_of_unwatched_hosts').close_outages(apps, None)
        for outage in (a, b, c, d):
            outage.refresh_from_db()
        self.assertEqual(a.total_down_time, 3 * 3600, 'ends when the host was disabled')
        self.assertEqual((b.end_time, b.total_down_time), (b.start_time, 0), 'opened after the disable: no downtime')
        self.assertEqual((c.end_time, c.total_down_time), (c.start_time, 0), 'when monitoring stopped is unknown')
        self.assertIsNone(d.end_time, 'a watched host keeps its outage open')

    def test_disabling_closes_the_outage_with_its_length(self):
        import datetime
        from django.utils import timezone
        from monitoring.models import RouterDownTime
        from routerfleet.testing import make_router
        router = make_router(name='to-disable')
        outage = RouterDownTime.objects.create(router=router, start_time=timezone.now() - datetime.timedelta(minutes=30))
        router.enabled = False
        router.save()
        outage.refresh_from_db()
        self.assertIsNotNone(outage.end_time)
        self.assertGreaterEqual(outage.total_down_time, 30 * 60 - 5, 'the length is counted, not left at 0')
