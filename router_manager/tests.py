import datetime
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from audit_log.models import ChangeRecord
from backup_data.models import RouterBackup
from monitoring.models import RouterDownTime
from router_manager.forms import RouterBulkEditForm, RouterGroupForm
from router_manager.models import BackupSchedule, Router, RouterGroup, RouterInformation, RouterStatus
from router_manager.templatetags.router_tags import (compare_versions, firmware_state, os_version_state,
                                                     update_state_counts, version_key)
from router_manager.views import _router_due_for_information_update
from routerfleet.testing import (make_group, make_information, make_profile, make_router, make_sshkey,
                                 make_user)
from routerfleet_tools.models import WebadminSettings


class VersionCompareTests(SimpleTestCase):
    def test_key_reads_the_leading_number(self):
        self.assertEqual(version_key('7.16.2 (stable)'), (7, 16, 2))
        self.assertEqual(version_key('OpenWrt 24.10.3 r28714'), (24, 10, 3))
        self.assertIsNone(version_key('stable'))
        self.assertIsNone(version_key(None))

    def test_missing_patch_level_is_zero(self):
        self.assertEqual(compare_versions('7.16', '7.16.0'), 0)
        self.assertEqual(compare_versions('7.16', '7.17'), -1)
        self.assertEqual(compare_versions('7.18', '7.17.9'), 1)
        self.assertIsNone(compare_versions('7.16', ''))


class Info:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class StateTests(SimpleTestCase):
    def test_firmware(self):
        self.assertEqual(firmware_state(Info(firmware_version='7.16', os_version='7.16.0'))['css_class'],
                         'text-success')
        self.assertEqual(firmware_state(Info(firmware_version='7.15', os_version='7.16'))['css_class'],
                         'text-danger')
        self.assertEqual(firmware_state(Info(firmware_version='7.17', os_version='7.16'))['css_class'], '')
        self.assertEqual(firmware_state(None)['css_class'], '')

    def test_os_version(self):
        self.assertEqual(os_version_state(None)['css_class'], '')
        self.assertEqual(os_version_state(Info(os_version='7.16', available_version=''))['css_class'],
                         'text-warning')
        self.assertEqual(os_version_state(Info(os_version='7.16', available_version='7.16'))['css_class'],
                         'text-success')
        state = os_version_state(Info(os_version='7.16 (stable)', available_version='7.17'))
        self.assertEqual(state['css_class'], 'text-danger')
        self.assertIn('7.17 is available', state['title'])
        self.assertEqual(os_version_state(Info(os_version='7.18', available_version='7.17'))['css_class'], '')
        self.assertEqual(os_version_state(Info(os_version='7.16', available_version='beta'))['css_class'], '')


class UpdateStateCountTests(TestCase):
    def test_counts_follow_the_list_colours_and_skip_monitoring_devices(self):
        make_information(make_router(), os_version='7.17', available_version='7.17')
        make_information(make_router(), os_version='7.16', available_version='7.17')
        make_information(make_router(), os_version='7.16', available_version='')
        make_router()  # never read
        make_router(router_type='monitoring')
        self.assertEqual(update_state_counts(), {'up_to_date': 1, 'update_waiting': 1, 'no_information': 2})


class InformationQueueTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.queue = Router.objects.filter(enabled=True)

    def due(self):
        return _router_due_for_information_update(self.queue, 24)

    def test_priority_order(self):
        fresh = make_router()
        make_information(fresh, last_retrieval=self.now)
        stale = make_router()
        make_information(stale, last_retrieval=self.now - datetime.timedelta(hours=25))
        self.assertEqual(self.due(), stale)

        never_read = make_router()
        make_information(never_read)
        self.assertEqual(self.due(), never_read)

        retry = make_router()
        make_information(retry, last_retrieval=self.now, next_retry=self.now - datetime.timedelta(minutes=1))
        self.assertEqual(self.due(), retry)

        no_row = make_router()
        self.assertEqual(self.due(), no_row)

        RouterInformation.objects.create(router=no_row)  # remove "no row" from the race
        requested = make_router()
        make_information(requested, last_retrieval=self.now, update_requested=True)
        self.assertEqual(self.due(), requested)

    def test_nothing_due(self):
        make_information(make_router(), last_retrieval=self.now, available_version='7.25.2')
        self.assertIsNone(self.due())

    def test_an_update_check_without_a_version_is_tried_again_after_an_hour(self):
        unchecked = make_router(router_type='routeros')
        make_information(unchecked, last_retrieval=self.now - datetime.timedelta(minutes=30), available_version='',
                         update_status='ERROR: could not resolve dns name')
        self.assertIsNone(self.due(), 'not before the hour is up')
        RouterInformation.objects.filter(router=unchecked).update(last_retrieval=self.now - datetime.timedelta(minutes=61))
        self.assertEqual(self.due(), unchecked)
        # A type that checks nothing has nothing to try again
        RouterInformation.objects.filter(router=unchecked).update(available_version='7.25.2')
        quiet = make_router(router_type='ubiquiti-airos')
        make_information(quiet, last_retrieval=self.now - datetime.timedelta(hours=2), available_version='')
        self.assertIsNone(self.due())

    def test_the_reason_is_shown(self):
        router = make_router(router_type='routeros')
        make_information(router, last_retrieval=self.now, os_version='7.25', available_version='',
                         update_status='ERROR: could not resolve dns name')
        state = os_version_state(RouterInformation.objects.get(router=router))
        self.assertIn('ERROR: could not resolve dns name', state['title'])
        self.assertIn('within the hour', state['title'])
        self.client.force_login(make_user(10))
        self.assertContains(self.client.get(f'/router/details/?uuid={router.uuid}'),
                            'not known - the device said: ERROR: could not resolve dns name')

    def test_cron_skips_offline_disabled_and_monitoring_devices(self):
        make_router(status_online=False)
        make_router(enabled=False)
        make_router(router_type='monitoring')
        with mock.patch('router_manager.views.update_router_information') as update:
            data = self.client.get('/cron/update_router_information/').json()
        update.assert_not_called()
        self.assertEqual(data['message'], 'No routers need update')

    def test_cron_clears_a_request_before_running(self):
        router = make_router()
        make_information(router, last_retrieval=self.now, update_requested=True)

        def check(info):
            self.assertFalse(RouterInformation.objects.get(pk=info.pk).update_requested)
            info.last_retrieval = timezone.now()
            info.save()
            return True, ''
        with mock.patch('router_manager.views.update_router_information', side_effect=check):
            data = self.client.get('/cron/update_router_information/').json()
        self.assertEqual(data['updated'], 1)

    def test_cron_stops_at_its_budget(self):
        make_router()
        with mock.patch('router_manager.views.MAX_UPDATE_INFORMATION_RUNTIME', 0), \
                mock.patch('router_manager.views.update_router_information') as update:
            self.client.get('/cron/update_router_information/')
        update.assert_not_called()


class ListAndDetailTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(10))

    def test_list_annotates_last_config_change(self):
        router = make_router()
        RouterBackup.objects.create(router=router, schedule_type='daily', backup_text='a')
        changed = RouterBackup.objects.create(router=router, schedule_type='daily', backup_text='b')
        response = self.client.get('/router/list/')
        listed = [r for r in response.context['router_list'] if r.pk == router.pk][0]
        self.assertEqual(listed.last_config_change_uuid, changed.uuid)

    def test_list_creates_the_default_profile_and_filters_to_the_default_group(self):
        a, _other = make_router(), make_router()
        make_group(routers=[a], default_group=True)
        response = self.client.get('/router/list/')
        self.assertTrue(response.context['filter_group'].default_group)
        from backup.models import BackupProfile
        self.assertTrue(BackupProfile.objects.filter(name='default').exists())
        # ?filter_group=all shows every device
        response = self.client.get('/router/list/?filter_group=all')
        self.assertIsNone(response.context['filter_group'])

    def test_list_filter_by_group(self):
        a, _other = make_router(), make_router()
        group = make_group(routers=[a])
        response = self.client.get(f'/router/list/?filter_group={group.uuid}')
        self.assertEqual(list(response.context['router_list']), [a])

    def test_visible_columns_cookie(self):
        self.client.cookies['router_visible_columns'] = '%5B%22name%22%5D'
        self.assertEqual(self.client.get('/router/list/').context['visible_columns'], ['name'])
        self.client.cookies['router_visible_columns'] = 'not json'
        self.assertIn('groups', self.client.get('/router/list/').context['visible_columns'])

    def test_details_availability(self):
        router = make_router()
        RouterDownTime.objects.create(router=router, start_time=timezone.now() - datetime.timedelta(hours=2),
                                      end_time=timezone.now() - datetime.timedelta(hours=1))
        response = self.client.get(f'/router/details/?uuid={router.uuid}')
        self.assertEqual(response.context['offline_time_last_week'], 3600)
        self.assertEqual(response.context['last_week_availability'], round((604800 - 3600) / 604800 * 100, 3))

    def test_details_say_what_the_device_did_not_report(self):
        # RouterOS on x86: no routerboard menu, so no model, serial or firmware
        router = make_router()
        RouterInformation.objects.update_or_create(router=router, defaults={
            'model_name': 'x86', 'os_version': '6.49.21 (long-term)', 'architecture': 'x86', 'cpu': 'Common',
            'model_version': '', 'serial_number': '', 'firmware_version': ''})
        page = self.client.get(f'/router/details/?uuid={router.uuid}')
        self.assertContains(page, '6.49.21 (long-term)')
        self.assertContains(page, 'not reported by the device', count=3)
        self.assertContains(page, 'Architecture')
        self.assertNotContains(page, 'Architeture')

    def test_details_never_rounds_a_short_outage_to_100(self):
        router = make_router()
        RouterDownTime.objects.create(router=router, start_time=timezone.now() - datetime.timedelta(seconds=2),
                                      end_time=timezone.now())
        response = self.client.get(f'/router/details/?uuid={router.uuid}')
        self.assertEqual(response.context['last_week_availability'], 99.999)

    def test_details_clears_a_lock_with_no_active_backup(self):
        router = make_router()
        RouterStatus.objects.filter(router=router).update(backup_lock=timezone.now())
        self.client.get(f'/router/details/?uuid={router.uuid}')
        self.assertIsNone(RouterStatus.objects.get(router=router).backup_lock)

    def test_availability_page(self):
        router = make_router()
        response = self.client.get(f'/router/availability/?uuid={router.uuid}')
        self.assertEqual(response.status_code, 200)


# Saving never connects to the device: connecting fails the test
@mock.patch('routerlib.functions.connect_to_ssh', side_effect=AssertionError('saving must not connect'))
@mock.patch('routerlib.functions.test_authentication', return_value=(True, 'ok'))
class ManageRouterTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(30))

    def post(self, data, router=None):
        url = '/router/manage/' + (f'?uuid={router.uuid}' if router else '')
        base = {'name': 'edge-1', 'address': '192.0.2.1', 'port': 22, 'username': 'admin',
                'router_type': 'routeros', 'monitoring': 'on', 'enabled': 'on'}
        base.update(data)
        return self.client.post(url, base)

    def test_create_bumps_the_monitoring_config_timestamp(self, auth, _dns):
        response = self.post({'password': 'pw'})
        self.assertRedirects(response, '/router/list/', fetch_redirect_response=False)
        router = Router.objects.get(name='edge-1')
        self.assertEqual(router.password, 'pw')
        self.assertTrue(RouterStatus.objects.filter(router=router).exists())
        self.assertIsNotNone(WebadminSettings.objects.get().router_config_last_updated)
        auth.assert_not_called()

    def test_an_unreachable_router_can_be_disabled_with_its_monitoring(self, auth, _connect):
        # The reported case: disabling a device that does not answer failed on
        # the credential test, alone or together with monitoring
        router = make_router(name='edge-1', password='pw')
        response = self.post({'password': '', 'enabled': '', 'monitoring': ''}, router)
        self.assertRedirects(response, '/router/list/', fetch_redirect_response=False)
        router.refresh_from_db()
        self.assertEqual((router.enabled, router.monitoring), (False, False))
        auth.assert_not_called()

    def test_a_router_that_does_not_answer_yet_can_be_added(self, auth, _connect):
        self.assertRedirects(self.post({'password': 'pw', 'address': 'future-site.example.net'}), '/router/list/',
                             fetch_redirect_response=False)
        auth.assert_not_called()

    def test_the_form_has_a_test_connection_button(self, _auth, _connect):
        self.assertContains(self.client.get('/router/manage/'), 'id="test-connection"')

    def test_password_or_key_not_both_not_neither(self, _auth, _dns):
        self.assertContains(self.post({}), 'You must provide a password or an SSH Key')
        key = make_sshkey()
        self.assertContains(self.post({'password': 'p', 'ssh_key': key.pk}), 'not both')

    def test_blank_password_keeps_the_stored_one(self, _auth, _dns):
        router = make_router(name='edge-1', password='kept')
        self.post({'password': ''}, router)
        self.assertEqual(Router.objects.get(pk=router.pk).password, 'kept')

    def test_switching_to_a_key_clears_the_password(self, _auth, _dns):
        router = make_router(name='edge-1', password='old')
        key = make_sshkey()
        self.post({'ssh_key': key.pk}, router)
        router.refresh_from_db()
        self.assertEqual((router.password, router.ssh_key), ('', key))

    def test_monitoring_type_takes_no_credentials_or_profile(self, auth, _dns):
        self.assertContains(self.post({'router_type': 'monitoring', 'backup_profile': make_profile().pk}),
                            'cannot have a backup profile')
        self.post({'router_type': 'monitoring'})
        router = Router.objects.get(name='edge-1')
        self.assertEqual(router.password, '')
        auth.assert_not_called()

    def test_invalid_port(self, _auth, _dns):
        self.assertContains(self.post({'password': 'p', 'port': 70000}), 'Invalid port number')

    def test_invalid_address(self, _auth, _connect):
        for address in ('not a host', 'bad_name!', '-start.example'):
            self.assertContains(self.post({'password': 'p', 'address': address}), 'valid hostname or IP address')

    def test_saving_resets_the_backup_schedule(self, _auth, _dns):
        router = make_router(name='edge-1')
        BackupSchedule.objects.create(router=router, next_daily_backup=timezone.now())
        self.post({'password': 'pw'}, router)
        self.assertFalse(BackupSchedule.objects.filter(router=router).exists())

    def test_delete_needs_confirmation(self, _auth, _dns):
        router = make_router()
        self.client.post(f'/router/manage/?uuid={router.uuid}', {'action': 'delete', 'confirmation': 'nope'})
        self.assertTrue(Router.objects.filter(pk=router.pk).exists())
        self.client.post(f'/router/manage/?uuid={router.uuid}', {'action': 'delete', 'confirmation': 'delete'})
        self.assertFalse(Router.objects.filter(pk=router.pk).exists())

    def test_refresh_information_runs_now(self, _auth, _dns):
        router = make_router()
        with mock.patch('router_manager.views.update_router_information', return_value=(False, 'a|b')):
            response = self.client.post(f'/router/manage/?uuid={router.uuid}', {'action': 'refresh_information'},
                                       follow=True)
        # The toast is "title|body", so a pipe in the body is replaced
        messages = [str(m) for m in response.context['messages']]
        self.assertIn('Router information update failed|a/b', messages)


class BulkEditTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(30))
        self.key = make_sshkey()
        self.a = make_router(password='a-pw', port=22, username='admin')
        self.b = make_router(password='b-pw', port=22, username='admin')
        self.monitor = make_router(router_type='monitoring', password='')

    def apply(self, **data):
        payload = {'router_uuids': [str(self.a.uuid), str(self.b.uuid), str(self.monitor.uuid)]}
        payload.update(data)
        return self.client.post('/router/edit/multiple/', payload)

    def test_selection_is_kept_in_the_session(self):
        response = self.client.post('/router/edit/multiple/', {'routers[]': [str(self.a.uuid)]})
        self.assertRedirects(response, '/router/edit/multiple/', fetch_redirect_response=False)
        self.assertEqual(self.client.session['router_selection'], [str(self.a.uuid)])
        self.assertEqual(list(self.client.get('/router/edit/multiple/').context['routers']), [self.a])

    def test_blank_fields_leave_values_alone(self):
        self.apply(port='2222')
        for router in (self.a, self.b):
            router.refresh_from_db()
            self.assertEqual((router.port, router.username), (2222, 'admin'))
        self.assertEqual(Router.objects.get(pk=self.a.pk).password, 'a-pw')

    def test_new_password_drops_the_key_but_not_on_monitoring_devices(self):
        Router.objects.filter(pk=self.a.pk).update(ssh_key=self.key)
        self.apply(password='new-pw')
        self.a.refresh_from_db()
        self.assertEqual((self.a.password, self.a.ssh_key), ('new-pw', None))
        self.assertEqual(Router.objects.get(pk=self.monitor.pk).password, '')

    def test_setting_a_key_clears_the_password(self):
        self.apply(ssh_key=str(self.key.uuid))
        self.a.refresh_from_db()
        self.assertEqual((self.a.password, self.a.ssh_key), ('', self.key))

    def test_one_audit_entry_for_the_whole_edit(self):
        before = ChangeRecord.objects.count()
        self.apply(port='2200')
        new = ChangeRecord.objects.order_by('-created')[:ChangeRecord.objects.count() - before]
        self.assertEqual([(r.action, r.note, r.affected_count) for r in new],
                         [('bulk', 'Bulk edit of routers', 3)])

    def test_form_rules(self):
        self.assertFalse(RouterBulkEditForm({}).is_valid())
        self.assertFalse(RouterBulkEditForm({'ssh_key': 'clear'}).is_valid())
        self.assertTrue(RouterBulkEditForm({'ssh_key': 'clear', 'password': 'x'}).is_valid())
        self.assertFalse(RouterBulkEditForm({'ssh_key': str(self.key.uuid), 'password': 'x'}).is_valid())

    def test_no_selection(self):
        self.assertRedirects(self.client.get('/router/edit/multiple/'), '/router/list/',
                             fetch_redirect_response=False)


class GroupTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(40))

    def test_only_one_default_group(self):
        first = make_group(default_group=True)
        form = RouterGroupForm({'name': 'second', 'default_group': True})
        self.assertTrue(form.is_valid())
        form.save()
        first.refresh_from_db()
        self.assertFalse(first.default_group)
        self.assertTrue(RouterGroup.objects.get(name='second').default_group)

    def test_duplicate_name_does_not_steal_the_default(self):
        first = make_group(name='taken', default_group=True)
        other = make_group(name='other')
        form = RouterGroupForm({'name': 'taken', 'default_group': True}, instance=other)
        self.assertFalse(form.is_valid())
        first.refresh_from_db()
        self.assertTrue(first.default_group)

    def test_add_and_remove_many_with_one_entry_each(self):
        group = make_group()
        a, b = make_router(), make_router()
        uuids = [str(a.uuid), str(b.uuid)]
        before = ChangeRecord.objects.count()
        self.client.post('/router/manage_groups/multiple/', {'router_uuids': uuids, 'add_group': str(group.uuid)})
        self.assertEqual(set(group.routers.all()), {a, b})
        self.client.post('/router/manage_groups/multiple/', {'router_uuids': uuids, 'remove_group': str(group.uuid)})
        self.assertFalse(group.routers.exists())
        notes = list(ChangeRecord.objects.order_by('created')[before:].values_list('note', flat=True))
        self.assertEqual(notes, [f'Added routers to group {group.name}', f'Removed routers from group {group.name}'])

    def test_same_group_for_add_and_remove_is_refused(self):
        group = make_group()
        router = make_router()
        self.client.post('/router/manage_groups/multiple/', {'router_uuids': [str(router.uuid)],
                                                             'add_group': str(group.uuid),
                                                             'remove_group': str(group.uuid)})
        self.assertFalse(group.routers.exists())

    def test_manage_group_and_key_pages(self):
        response = self.client.post('/router/manage_group/', {'name': ' spaced '})
        self.assertRedirects(response, '/router/group_list/', fetch_redirect_response=False)
        self.assertTrue(RouterGroup.objects.filter(name='spaced').exists())
        response = self.client.post('/router/manage_sshkey/', {'name': 'k', 'public_key': 'p', 'private_key': 's'})
        self.assertRedirects(response, '/router/ssh_keys/', fetch_redirect_response=False)

    def test_ssh_key_list_never_shows_the_private_key(self):
        make_sshkey()
        response = self.client.get('/router/ssh_keys/')
        self.assertNotContains(response, 'FAKE-TEST-PRIVATE-KEY-MATERIAL')


class InstantBackupTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(20))
        self.router = make_router(backup_profile=make_profile())

    def test_creates_and_locks(self):
        self.client.post(f'/router/create_instant_backup/?uuid={self.router.uuid}')
        backup = RouterBackup.objects.get(router=self.router)
        self.assertEqual(backup.schedule_type, 'instant')
        self.assertIsNotNone(RouterStatus.objects.get(router=self.router).backup_lock)

    def test_refused_when_busy_locked_or_without_profile(self):
        from router_manager.views import create_instant_backup
        RouterBackup.objects.create(router=self.router, schedule_type='daily')
        self.assertEqual(create_instant_backup(self.router), 'Active router backup task already exists')
        RouterBackup.objects.all().delete()
        RouterStatus.objects.filter(router=self.router).update(backup_lock=timezone.now())
        self.router.refresh_from_db()
        self.assertEqual(create_instant_backup(self.router), 'Router backup is currently locked')
        no_profile = make_router()
        self.assertEqual(create_instant_backup(no_profile), 'Router has no backup profile')

    def test_many_at_once(self):
        other = make_router()
        response = self.client.post('/router/create_instant_backup/multiple/',
                                    {'routers[]': [str(self.router.uuid), str(other.uuid)]})
        results = {r['router']: r['status'] for r in response.json()['results']}
        self.assertIsNone(results[self.router.name])
        self.assertEqual(results[other.name], 'Router has no backup profile')

    def test_many_without_selection(self):
        self.assertEqual(self.client.post('/router/create_instant_backup/multiple/').status_code, 400)


class UpdateInformationMultipleTests(TestCase):
    def test_queues_and_resets_errors(self):
        self.client.force_login(make_user(30))
        router = make_router()
        make_information(router, retry_count=3, error=True, error_message='old')
        response = self.client.post('/router/update_information/multiple/', {'routers[]': [str(router.uuid)]})
        self.assertEqual(response.json(), {'requested': 1})
        info = RouterInformation.objects.get(router=router)
        self.assertEqual((info.update_requested, info.retry_count, info.error), (True, 0, False))

    def test_without_selection(self):
        self.client.force_login(make_user(30))
        self.assertEqual(self.client.post('/router/update_information/multiple/').status_code, 400)


class AirOsUpdateStateTests(TestCase):
    """airOS has no update check over SSH: its devices are not counted as
    missing information an information update could fill in."""

    def test_airos_is_left_out_of_the_update_counts(self):
        from router_manager.models import RouterInformation
        from router_manager.templatetags.router_tags import os_version_state, update_state_counts
        router = make_router(router_type='ubiquiti-airos')
        info, _ = RouterInformation.objects.get_or_create(router=router)
        info.os_version = '4.14.151'
        info.save()
        self.assertEqual(update_state_counts(), {'up_to_date': 0, 'update_waiting': 0, 'no_information': 0})
        self.assertEqual(os_version_state(info)['css_class'], '')
        self.assertIn('airOS', os_version_state(info)['title'])


@mock.patch('routerlib.functions.test_authentication', return_value=(True, 'Success: MikroTik device confirmed'))
class TestConnectionTests(TestCase):
    """The connection test, on request only."""

    def setUp(self):
        self.client.force_login(make_user(30))
        self.router = make_router(name='edge-1', address='192.0.2.1', password='stored-secret')
        self.form = {'name': 'edge-1', 'address': '192.0.2.1', 'port': 22, 'username': 'admin',
                     'router_type': 'routeros', 'password': ''}

    def ask(self, data):
        return self.client.post('/router/test_connection/', data).json()

    def test_uses_the_form_values_and_saves_nothing(self, auth):
        answer = self.ask(dict(self.form, uuid=str(self.router.uuid), username='other'))
        self.assertTrue(answer['success'])
        self.assertEqual(auth.call_args.args[3:5], ('other', 'stored-secret'))
        self.assertEqual(Router.objects.get(pk=self.router.pk).username, self.router.username)

    def test_the_stored_password_only_goes_to_the_stored_address(self, auth):
        answer = self.ask(dict(self.form, uuid=str(self.router.uuid), address='198.51.100.9'))
        self.assertFalse(answer['success'])
        self.assertIn('Type the password', answer['message'])
        auth.assert_not_called()
        self.assertTrue(self.ask(dict(self.form, uuid=str(self.router.uuid), address='198.51.100.9',
                                       password='typed'))['success'])

    def test_with_the_saved_settings(self, auth):
        self.assertTrue(self.ask({'uuid': str(self.router.uuid), 'stored': '1'})['success'])
        self.assertEqual(auth.call_args.args[1], '192.0.2.1')

    def test_a_failure_is_explained(self, auth):
        auth.return_value = (False, 'timed out')
        answer = self.ask({'uuid': str(self.router.uuid), 'stored': '1'})
        self.assertEqual(answer, {'success': False, 'message': 'Could not authenticate: timed out'})

    def test_permissions_and_method(self, _auth):
        self.assertEqual(self.client.get('/router/test_connection/').status_code, 405)
        self.client.force_login(make_user(20))
        self.assertEqual(self.client.post('/router/test_connection/', {'uuid': str(self.router.uuid), 'stored': '1'}).status_code, 403)


class SavedRouterMessageTests(TestCase):
    """The message after saving says what the save means - "monitoring starts"
    only when it will (reported: shown after switching monitoring off)."""

    def message(self, created=False, changed=(), enabled=True, monitoring=True):
        from router_manager.views import saved_router_message
        return saved_router_message(Router(enabled=enabled, monitoring=monitoring), created, set(changed))

    def test_monitoring_switched_off(self):
        self.assertEqual(self.message(changed={'monitoring'}, monitoring=False), 'Router saved|Monitoring is off for this router.')

    def test_disabled(self):
        self.assertIn('Router disabled', self.message(changed={'enabled', 'monitoring'}, enabled=False, monitoring=False))

    def test_monitoring_switched_on_or_added(self):
        self.assertIn('Monitoring starts', self.message(changed={'monitoring'}))
        self.assertIn('Monitoring starts', self.message(created=True))
        self.assertIn('Router enabled|Monitoring starts', self.message(changed={'enabled'}))

    def test_other_changes_say_only_saved(self):
        self.assertEqual(self.message(changed={'username'}), 'Router saved')
        self.assertEqual(self.message(created=True, monitoring=False), 'Router added|Monitoring is off for this router.')


class QuickActionsAndBulkEnableTests(TestCase):
    """The router list's Quick actions (Back up now) and enabling / disabling
    the selected hosts - a disabled host is neither backed up nor monitored."""

    def setUp(self):
        self.profile = make_profile()
        self.router = make_router(name='edge-q1', backup_profile=self.profile)
        self.client.force_login(make_user(30))

    def test_the_column_is_named_and_offers_back_up_now(self):
        page = self.client.get('/router/list/')
        self.assertContains(page, 'Quick actions')
        self.assertContains(page, f'data-rf-post="/router/create_instant_backup/?uuid={self.router.uuid}"')
        self.client.force_login(make_user(10))
        self.assertNotContains(self.client.get('/router/list/'), 'Back up now')

    def test_a_disabled_host_offers_no_backup(self):
        self.router.enabled = False
        self.router.save()
        self.assertNotContains(self.client.get('/router/list/'), f'create_instant_backup/?uuid={self.router.uuid}')

    def test_back_up_now_returns_to_the_list_and_never_elsewhere(self):
        url = f'/router/create_instant_backup/?uuid={self.router.uuid}'
        response = self.client.post(url, {'next': '/router/list/?filter_group=all'})
        self.assertRedirects(response, '/router/list/?filter_group=all', fetch_redirect_response=False)
        self.assertTrue(RouterBackup.objects.filter(router=self.router, schedule_type='instant').exists())
        RouterBackup.objects.all().delete()
        for foreign in ('https://evil.example/router/list/', '//evil.example/router/list/', '/accounts/logout/'):
            response = self.client.post(url, {'next': foreign})
            self.assertRedirects(response, f'/router/details/?uuid={self.router.uuid}', fetch_redirect_response=False)
            RouterBackup.objects.all().delete()

    def test_disable_and_enable_the_selected_hosts(self):
        other = make_router(name='edge-q2')
        RouterDownTime.objects.create(router=self.router, start_time=timezone.now() - datetime.timedelta(minutes=5))
        response = self.client.post('/router/enable/multiple/', {'routers[]': [self.router.uuid, other.uuid],
                                                                  'action': 'disable'})
        self.assertEqual(response.json(), {'changed': 2, 'enabled': False})
        self.router.refresh_from_db()
        self.assertFalse(self.router.enabled)
        self.assertIsNotNone(self.router.enabled_changed_at)
        self.assertFalse(RouterDownTime.objects.filter(router=self.router, end_time=None).exists(),
                         'the open outage is closed: a disabled host is not monitored')
        self.assertTrue(ChangeRecord.objects.filter(model_label='router_manager.Router').exists())
        response = self.client.post('/router/enable/multiple/', {'routers[]': [self.router.uuid], 'action': 'enable'})
        self.assertEqual(response.json()['changed'], 1)
        self.router.refresh_from_db()
        self.assertTrue(self.router.enabled)

    def test_bulk_enable_needs_level_30_a_post_and_a_known_action(self):
        self.assertEqual(self.client.get('/router/enable/multiple/').status_code, 405)
        self.assertEqual(self.client.post('/router/enable/multiple/', {'routers[]': [self.router.uuid],
                                                                       'action': 'delete'}).status_code, 400)
        self.client.force_login(make_user(20))
        self.assertEqual(self.client.post('/router/enable/multiple/', {'routers[]': [self.router.uuid],
                                                                       'action': 'disable'}).status_code, 403)
        self.router.refresh_from_db()
        self.assertTrue(self.router.enabled)

    def test_a_status_report_for_a_disabled_host_is_ignored(self):
        self.router.enabled = False
        self.router.save()
        response = self.client.get(f'/monitoring/update_router_status/?uuid={self.router.uuid}&status=offline')
        self.assertEqual(response.json()['status'], 'ignored')
        self.assertFalse(RouterDownTime.objects.filter(router=self.router).exists())

    def test_switching_monitoring_off_closes_the_open_outage(self):
        RouterDownTime.objects.create(router=self.router, start_time=timezone.now())
        self.router.monitoring = False
        self.router.save()
        self.assertFalse(RouterDownTime.objects.filter(router=self.router, end_time=None).exists())


class SourceDataTests(SimpleTestCase):
    def test_output_gets_real_line_breaks_per_command(self):
        import json
        from router_manager.templatetags.router_tags import source_sections
        data = json.dumps({'/system resource print': ' uptime: 4w3d \r\n version: 7.24.5 \r\n\r\n',
                           '/system routerboard print': ' model: RB450G\r\n'})
        self.assertEqual(source_sections(data), [('/system resource print', ' uptime: 4w3d \n version: 7.24.5 '),
                                                 ('/system routerboard print', ' model: RB450G')])
        self.assertEqual(source_sections(''), [])
        self.assertEqual(source_sections('not json'), [('', 'not json')])
