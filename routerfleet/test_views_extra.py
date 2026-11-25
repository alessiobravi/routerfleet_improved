"""Branches of the views the feature tests do not reach: confirmations, empty
selections, the less common fields, and the error paths."""
import datetime
import os
import tempfile
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from backup.models import BackupProfile
from backup_data.models import RouterBackup
from fleet_commander.models import Command, CommandJob, CommandSchedule, CommandTask, CommandVariant
from message_center.models import Message, MessageChannel, MessageSettings
from router_manager.models import BackupSchedule, Router, RouterGroup, RouterStatus, SSHKey
from routerfleet.testing import make_group, make_profile, make_router, make_sshkey, make_user


def messages_of(response):
    return ' '.join(str(m) for m in response.context['messages'])


class DeleteConfirmationTests(TestCase):
    """Every delete asks for a typed confirmation and does nothing without it."""

    def setUp(self):
        self.client.force_login(make_user(50))

    def check(self, url, model, pk):
        self.client.post(url, {'action': 'delete', 'confirmation': 'wrong'})
        self.assertTrue(model.objects.filter(pk=pk).exists(), url)
        self.client.post(url, {'action': 'delete', 'confirmation': 'delete'})
        self.assertFalse(model.objects.filter(pk=pk).exists(), url)

    def test_router_group_ssh_key_command_variant_schedule(self):
        group = make_group()
        self.check(f'/router/manage_group/?uuid={group.uuid}', RouterGroup, group.pk)
        key = make_sshkey()
        self.check(f'/ssh_keys/manage/?uuid={key.uuid}', SSHKey, key.pk)
        command = Command.objects.create(name='Deletable')
        self.check(f'/fleet_commander/command/manage/?uuid={command.uuid}', Command, command.pk)
        command = Command.objects.create(name='Parent')
        variant = CommandVariant.objects.create(command=command, router_type='routeros', payload='/x')
        self.check(f'/fleet_commander/variant/manage/?uuid={variant.uuid}', CommandVariant, variant.pk)
        schedule = CommandSchedule.objects.create(command=command)
        self.check(f'/fleet_commander/schedule/manage/?uuid={schedule.uuid}', CommandSchedule, schedule.pk)

    def test_backup_profile_wrong_confirmation(self):
        profile = make_profile()
        response = self.client.post(f'/backup/manage_profile/?uuid={profile.uuid}', {'action': 'delete', 'confirmation': 'x'},
                                   follow=True)
        self.assertTrue(BackupProfile.objects.filter(pk=profile.pk).exists())
        self.assertIn('Invalid confirmation', messages_of(response))


class CommandViewBranchTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(40))
        self.command = Command.objects.create(name='C')
        CommandVariant.objects.create(command=self.command, router_type='routeros', payload='/x')

    def test_create_and_edit_command(self):
        response = self.client.post('/fleet_commander/command/manage/',
                                    {'name': 'New', 'max_retry': 3, 'retry_interval': 30, 'verify_timeout': 300,
                                     'verify_interval': 30, 'enabled': 'on'})
        self.assertRedirects(response, '/fleet_commander/', fetch_redirect_response=False)
        new = Command.objects.get(name='New')
        response = self.client.post(f'/fleet_commander/command/manage/?uuid={new.uuid}',
                                    {'name': 'Renamed', 'max_retry': 2, 'retry_interval': 10,
                                     'verify_timeout': 300, 'verify_interval': 30})
        self.assertRedirects(response, f'/fleet_commander/command/details/?uuid={new.uuid}',
                             fetch_redirect_response=False)

    def test_create_variant(self):
        response = self.client.post(f'/fleet_commander/variant/manage/?command_uuid={self.command.uuid}',
                                    {'router_type': 'openwrt', 'payload': 'uname -a', 'enabled': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.command.variants.filter(router_type='openwrt').exists())

    def test_execute_with_no_enabled_target(self):
        disabled = make_router(enabled=False)
        response = self.client.post(f'/fleet_commander/command/execute/?command_uuid={self.command.uuid}',
                                    {'routers': [disabled.pk]})
        # The disabled device is not offered at all, so nothing valid was chosen
        self.assertFalse(CommandJob.objects.exists())
        self.assertEqual(response.status_code, 200)

    def test_run_on_many_edge_cases(self):
        url = '/fleet_commander/command/execute_multiple/'
        router = make_router()
        self.assertRedirects(self.client.post(url, {'routers[]': ['00000000-0000-0000-0000-000000000000']}),
                             '/router/list/', fetch_redirect_response=False)
        self.assertRedirects(self.client.post(url, {'command': str(self.command.uuid)}), '/router/list/',
                             fetch_redirect_response=False)
        self.assertRedirects(self.client.post(url, {'router_uuids': [str(router.uuid)]}), '/router/list/',
                             fetch_redirect_response=False)
        empty = Command.objects.create(name='No variants')
        self.assertRedirects(self.client.post(url, {'router_uuids': [str(router.uuid)], 'command': str(empty.uuid)}),
                             '/router/list/', fetch_redirect_response=False)
        disabled = make_router(enabled=False)
        response = self.client.post(url, {'router_uuids': [str(disabled.uuid)], 'command': str(self.command.uuid)},
                                    follow=True)
        self.assertIn('No targets', messages_of(response))
        self.assertRedirects(self.client.get(url), '/router/list/', fetch_redirect_response=False)

    def test_abort_task_without_confirmation_and_finished_task(self):
        self.client.force_login(make_user(20))
        job = CommandJob.objects.create(command=self.command)
        task = CommandTask.objects.create(job=job, router=make_router(), status='success')
        response = self.client.post(f'/fleet_commander/task/abort/?uuid={task.uuid}', {'confirmation': 'abort'}, follow=True)
        self.assertIn('Only a task that has not finished', messages_of(response))
        response = self.client.get(f'/fleet_commander/job/abort/?uuid={job.uuid}', follow=True)
        self.assertIn('Invalid confirmation', messages_of(response))

    def test_perform_tasks_stops_at_its_budget(self):
        for _ in range(2):
            job = CommandJob.objects.create(command=self.command)
            CommandTask.objects.create(job=job, router=make_router())
        start = timezone.now()
        times = iter([start, start + datetime.timedelta(seconds=50), start + datetime.timedelta(seconds=60)])
        with mock.patch('fleet_commander.views.execute_command_task'), \
                mock.patch('fleet_commander.views.timezone.now', side_effect=lambda: next(times)):
            data = self.client.get('/cron/perform_command_tasks/').json()
        self.assertEqual(data['tasks_performed'], 1)


class RouterViewBranchTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(40))

    def test_bulk_edit_every_field(self):
        profile = make_profile()
        key = make_sshkey()
        router = make_router(ssh_key=key, password='')
        response = self.client.post('/router/edit/multiple/', {
            'router_uuids': [str(router.uuid)], 'username': ' operator ', 'monitoring': 'false',
            'enabled': 'false', 'backup_profile': str(profile.uuid), 'internal_notes': 'rack 4',
            'ssh_key': 'clear', 'password': 'new-pw'})
        self.assertEqual(response.status_code, 302)
        router.refresh_from_db()
        self.assertEqual((router.username, router.monitoring, router.enabled, router.backup_profile,
                          router.internal_notes, router.ssh_key, router.password),
                         ('operator', False, False, profile, 'rack 4', None, 'new-pw'))

    def test_bulk_edit_clears_the_profile(self):
        router = make_router(backup_profile=make_profile())
        self.client.post('/router/edit/multiple/', {'router_uuids': [str(router.uuid)], 'backup_profile': 'clear'})
        router.refresh_from_db()
        self.assertIsNone(router.backup_profile)

    def test_bulk_edit_selection_of_nothing(self):
        self.assertRedirects(self.client.post('/router/edit/multiple/', {'routers[]': ['00000000-0000-0000-0000-000000000000']}),
                             '/router/list/', fetch_redirect_response=False)

    def test_groups_for_many(self):
        url = '/router/manage_groups/multiple/'
        router = make_router()
        self.client.post(url, {'routers[]': [str(router.uuid)]})
        response = self.client.get(url)
        self.assertEqual(list(response.context['routers']), [router])
        self.assertRedirects(self.client.post(url, {'routers[]': ['00000000-0000-0000-0000-000000000000']}),
                             '/router/list/', fetch_redirect_response=False)
        self.assertRedirects(self.client.post(url, {}), '/router/list/', fetch_redirect_response=False)
        missing = '00000000-0000-0000-0000-000000000000'
        response = self.client.post(url, {'router_uuids': [str(router.uuid)], 'add_group': missing,
                                          'remove_group': '11111111-1111-1111-1111-111111111111'}, follow=True)
        self.assertIn('Group not found', messages_of(response))
        self.client.post('/accounts/logout/')
        self.client.force_login(make_user(20))
        self.assertRedirects(self.client.get(url), '/router/list/', fetch_redirect_response=False)

    def test_router_delete_wrong_confirmation(self):
        router = make_router()
        self.client.force_login(make_user(30))
        self.client.post(f'/router/manage/?uuid={router.uuid}', {'action': 'delete', 'confirmation': 'nope'})
        self.assertTrue(Router.objects.filter(pk=router.pk).exists())

    def test_refresh_information_success(self):
        router = make_router()
        self.client.force_login(make_user(30))
        with mock.patch('router_manager.views.update_router_information', return_value=(True, '')):
            response = self.client.post(f'/router/manage/?uuid={router.uuid}', {'action': 'refresh_information'}, follow=True)
        self.assertIn('Device information updated', messages_of(response))

    def test_list_shows_the_last_status_change(self):
        router = make_router()
        RouterStatus.objects.filter(router=router).update(last_status_change=timezone.now())
        response = self.client.get('/router/list/')
        self.assertNotEqual(response.context['last_status_change_timestamp'], 0)

    def test_instant_backup_refused_message(self):
        self.client.force_login(make_user(20))
        router = make_router()
        response = self.client.post(f'/router/create_instant_backup/?uuid={router.uuid}', follow=True)
        self.assertIn('Device has no backup profile', messages_of(response))

    def test_information_cron_counts_failures(self):
        make_router()
        calls = iter([(False, 'unreachable')])

        def update(info):
            info.last_retrieval = timezone.now()
            info.save()
            return next(calls)
        with mock.patch('router_manager.views.update_router_information', side_effect=update):
            data = self.client.get('/cron/update_router_information/').json()
        self.assertEqual((data['updated'], data['failed']), (0, 1))


class BackupScheduleBranchTests(TestCase):
    @override_settings(TIME_ZONE='UTC')
    def test_weekly_and_monthly_filled_then_cleared(self):
        profile = make_profile(daily_backup=False, weekly_backup=True, monthly_backup=True)
        router = make_router(backup_profile=profile)
        self.client.get('/cron/generate_backup_schedule/')
        schedule = BackupSchedule.objects.get(router=router)
        self.assertIsNotNone(schedule.next_weekly_backup)
        self.assertIsNotNone(schedule.next_monthly_backup)
        profile.weekly_backup = profile.monthly_backup = False
        profile.save()
        data = self.client.get('/cron/generate_backup_schedule/').json()
        self.assertEqual((data['weekly_backup_schedule_removed'], data['monthly_backup_schedule_removed']), (1, 1))

    def test_weekly_and_monthly_calculation_errors_are_reported(self):
        profile = make_profile(daily_backup=False, weekly_backup=True, monthly_backup=True)
        make_router(backup_profile=profile)
        with mock.patch('backup_data.views.calculate_next_backup', return_value=(None, None, None)):
            self.client.get('/cron/generate_backup_schedule/')
        profile.refresh_from_db()
        self.assertIn('monthly', profile.profile_error_information)

    @override_settings(TIME_ZONE='UTC')
    def test_weekly_on_the_same_day_after_the_hour_is_next_week(self):
        from backup_data.views import next_weekday
        monday_noon = timezone.make_aware(datetime.datetime(2026, 6, 1, 12), datetime.timezone.utc)
        with mock.patch('django.utils.timezone.now', return_value=monday_noon):
            self.assertEqual(next_weekday(None, 0, 3).date(), datetime.date(2026, 6, 8))

    def test_housekeeping_keeps_the_lock_while_another_task_is_pending(self):
        router = make_router()
        old = timezone.now() - datetime.timedelta(hours=19)
        with mock.patch('django.utils.timezone.now', return_value=old):
            RouterBackup.objects.create(router=router, schedule_type='daily')
        RouterBackup.objects.create(router=router, schedule_type='daily')
        RouterStatus.objects.filter(router=router).update(backup_lock=timezone.now())
        self.client.get('/cron/housekeeping/')
        self.assertIsNotNone(RouterStatus.objects.get(router=router).backup_lock)


class MessageViewBranchTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(50))

    @override_settings(DEBUG=True)
    def test_debug_test_messages_for_a_router_and_a_backup(self):
        MessageChannel.objects.create(name='c', channel_type='ntfy', destination='t')
        router = make_router(status_online=False)
        backup = RouterBackup.objects.create(router=router, schedule_type='daily')
        with mock.patch('message_center.channels.requests.post', return_value=mock.Mock(status_code=200)):
            data = self.client.get(f'/debug/test_messages/?router_uuid={router.uuid}').json()
            self.assertIn(router.name, data['message'])
            data = self.client.get(f'/debug/test_messages/?backup_id={backup.id}').json()
            self.assertIn(str(backup.id), data['message'])

    @override_settings(TIME_ZONE='UTC')
    def test_daily_report_time_out_of_range_and_unreadable(self):
        settings = MessageSettings.objects.create(daily_report_time='99:99')
        data = self.client.get('/cron/daily_reports/').json()
        self.assertFalse(data['report_time_exception'])
        settings.daily_report_time = 'ab:cd'  # fits the field, but is no time
        settings.save()
        data = self.client.get('/cron/daily_reports/').json()
        self.assertTrue(data['report_time_exception'])

    @override_settings(TIME_ZONE='UTC')
    def test_daily_report_without_a_channel(self):
        MessageSettings.objects.create(daily_report_time='07:00')
        seven = timezone.now().replace(hour=7, minute=10, second=0, microsecond=0)
        with mock.patch('django.utils.timezone.now', return_value=seven):
            first = self.client.get('/cron/daily_reports/').json()
        self.assertEqual(first['no_channel_available'], 'backup_report ')
        MessageSettings.objects.update(last_daily_backup_report=seven)
        with mock.patch('django.utils.timezone.now', return_value=seven):
            second = self.client.get('/cron/daily_reports/').json()
        self.assertEqual(second['no_channel_available'], 'status_report')
        self.assertFalse(Message.objects.exists())

    def test_settings_and_channel_forms_save(self):
        response = self.client.post('/message_center/manage_settings/', {
            'max_length': 1500, 'max_retry': 2, 'retry_interval': 60, 'status_change_delay': 120,
            'backup_fails_delay': 600, 'daily_report_time': '06:30', 'concatenate_status_change': 'on'})
        self.assertRedirects(response, '/system/notifications/', fetch_redirect_response=False)
        self.assertEqual(MessageSettings.objects.get().daily_report_time, '06:30')
        with mock.patch('message_center.channels.requests.post', return_value=mock.Mock(status_code=200)):
            response = self.client.post('/system/notifications/channel/', {
                'name': 'alerts', 'channel_type': 'ntfy', 'destination': 'my-topic', 'enabled': 'on'})
        self.assertRedirects(response, '/system/notifications/', fetch_redirect_response=False)


class ImportBranchTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(50))

    def test_start_import_view_and_empty_error_cleanup(self):
        from import_tool.models import CsvData
        data = CsvData.objects.create(raw_csv_data='x', import_data=[])
        response = self.client.get(f'/router/import_tool/details/?uuid={data.uuid}&action=start_import')
        self.assertEqual(response.context['action'], 'start_import')
        response = self.client.post(f'/router/import_tool/details/?uuid={data.uuid}', {'action': 'delete_errors'}, follow=True)
        self.assertIn('No error tasks deleted', messages_of(response))
        response = self.client.post(f'/router/import_tool/details/?uuid={data.uuid}', {'action': 'create_tasks'}, follow=True)
        self.assertIn('No new tasks created', messages_of(response))

    def test_list_page(self):
        self.assertEqual(self.client.get('/router/import_tool/').status_code, 200)

    def test_an_invalid_address_is_refused(self):
        from import_tool.models import CsvData, ImportTask
        data = CsvData.objects.create(raw_csv_data='x', import_data=[])
        task = ImportTask.objects.create(csv_data=data, import_id=1, name='n', username='u', password='p',
                                         address='not a host', router_type='routeros')
        result = self.client.post(f'/router/import_tool/run_import_task/?uuid={task.uuid}').json()
        self.assertIn('valid hostname or IP address', result['error_message'])


class StorageStatisticsTests(TestCase):
    def test_directory_usage_and_warnings(self):
        from dashboard.views import get_directory_statistics
        with tempfile.TemporaryDirectory() as path:
            with open(os.path.join(path, 'backup.bin'), 'wb') as fh:
                fh.write(b'x' * 800)
            with mock.patch('dashboard.views.shutil.disk_usage', return_value=(1000, 0, 200)):
                stats = get_directory_statistics(path)
            self.assertEqual(stats['usage_percentage'], 80.0)
            self.assertIn('above 75%', stats['storage_warning'])
            with mock.patch('dashboard.views.shutil.disk_usage', return_value=(850, 0, 50)):
                self.assertIn('above 90%', get_directory_statistics(path)['storage_warning'])
            with mock.patch('dashboard.views.shutil.disk_usage', return_value=(0, 0, 0)):
                self.assertEqual(get_directory_statistics(path)['usage_percentage'], 0)


class PruneScriptNetworkTests(TestCase):
    """scripts/prune_images.py against a faked GitHub API and registry."""

    def script(self):
        import importlib.util
        import pathlib
        spec = importlib.util.spec_from_file_location(
            'prune_images', pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'prune_images.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fake_request(self, versions, manifests):
        calls = []

        def request(url, token=None, method='GET', accept=None):
            calls.append((method, url))
            if 'ghcr.io/token' in url:
                return {'token': 'anon'}
            if '/manifests/' in url:
                return manifests[url.rsplit('/', 1)[1]]
            if method == 'DELETE':
                return None
            page = int(url.rsplit('page=', 1)[1])
            return versions if page == 1 else []
        return request, calls

    def api_versions(self):
        return [
            {'id': 1, 'name': 'sha256:idx', 'metadata': {'container': {'tags': ['1.0.0', 'latest']}}},
            {'id': 2, 'name': 'sha256:amd', 'metadata': {'container': {'tags': []}}},
            {'id': 3, 'name': 'sha256:junk', 'metadata': {'container': {'tags': []}}},
        ]

    def run_main(self, module, *args):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.dict(os.environ, {'GH_TOKEN': 'x'}):
            module.main(['prune_images.py', *args])
        return out.getvalue()

    def test_deletes_only_what_no_release_needs(self):
        module = self.script()
        manifests = {'1.0.0': {'mediaType': module.INDEX_TYPES[0], 'manifests': [{'digest': 'sha256:amd'}]},
                     'latest': {'mediaType': module.INDEX_TYPES[0], 'manifests': [{'digest': 'sha256:amd'}]}}
        request, calls = self.fake_request(self.api_versions(), manifests)
        with mock.patch.object(module, '_request', side_effect=request):
            out = self.run_main(module, 'routerfleet', '1.0.0')
        deletes = [url for method, url in calls if method == 'DELETE']
        self.assertEqual(len(deletes), 1)
        self.assertTrue(deletes[0].endswith('/versions/3'))
        self.assertIn('1 deleted', out)

    def test_dry_run_deletes_nothing(self):
        module = self.script()
        manifests = {'1.0.0': {'manifests': []}, 'latest': {'manifests': []}}
        request, calls = self.fake_request(self.api_versions(), manifests)
        with mock.patch.object(module, '_request', side_effect=request):
            out = self.run_main(module, 'routerfleet', '1.0.0', '--dry-run')
        self.assertFalse([c for c in calls if c[0] == 'DELETE'])
        self.assertIn('would delete', out)

    def test_refuses_when_the_new_release_is_missing(self):
        module = self.script()
        request, calls = self.fake_request(self.api_versions(), {})
        with mock.patch.object(module, '_request', side_effect=request), self.assertRaises(SystemExit):
            self.run_main(module, 'routerfleet', '2.0.0')
        self.assertFalse([c for c in calls if c[0] == 'DELETE'])

    def test_needs_a_token_and_two_arguments(self):
        module = self.script()
        with self.assertRaises(SystemExit):
            module.main(['prune_images.py', 'only-one'])
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit):
            module.main(['prune_images.py', 'img', '0.9.0'])
