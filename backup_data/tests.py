import datetime
import hashlib
import os
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from audit_log.models import ChangeRecord
from backup_data import views as cron
from backup_data.models import RouterBackup
from message_center.models import Message, MessageChannel, Notification
from router_manager.models import BackupSchedule, RouterStatus
from routerfleet.testing import (edit_type, FakeSCPClient, FakeSSHClient, make_profile, make_router,
                                 temporary_media_root)
from routerlib import backup_functions

SSH = 'routerlib.backup_functions'


def at(moment):
    return mock.patch('django.utils.timezone.now', return_value=moment)


class ChangeDetectionTests(TestCase):
    def setUp(self):
        self.router = make_router()
        self.t0 = timezone.now() - datetime.timedelta(days=10)

    def backup(self, text, minutes):
        with at(self.t0 + datetime.timedelta(minutes=minutes)):
            return RouterBackup.objects.create(router=self.router, schedule_type='daily', backup_text=text)

    def test_hash_is_written_on_save(self):
        backup = self.backup('cfg', 0)
        self.assertEqual(backup.backup_text_hash, hashlib.sha256(b'cfg').hexdigest())
        self.assertEqual(self.backup(None, 1).backup_text_hash, '')

    def test_first_backup_is_the_baseline_not_a_change(self):
        self.assertFalse(self.backup('cfg A', 0).config_change_detected)

    def test_identical_is_not_a_change_different_is(self):
        self.backup('cfg A', 0)
        self.assertFalse(self.backup('cfg A', 1).config_change_detected)
        self.assertTrue(self.backup('cfg B', 2).config_change_detected)

    def test_failed_backup_is_looked_past(self):
        first = self.backup('cfg A', 0)
        self.backup(None, 1)
        later = self.backup('cfg A', 2)
        self.assertFalse(later.config_change_detected)
        self.assertEqual(later.previous_backup(), first)

    def test_comparison_only_looks_backwards(self):
        early = self.backup(None, 0)          # still waiting for its text
        self.backup('cfg B', 5)               # a later backup finished first
        early.backup_text = 'cfg A'           # the early one finishes now
        early.save()
        self.assertIsNone(early.previous_backup())
        self.assertFalse(early.config_change_detected)

    def test_other_devices_do_not_count(self):
        self.backup('cfg A', 0)
        other = make_router()
        with at(self.t0 + datetime.timedelta(minutes=1)):
            backup = RouterBackup.objects.create(router=other, schedule_type='daily', backup_text='cfg B')
        self.assertFalse(backup.config_change_detected)


class NextBackupTests(TestCase):
    """The moments are computed in the installation's timezone."""

    @override_settings(TIME_ZONE='UTC')
    def test_daily_skips_disabled_weekdays(self):
        profile = make_profile(daily_hour=3)
        for day in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday'):
            setattr(profile, f'daily_day_{day}', False)
        # Monday 2026-06-01 10:00 UTC
        with at(timezone.make_aware(datetime.datetime(2026, 6, 1, 10), datetime.timezone.utc)):
            daily, weekly, monthly = cron.calculate_next_backup(profile)
        self.assertEqual(daily.date(), datetime.date(2026, 6, 6))  # Saturday
        self.assertEqual(daily.hour, 3)
        self.assertIsNone(weekly)
        self.assertIsNone(monthly)

    @override_settings(TIME_ZONE='UTC')
    def test_daily_later_today(self):
        profile = make_profile(daily_hour=23)
        with at(timezone.make_aware(datetime.datetime(2026, 6, 1, 10), datetime.timezone.utc)):
            daily, _, _ = cron.calculate_next_backup(profile)
        self.assertEqual((daily.date(), daily.hour), (datetime.date(2026, 6, 1), 23))

    @override_settings(TIME_ZONE='UTC')
    def test_weekly_and_monthly(self):
        profile = make_profile(weekly_backup=True, weekly_day='sunday', weekly_hour=1,
                               monthly_backup=True, monthly_day=1, monthly_hour=0)
        with at(timezone.make_aware(datetime.datetime(2026, 6, 1, 10), datetime.timezone.utc)):
            _, weekly, monthly = cron.calculate_next_backup(profile)
        self.assertEqual((weekly.date(), weekly.hour), (datetime.date(2026, 6, 7), 1))
        self.assertEqual((monthly.date(), monthly.hour), (datetime.date(2026, 7, 1), 0))

    @override_settings(TIME_ZONE='UTC')
    def test_monthly_in_december_rolls_into_january(self):
        profile = make_profile(monthly_backup=True, monthly_day=1, monthly_hour=0)
        with at(timezone.make_aware(datetime.datetime(2026, 12, 5, 10), datetime.timezone.utc)):
            _, _, monthly = cron.calculate_next_backup(profile)
        self.assertEqual(monthly.date(), datetime.date(2027, 1, 1))


class ScheduleGenerationTests(TestCase):
    def test_schedules_are_created_filled_and_cleared(self):
        profile = make_profile(daily_backup=True, weekly_backup=False)
        router = make_router(backup_profile=profile)
        response = self.client.get('/cron/generate_backup_schedule/')
        self.assertEqual(response.json()['backup_schedule_created'], 1)
        schedule = BackupSchedule.objects.get(router=router)
        self.assertIsNotNone(schedule.next_daily_backup)
        self.assertIsNone(schedule.next_weekly_backup)

        profile.daily_backup = False
        profile.save()
        self.client.get('/cron/generate_backup_schedule/')
        schedule.refresh_from_db()
        self.assertIsNone(schedule.next_daily_backup)

    def test_impossible_daily_schedule_is_reported_on_the_profile(self):
        profile = make_profile(daily_backup=True)
        for day in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'):
            setattr(profile, f'daily_day_{day}', False)
        profile.save()
        make_router(backup_profile=profile)
        self.client.get('/cron/generate_backup_schedule/')
        profile.refresh_from_db()
        self.assertIn('daily', profile.profile_error_information)


class TaskCreationTests(TestCase):
    def setUp(self):
        self.profile = make_profile()
        self.past = timezone.now() - datetime.timedelta(minutes=1)

    def due_router(self, **fields):
        router = make_router(backup_profile=self.profile, **fields)
        BackupSchedule.objects.create(router=router, next_daily_backup=self.past,
                                      next_weekly_backup=self.past, next_monthly_backup=self.past)
        return router

    def test_longest_retained_kind_wins_when_moments_coincide(self):
        router = self.due_router()
        data = self.client.get('/cron/create_backup_tasks/').json()
        self.assertEqual((data['monthly_backup_tasks_created'], data['weekly_backup_tasks_created'],
                          data['daily_backup_tasks_created']), (1, 0, 0))
        backup = RouterBackup.objects.get(router=router)
        self.assertEqual(backup.schedule_type, 'monthly')
        status = RouterStatus.objects.get(router=router)
        self.assertEqual(status.backup_lock, backup.schedule_time)

    def test_offline_monitored_device_is_not_attempted(self):
        self.due_router(status_online=False)
        self.client.get('/cron/create_backup_tasks/')
        self.assertFalse(RouterBackup.objects.exists())

    def test_unmonitored_device_is_attempted_whatever_its_status(self):
        self.due_router(status_online=False, monitoring=False)
        self.client.get('/cron/create_backup_tasks/')
        self.assertTrue(RouterBackup.objects.exists())

    def test_disabled_and_locked_devices_are_skipped(self):
        self.due_router(enabled=False)
        locked = self.due_router()
        RouterStatus.objects.filter(router=locked).update(backup_lock=timezone.now())
        self.client.get('/cron/create_backup_tasks/')
        self.assertFalse(RouterBackup.objects.exists())


RSC = '# 2026-06-01 header comment\n/ip address\nadd address=10.0.0.1/24\n'


def with_routeros_binary():
    """The RouterOS file commands of an installation that kept the binary
    .backup (shipped until 0.22): text from /export, the .backup downloaded."""
    from fleet_commander.models import CommandVariant
    for key, payload in (('backup_create', '/system backup save name={{ backup_name }}.backup'),
                         ('backup_download', '/{{ backup_name }}.backup'),
                         ('backup_remove', '/file remove "{{ backup_name }}.backup"')):
        CommandVariant.objects.filter(command__system_key=key, router_type='routeros').update(payload=payload)


class PipelineTests(TestCase):
    def setUp(self):
        self.profile = make_profile(parameter_sensitive=True, parameter_terse=True)
        FakeSCPClient.files = {}
        FakeSCPClient.fetched = []

    def make_backup(self, router_type):
        router = make_router(router_type=router_type, backup_profile=self.profile)
        RouterStatus.objects.filter(router=router).update(backup_lock=timezone.now())
        return RouterBackup.objects.create(router=router, schedule_type='instant',
                                           schedule_time=timezone.now())

    def run_phase(self, backup, client):
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client), \
                mock.patch(f'{SSH}.SafeSCPClient', FakeSCPClient):
            backup_functions.perform_backup(backup)
        backup.refresh_from_db()
        return backup

    def test_routeros_text_only_in_one_step(self):
        backup = self.make_backup('routeros')
        # The text backup is the output of /export, with the profile's options
        client = FakeSSHClient({'/export show-sensitive terse': RSC})
        with temporary_media_root():
            backup = self.run_phase(backup, client)
        self.assertTrue(backup.success, backup.error_message)
        self.assertFalse(backup.backup_pending_retrieval)
        self.assertEqual(client.commands[0], '/export show-sensitive terse')
        self.assertNotIn('/system backup save', ' '.join(client.commands), 'no binary backup any more')
        self.assertEqual(FakeSCPClient.fetched, [], 'nothing downloaded')
        self.assertFalse(backup.backup_binary)
        # Comment lines are stripped, so a header timestamp is not a change
        self.assertNotIn('header comment', backup.backup_text)
        self.assertIn('add address=10.0.0.1/24', backup.backup_text)
        self.assertEqual(backup.backup_text_name, 'configuration')
        self.assertIn('[stdout suppressed]', backup.task_console_output)
        self.assertIn('file remove [find where name~"routerfleet-backup-"]', client.commands)
        status = RouterStatus.objects.get(router=backup.router)
        self.assertIsNone(status.backup_lock)
        self.assertIsNotNone(status.last_backup)

    def set_os(self, backup, version):
        from router_manager.models import RouterInformation
        RouterInformation.objects.update_or_create(router=backup.router, defaults={'os_version': version})

    def test_a_refused_export_fails_and_is_never_stored(self):
        # RouterOS answers a command it does not understand with exit code 1
        # and the error on stdout - that line must not become the backup
        backup = self.make_backup('routeros')
        self.set_os(backup, '6.49.21 (long-term)')
        client = FakeSSHClient({'/export terse': (1, 'expected end of command (line 1 column 10)\n', '')})
        with temporary_media_root():
            backup = self.run_phase(backup, client)
        self.assertFalse(backup.success)
        self.assertFalse(backup.backup_text)
        self.assertIn('refused', backup.error_message)
        self.assertIn('expected end of command', backup.error_message)
        self.assertIsNotNone(backup.next_retry)

    def test_routeros_6_includes_passwords_without_show_sensitive(self):
        backup = self.make_backup('routeros')
        self.set_os(backup, '6.49.21 (long-term)')
        client = FakeSSHClient({'/export terse': RSC})
        with temporary_media_root():
            backup = self.run_phase(backup, client)
        self.assertTrue(backup.success, backup.error_message)
        self.assertEqual(client.commands[0], '/export terse', 'RouterOS 6 exports passwords by default')

    def test_routeros_6_leaves_passwords_out_with_hide_sensitive(self):
        self.profile.parameter_sensitive = False
        self.profile.save()
        backup = self.make_backup('routeros')
        self.set_os(backup, '6.49.21 (long-term)')
        client = FakeSSHClient({'/export hide-sensitive terse': RSC})
        with temporary_media_root():
            backup = self.run_phase(backup, client)
        self.assertTrue(backup.success, backup.error_message)
        self.assertEqual(client.commands[0], '/export hide-sensitive terse')

    def test_routeros_7_and_unknown_versions_use_the_current_words(self):
        from routerlib.backup_functions import export_words
        from router_manager import device_types
        routeros = device_types.get('routeros')
        self.assertEqual(export_words(routeros, '7.16.2 (stable)'), ('show-sensitive', ''))
        self.assertEqual(export_words(routeros, '7.0'), ('show-sensitive', ''))
        self.assertEqual(export_words(routeros, ''), ('show-sensitive', ''))
        self.assertEqual(export_words(routeros, None), ('show-sensitive', ''))
        self.assertEqual(export_words(routeros, '6.49.21 (long-term)'), ('', 'hide-sensitive'))
        self.assertEqual(export_words(routeros, '6.48beta3'), ('', 'hide-sensitive'))
        # With any release channel, as field reports show them
        for version in ('6.49.23 (stable)', '6.49.21 (long-term)', '6.49.18 (testing)', '6.49'):
            self.assertEqual(export_words(routeros, version), ('', 'hide-sensitive'), version)
        for version in ('7.25.1 (stable)', '7.24.5 (long-term)', '7.26beta1 (testing)'):
            self.assertEqual(export_words(routeros, version), ('show-sensitive', ''), version)

    def test_routeros_with_the_binary_kept(self):
        with_routeros_binary()
        backup = self.make_backup('routeros')
        name = backup_functions.gen_backup_name(backup)
        client = FakeSSHClient({'/export show-sensitive terse': RSC})
        with temporary_media_root() as media:
            backup = self.run_phase(backup, client)
            self.assertTrue(backup.backup_pending_retrieval)
            FakeSCPClient.files = {f'/{name}.backup': b'\x00binary'}
            backup = self.run_phase(backup, client)
            self.assertTrue(backup.success)
            self.assertTrue(backup.backup_binary.path.startswith(media))
            self.assertIn(f'/file remove "{name}.backup"', client.commands)
        self.assertIn('=== Retrieving backup files ===', backup.task_console_output)

    def test_an_edited_routeros_backup_keeps_its_exported_file(self):
        """An installation whose RouterOS backup commands were edited keeps them:
        no text command, and the downloaded .rsc is still the text backup."""
        from fleet_commander.models import CommandVariant
        CommandVariant.objects.filter(command__system_key='backup_text', router_type='routeros').update(payload='')
        CommandVariant.objects.filter(command__system_key='backup_create', router_type='routeros').update(
            payload='/system backup save name={{ backup_name }}.backup\n/export file={{ backup_name }}.rsc')
        CommandVariant.objects.filter(command__system_key='backup_download', router_type='routeros').update(
            payload='/{{ backup_name }}.rsc\n/{{ backup_name }}.backup')
        backup = self.make_backup('routeros')
        name = backup_functions.gen_backup_name(backup)
        client = FakeSSHClient()
        with temporary_media_root():
            backup = self.run_phase(backup, client)
            FakeSCPClient.files = {f'/{name}.rsc': RSC, f'/{name}.backup': b'\x00binary'}
            backup = self.run_phase(backup, client)
        self.assertTrue(backup.success)
        self.assertIn('add address=10.0.0.1/24', backup.backup_text)
        self.assertNotIn('header comment', backup.backup_text)

    def test_named_text_backups_are_kept_apart_and_all_count_as_change(self):
        from fleet_commander.models import CommandVariant
        backup = self.make_backup('juniper-junos')
        router = backup.router
        Router = type(router)
        # Juniper talks through a shell; the fake client answers per command here
        edit_type('juniper-junos', session_mode='exec')
        client = FakeSSHClient({'show configuration': '## Last commit: 2026-11-22\nsystem { host-name r1; }\n',
                                'show configuration | display set': 'set system host-name r1\n'})
        backup = self.run_phase(backup, client)
        # Nothing to download: finished in one step
        self.assertTrue(backup.success, backup.error_message)
        self.assertEqual(backup.backup_text_name, 'configuration')
        self.assertNotIn('Last commit', backup.backup_text, "the type's noise lines are dropped")
        self.assertEqual(backup.extra_texts, [{'name': 'set', 'text': 'set system host-name r1\n'}])
        self.assertEqual([n for n, _ in backup.named_texts()], ['configuration', 'set'])
        # Only the set format changes: still a change
        RouterStatus.objects.filter(router=router).update(backup_lock=timezone.now())
        second = RouterBackup.objects.create(router=router, schedule_type='instant', schedule_time=timezone.now())
        client.responses['show configuration | display set'] = 'set system host-name r2\n'
        second = self.run_phase(second, client)
        self.assertTrue(second.success, second.error_message)
        self.assertTrue(second.config_change_detected)

    def test_openwrt_text_comes_from_phase_one(self):
        backup = self.make_backup('openwrt')
        name = backup_functions.gen_backup_name(backup)
        client = FakeSSHClient({'uci export': "package system\nconfig system\n"})
        with temporary_media_root():
            backup = self.run_phase(backup, client)
            self.assertIn('config system', backup.backup_text)
            self.assertIn(f'sysupgrade --create-backup /tmp/{name}.tar.gz', client.commands)
            # The configuration itself is not echoed into the console log
            self.assertIn('[stdout suppressed]', backup.task_console_output)
            FakeSCPClient.files = {f'/tmp/{name}.tar.gz': b'tar'}
            backup = self.run_phase(backup, client)
        self.assertTrue(backup.success)

    def test_openwrt_empty_export_fails(self):
        backup = self.make_backup('openwrt')
        backup = self.run_phase(backup, FakeSSHClient({'uci export': ''}))
        self.assertEqual(backup.retry_count, 1)
        self.assertIn("the text backup 'configuration' is empty", backup.error_message)

    def test_airos_reads_system_cfg_over_the_terminal_with_latin1_fallback(self):
        # Since 0.37.3 airOS takes its text from command output, like RouterOS:
        # no SCP; a configuration written in Latin-1 keeps its characters
        backup = self.make_backup('ubiquiti-airos')
        client = FakeSSHClient({'cat /tmp/system.cfg': (0, 'wireless.1.ssid=caf\xe9\n'.encode('latin-1'), '')})
        backup = self.run_phase(backup, client)
        self.assertEqual(client.commands, ['cat /tmp/system.cfg'])
        FakeSCPClient.files = {}
        backup = self.run_phase(backup, client)
        self.assertTrue(backup.success)
        self.assertIn('caf\xe9', backup.backup_text)
        self.assertIn('[decode=latin-1 fallback]', backup.task_console_output)
        self.assertEqual(FakeSCPClient.fetched, [], 'nothing downloaded over SCP')

    def test_a_binary_file_comes_over_the_command_channel_and_scp_only_when_it_must(self):
        from routerlib.backup_functions import CAT_MARKER
        backup = self.make_backup('openwrt')
        name = backup_functions.gen_backup_name(backup)
        archive = bytes(range(256)) * 4   # binary, every byte value
        client = FakeSSHClient({'uci export': 'config system\n',
                                f"cat -- /tmp/{name}.tar.gz && echo {CAT_MARKER} >&2": (0, archive, CAT_MARKER + '\n')})
        FakeSCPClient.files, FakeSCPClient.fetched = {}, []
        with temporary_media_root():
            backup = self.run_phase(backup, client)
            backup = self.run_phase(backup, client)
            self.assertTrue(backup.success, backup.error_message)
            with backup.backup_binary.open('rb') as stored:
                self.assertEqual(stored.read(), archive, 'byte for byte')
        self.assertEqual(FakeSCPClient.fetched, [], 'cat worked: no SCP')

    def test_a_device_without_a_shell_falls_back_to_scp_and_its_error_is_never_the_backup(self):
        # RouterOS has no cat and may answer with exit status 0 - no marker, so SCP
        from fleet_commander.models import CommandVariant
        backup = self.make_backup('routeros')
        name = backup_functions.gen_backup_name(backup)
        CommandVariant.objects.filter(command__system_key='backup_download', router_type='routeros').update(
            payload='/{{ backup_name }}.rsc')
        def device(command):
            if command.startswith('cat '):
                return (0, 'bad command name cat (line 1 column 1)', '')
            return (0, '/ip address\nadd address=10.0.0.1/24\n', '')
        client = FakeSSHClient({}, default=device)
        FakeSCPClient.files, FakeSCPClient.fetched = {f'/{name}.rsc': b'# exported\n'}, []
        with temporary_media_root():
            backup = self.run_phase(backup, client)
            backup = self.run_phase(backup, client)
            self.assertTrue(backup.success, backup.error_message)
            stored = backup.backup_binary.read() if backup.backup_binary else b''
        self.assertNotIn('bad command name', backup.backup_text or '')
        self.assertNotIn(b'bad command name', stored)
        self.assertTrue(FakeSCPClient.fetched, 'fell back to SCP')

    def test_failure_retries_after_retry_interval_minutes(self):
        backup = self.make_backup('routeros')
        now = timezone.now()
        with at(now), mock.patch('routerlib.device_session.connect_to_ssh', side_effect=OSError('refused')):
            backup_functions.perform_backup(backup)
        backup.refresh_from_db()
        self.assertEqual(backup.retry_count, 1)
        self.assertEqual(backup.next_retry, now + datetime.timedelta(minutes=self.profile.retry_interval))
        self.assertIn('refused', backup.error_message)

    def test_retrieval_failure(self):
        backup = self.make_backup('routeros')
        backup.backup_pending_retrieval = True
        backup.save()
        backup = self.run_phase(backup, FakeSSHClient())
        self.assertIn('Failed to retrieve backup files', backup.error_message)
        self.assertEqual(backup.retry_count, 1)

    def test_exhausted_retries_become_an_error_and_notify(self):
        MessageChannel.objects.create(name='c', channel_type='ntfy', destination='t')
        backup = self.make_backup('routeros')
        backup.retry_count = self.profile.max_retry + 1
        backup.save()
        backup_functions.perform_backup(backup)
        backup.refresh_from_db()
        self.assertTrue(backup.error)
        self.assertTrue(Notification.objects.filter(notification_type='backup_fail').exists())
        self.assertIsNone(RouterStatus.objects.get(router=backup.router).backup_lock)

    def test_device_without_profile_fails_immediately(self):
        router = make_router()
        backup = RouterBackup.objects.create(router=router, schedule_type='instant')
        backup_functions.perform_backup(backup)
        backup.refresh_from_db()
        self.assertTrue(backup.error)
        self.assertEqual(backup.error_message, 'No backup profile assigned')

    def test_finished_backup_only_releases_the_lock(self):
        backup = self.make_backup('routeros')
        backup.success = True
        backup.save()
        with mock.patch('routerlib.device_session.connect_to_ssh') as connect:
            backup_functions.perform_backup(backup)
        connect.assert_not_called()
        self.assertIsNone(RouterStatus.objects.get(router=backup.router).backup_lock)

    def test_unsupported_type(self):
        backup = self.make_backup('monitoring')
        backup = self.run_phase(backup, FakeSSHClient())
        self.assertIn('not supported', backup.error_message)


class PerformTasksCronTests(TestCase):
    def test_runs_due_tasks_and_releases_the_task_lock(self):
        profile = make_profile()
        router = make_router(backup_profile=profile)
        due = RouterBackup.objects.create(router=router, schedule_type='daily',
                                          schedule_time=timezone.now() - datetime.timedelta(minutes=1))
        future = RouterBackup.objects.create(router=make_router(backup_profile=profile), schedule_type='daily',
                                             schedule_time=timezone.now() + datetime.timedelta(hours=1))
        with mock.patch('backup_data.views.perform_backup') as perform:
            data = self.client.get('/cron/perform_backup_tasks/').json()
        self.assertEqual(data['backup_tasks_performed'], 1)
        perform.assert_called_once()
        self.assertEqual(perform.call_args.args[0].pk, due.pk)
        due.refresh_from_db()
        self.assertIsNone(due.task_lock)
        future.refresh_from_db()
        self.assertIsNone(future.task_lock)

    def test_one_minute_interval_does_one_device_per_pass(self):
        profile = make_profile(backup_interval=60)
        for _ in range(3):
            RouterBackup.objects.create(router=make_router(backup_profile=profile), schedule_type='daily',
                                        schedule_time=timezone.now() - datetime.timedelta(minutes=1))
        with mock.patch('backup_data.views.perform_backup'):
            data = self.client.get('/cron/perform_backup_tasks/').json()
        self.assertEqual(data['backup_tasks_performed'], 1)

    def test_short_interval_sleeps_between_devices(self):
        profile = make_profile(backup_interval=5)
        for _ in range(2):
            RouterBackup.objects.create(router=make_router(backup_profile=profile), schedule_type='daily',
                                        schedule_time=timezone.now() - datetime.timedelta(minutes=1))
        with mock.patch('backup_data.views.perform_backup'), \
                mock.patch('backup_data.views.time.sleep') as sleep:
            data = self.client.get('/cron/perform_backup_tasks/').json()
        self.assertEqual(data['backup_tasks_performed'], 2)
        sleep.assert_called_with(5)

    def test_offline_monitored_device_is_skipped(self):
        RouterBackup.objects.create(router=make_router(backup_profile=make_profile(), status_online=False),
                                    schedule_type='daily',
                                    schedule_time=timezone.now() - datetime.timedelta(minutes=1))
        with mock.patch('backup_data.views.perform_backup') as perform:
            self.client.get('/cron/perform_backup_tasks/')
        perform.assert_not_called()


class HousekeepingTests(TestCase):
    def setUp(self):
        self.channel = MessageChannel.objects.create(name='c', channel_type='ntfy', destination='t')
        self.now = timezone.now()

    def old(self, hours=0, days=0):
        return self.now - datetime.timedelta(hours=hours, days=days)

    def sweep(self):
        return self.client.get('/cron/housekeeping/').json()

    def test_stale_task_lock_is_cleared_and_reported(self):
        backup = RouterBackup.objects.create(router=make_router(), schedule_type='daily',
                                             task_lock=self.old(hours=7))
        data = self.sweep()
        self.assertEqual(data['backup_task_locks_expired'], 1)
        backup.refresh_from_db()
        self.assertIsNone(backup.task_lock)
        self.assertIn('Backup task expired', backup.task_console_output)
        self.assertTrue(Message.objects.filter(subject__startswith='Backup lock expired').exists())

    def test_unfinished_task_expires_after_18_hours(self):
        router = make_router()
        with at(self.old(hours=19)):
            backup = RouterBackup.objects.create(router=router, schedule_type='daily')
        RouterStatus.objects.filter(router=router).update(backup_lock=self.old(hours=19))
        data = self.sweep()
        self.assertEqual(data['backup_tasks_expired'], 1)
        backup.refresh_from_db()
        self.assertEqual((backup.error, backup.error_message), (True, 'Backup task expired'))
        status = RouterStatus.objects.get(router=router)
        self.assertIsNone(status.backup_lock)
        self.assertIsNotNone(status.last_backup_failed)

    def test_orphaned_locks(self):
        idle = make_router()
        RouterStatus.objects.filter(router=idle).update(backup_lock=self.old(hours=19),
                                                        command_lock=self.old(hours=3))
        data = self.sweep()
        self.assertEqual((data['backup_locks_removed'], data['command_locks_removed']), (1, 1))
        status = RouterStatus.objects.get(router=idle)
        self.assertIsNone(status.backup_lock)
        self.assertIsNone(status.command_lock)

    def test_retention_per_kind_and_one_audit_entry(self):
        profile = make_profile(daily_retention=7, weekly_retention=30, retain_backups_on_error=False)
        router = make_router(backup_profile=profile)
        with at(self.old(days=8)):
            old_daily = RouterBackup.objects.create(router=router, schedule_type='daily', success=True)
            kept_weekly = RouterBackup.objects.create(router=router, schedule_type='weekly', success=True)
        recent_daily = RouterBackup.objects.create(router=router, schedule_type='daily', success=True)
        before = ChangeRecord.objects.count()
        self.sweep()
        remaining = set(RouterBackup.objects.values_list('pk', flat=True))
        self.assertNotIn(old_daily.pk, remaining)
        self.assertIn(kept_weekly.pk, remaining)
        self.assertIn(recent_daily.pk, remaining)
        entries = ChangeRecord.objects.order_by('-created')[:ChangeRecord.objects.count() - before]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].note, 'Retention')
        self.assertEqual(entries[0].affected_count, 1)
        self.assertEqual(entries[0].actor_kind, 'cron')

    def test_retention_counts_what_a_delete_took_with_it(self):
        profile = make_profile(daily_retention=1, retain_backups_on_error=False)
        router = make_router(backup_profile=profile)
        with at(self.old(days=5)):
            backup = RouterBackup.objects.create(router=router, schedule_type='daily', success=True)
        Notification.objects.create(notification_type='backup_fail', router=router, router_backup=backup)
        before = ChangeRecord.objects.count()
        self.sweep()
        entry = ChangeRecord.objects.order_by('-created')[:ChangeRecord.objects.count() - before][0]
        # The notification went with the backup by cascade, and is as removed
        self.assertEqual(entry.changes, {'RouterBackup': {'old': None, 'new': 1},
                                         'Notification': {'old': None, 'new': 1}})
        self.assertEqual(entry.affected_count, 2)

    def test_retention_is_suspended_for_a_device_whose_last_backup_failed(self):
        profile = make_profile(daily_retention=1)  # retain_backups_on_error defaults to True
        router = make_router(backup_profile=profile)
        RouterStatus.objects.filter(router=router).update(last_backup_failed=self.now)
        with at(self.old(days=5)):
            backup = RouterBackup.objects.create(router=router, schedule_type='daily')
        self.sweep()
        self.assertTrue(RouterBackup.objects.filter(pk=backup.pk).exists())

    def test_default_profile_covers_devices_without_one(self):
        make_profile(name='default', daily_retention=1, retain_backups_on_error=False)
        router = make_router()
        with at(self.old(days=5)):
            backup = RouterBackup.objects.create(router=router, schedule_type='daily')
        self.sweep()
        self.assertFalse(RouterBackup.objects.filter(pk=backup.pk).exists())

    def test_quiet_pass_writes_no_audit_entry(self):
        before = ChangeRecord.objects.count()
        self.sweep()
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_old_messages_are_removed(self):
        with at(self.old(days=31)):
            Message.objects.create(channel=self.channel, message='old')
        Message.objects.create(channel=self.channel, message='new')
        self.assertEqual(self.sweep()['messages_removed'], 1)
        self.assertEqual(list(Message.objects.values_list('message', flat=True)), ['new'])


class RetrievalLeavesNothingBehindTests(TestCase):
    """The files a retrieval fetches live in a directory of their own, and that
    directory is gone afterwards - after a failure as much as after success."""

    def setUp(self):
        with_routeros_binary()
        import tempfile
        self.parent = tempfile.mkdtemp(prefix='rf-test-')
        self.created = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(prefix=''):
            path = real_mkdtemp(prefix=prefix, dir=self.parent)
            if prefix.startswith('routerfleet-backup-'):
                self.created.append(path)
            return path
        patcher = mock.patch('routerlib.backup_functions.tempfile.mkdtemp', side_effect=tracking_mkdtemp)
        patcher.start()
        self.addCleanup(patcher.stop)
        FakeSCPClient.files = {}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.parent, ignore_errors=True)

    def retrieve(self, files):
        backup = RouterBackup.objects.create(router=make_router(router_type='routeros', backup_profile=make_profile()),
                                             schedule_type='instant', backup_pending_retrieval=True)
        FakeSCPClient.files = files(backup_functions.gen_backup_name(backup))
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=FakeSSHClient()), \
                mock.patch(f'{SSH}.SafeSCPClient', FakeSCPClient), temporary_media_root():
            return backup_functions.retrieve_backup(backup)

    def test_after_success(self):
        ok, _ = self.retrieve(lambda name: {f'/{name}.rsc': '/ip\n', f'/{name}.backup': b'bin'})
        self.assertTrue(ok)
        self.assertEqual(len(self.created), 1)
        self.assertFalse(os.path.exists(self.created[0]))

    def test_after_a_failure_halfway(self):
        # The text arrives, the binary does not: the configuration was on disk
        ok, error = self.retrieve(lambda name: {f'/{name}.rsc': 'secret config\n'})
        self.assertFalse(ok)
        self.assertIn('Failed to retrieve backup files', error)
        self.assertFalse(os.path.exists(self.created[0]), 'the fetched configuration was left behind')


class KeepOnlyChangesPipelineTests(TestCase):
    """Through the real pipeline - execute, retrieve, success - not only the
    model method: an unchanged scheduled backup ends recorded, its binary file
    gone from the disk; a changed one is stored in full."""

    def setUp(self):
        with_routeros_binary()
        self.profile = make_profile()
        self.router = make_router(router_type='routeros', backup_profile=self.profile)
        FakeSCPClient.files = {}
        FakeSCPClient.fetched = []

    def run_backup(self, rsc, kind='daily'):
        RouterStatus.objects.filter(router=self.router).update(backup_lock=timezone.now())
        backup = RouterBackup.objects.create(router=self.router, schedule_type=kind, schedule_time=timezone.now())
        name = backup_functions.gen_backup_name(backup)
        client = FakeSSHClient({'/export': rsc})
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client), mock.patch(f'{SSH}.SafeSCPClient', FakeSCPClient):
            backup_functions.perform_backup(backup)
            FakeSCPClient.files = {f'/{name}.backup': b'\x00binary-' + name.encode()}
            backup_functions.perform_backup(backup)
        backup.refresh_from_db()
        self.assertTrue(backup.success)
        return backup

    def files_on_disk(self, media):
        directory = os.path.join(media, 'backups')
        return sorted(os.listdir(directory)) if os.path.isdir(directory) else []

    def test_unchanged_runs_keep_their_record_and_free_their_files(self):
        with temporary_media_root() as media:
            first = self.run_backup(RSC)
            second = self.run_backup(RSC)
            self.assertEqual(second.same_as, first)
            self.assertEqual((second.backup_text, bool(second.backup_binary)), ('', False))
            self.assertEqual(len(self.files_on_disk(media)), 1, 'only the stored backup has a file')
            self.assertIsNotNone(RouterStatus.objects.get(router=self.router).last_backup)

            changed = self.run_backup(RSC.replace('10.0.0.1/24', '10.0.0.2/24'))
            self.assertIsNone(changed.same_as_id)
            self.assertTrue(changed.config_change_detected and changed.backup_binary)
            self.assertEqual(len(self.files_on_disk(media)), 2)

            instant = self.run_backup(RSC.replace('10.0.0.1/24', '10.0.0.2/24'), kind='instant')
            self.assertIsNone(instant.same_as_id, 'Backup now is always stored in full')

    def test_a_profile_keeping_every_backup_stores_them_all(self):
        self.profile.keep_unchanged = True
        self.profile.save()
        with temporary_media_root() as media:
            self.run_backup(RSC)
            second = self.run_backup(RSC)
            self.assertIsNone(second.same_as_id)
            self.assertEqual(len(self.files_on_disk(media)), 2)



class MonitoringContractTests(KeepOnlyChangesPipelineTests):
    """The contract integrations rely on (docs/backups.md, "A contract for
    integrations"): what a row of each kind looks like. Changing it means a
    changelog entry under Monitoring - and a change to this test."""

    def contract(self, backup):
        backup.refresh_from_db()
        return (backup.success, backup.error, backup.finish_time is not None, backup.same_as_id is not None)

    def test_stored_and_unchanged_rows(self):
        with temporary_media_root():
            stored = self.run_backup(RSC)
            unchanged = self.run_backup(RSC)
        self.assertEqual(self.contract(stored), (True, False, True, False))
        self.assertEqual(self.contract(unchanged), (True, False, True, True), 'an unchanged backup is still a row, '
                                                                             'and still a success with its finish time')

    def test_failed_rows_have_no_finish_time(self):
        RouterStatus.objects.filter(router=self.router).update(backup_lock=timezone.now())
        backup = RouterBackup.objects.create(router=self.router, schedule_type='daily', schedule_time=timezone.now())
        with mock.patch('routerlib.device_session.connect_to_ssh', side_effect=OSError('refused')):
            backup_functions.perform_backup(backup)
        self.assertEqual(self.contract(backup), (False, False, False, False))
        backup.retry_count = self.profile.max_retry + 1
        backup.save()
        with mock.patch('routerlib.backup_functions.notify_backup_fail'):
            backup_functions.perform_backup(backup)
        self.assertEqual(self.contract(backup), (False, True, False, False))

    def test_the_latest_attempt_is_the_highest_id(self):
        with temporary_media_root():
            self.run_backup(RSC)
        RouterStatus.objects.filter(router=self.router).update(backup_lock=timezone.now())
        failed = RouterBackup.objects.create(router=self.router, schedule_type='daily', schedule_time=timezone.now())
        with mock.patch('routerlib.device_session.connect_to_ssh', side_effect=OSError('refused')):
            backup_functions.perform_backup(failed)
        latest = RouterBackup.objects.filter(router=self.router).order_by('-id').first()
        self.assertEqual(latest, failed)
        self.assertFalse(latest.success)
        # Ordering by finish_time would hide the failure behind the success
        by_finish = RouterBackup.objects.filter(router=self.router, finish_time__isnull=False).order_by('-finish_time').first()
        self.assertTrue(by_finish.success)


class RefusedExportMigrationTests(TestCase):
    """0016: a backup whose text is a RouterOS refusal becomes a failure;
    real configurations, even short ones, are left alone."""

    def test_refusals_become_failures_and_configurations_stay(self):
        import importlib
        from django.apps import apps
        router = make_router()
        refused = RouterBackup.objects.create(router=router, success=True, schedule_type='daily',
                                              backup_text='expected end of command (line 1 column 10)\n',
                                              backup_text_hash='x' * 64, config_change_detected=True)
        repeat = RouterBackup.objects.create(router=router, success=True, schedule_type='daily', same_as=refused)
        real = RouterBackup.objects.create(router=router, success=True, schedule_type='daily',
                                           backup_text='/ip address\nadd address=10.0.0.1/24 comment="(line 1 column 2)"\n')
        module = importlib.import_module('backup_data.migrations.0016_refused_exports_are_failures')
        module.refused_exports_are_failures(apps, None)
        refused.refresh_from_db()
        self.assertFalse(refused.success)
        self.assertTrue(refused.error)
        self.assertEqual(refused.backup_text, '')
        self.assertIn('expected end of command', refused.error_message)
        repeat.refresh_from_db()
        self.assertFalse(repeat.success)
        self.assertIsNone(repeat.same_as_id)
        real.refresh_from_db()
        self.assertTrue(real.success)
        self.assertIn('add address', real.backup_text)


class RefusedByExitCodeMigrationTests(TestCase):
    """0017: the exit code the console recorded decides - a refused command of
    the first phase makes the backup a failure, whatever text was stored."""

    def console(self, *lines):
        return ''.join(f'[2026-11-23 03:07:41] {line}\n' for line in lines)

    def test_refused_first_phase_fails_clean_and_cleanup_only_stay(self):
        import importlib
        from django.apps import apps
        router = make_router()
        refused = RouterBackup.objects.create(router=router, success=True, schedule_type='daily',
                                              backup_text='something that is not the error line', backup_text_hash='y' * 64,
                                              task_console_output=self.console('$ /export  show-sensitive terse',
                                                                               '[stdout suppressed]', '[exit_code=1]'))
        repeat = RouterBackup.objects.create(router=router, success=True, schedule_type='daily', same_as=refused)
        clean = RouterBackup.objects.create(router=router, success=True, schedule_type='daily', backup_text='/ip address',
                                            task_console_output=self.console('$ /export terse', '[stdout suppressed]',
                                                                             '[exit_code=0]'))
        cleanup_only = RouterBackup.objects.create(router=router, success=True, schedule_type='daily', backup_text='/ip x',
                                                   task_console_output=self.console(
                                                       '$ /export terse', '[exit_code=0]', '=== Retrieving backup files ===',
                                                       '$ file remove [find where name~"routerfleet-backup-"]', '[exit_code=1]'))
        old_format = RouterBackup.objects.create(router=router, success=True, schedule_type='daily', backup_text='/ip y',
                                                 task_console_output='Backup taken\n')
        module = importlib.import_module('backup_data.migrations.0017_refused_by_exit_code')
        module.refused_by_exit_code(apps, None)
        refused.refresh_from_db()
        self.assertFalse(refused.success)
        self.assertTrue(refused.error)
        self.assertEqual(refused.backup_text, '')
        self.assertIn('exit code 1', refused.error_message)
        self.assertIn('/export  show-sensitive terse', refused.error_message)
        repeat.refresh_from_db()
        self.assertFalse(repeat.success)
        for kept in (clean, cleanup_only, old_format):
            kept.refresh_from_db()
            self.assertTrue(kept.success, kept.task_console_output)
