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
from routerfleet.testing import (FakeSCPClient, FakeSSHClient, make_profile, make_router,
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
        with mock.patch(f'{SSH}.connect_to_ssh', return_value=client), \
                mock.patch(f'{SSH}.SCPClient', FakeSCPClient):
            backup_functions.perform_backup(backup)
        backup.refresh_from_db()
        return backup

    def test_routeros_two_phases(self):
        backup = self.make_backup('routeros')
        name = backup_functions.gen_backup_name(backup)
        client = FakeSSHClient()
        with temporary_media_root() as media:
            backup = self.run_phase(backup, client)
            self.assertTrue(backup.backup_pending_retrieval)
            self.assertFalse(backup.success)
            self.assertIn(f'/system backup save name={name}.backup', client.commands)
            self.assertIn(f'/export file={name}.rsc  show-sensitive terse', client.commands)

            FakeSCPClient.files = {f'/{name}.rsc': RSC, f'/{name}.backup': b'\x00binary'}
            backup = self.run_phase(backup, client)
            self.assertTrue(backup.success)
            self.assertFalse(backup.backup_pending_retrieval)
            # Comment lines are stripped, so a header timestamp is not a change
            self.assertNotIn('header comment', backup.backup_text)
            self.assertIn('add address=10.0.0.1/24', backup.backup_text)
            self.assertTrue(os.path.exists(backup.backup_binary.path))
            self.assertTrue(backup.backup_binary.path.startswith(media))
            self.assertIn(f'/file remove "{name}.rsc"', client.commands)
            self.assertIn('file remove [find where name~"routerfleet-backup-"]', client.commands)
        status = RouterStatus.objects.get(router=backup.router)
        self.assertIsNone(status.backup_lock)
        self.assertIsNotNone(status.last_backup)
        self.assertIn('=== Retrieving backup files ===', backup.task_console_output)

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
        self.assertIn('Empty backup text', backup.error_message)

    def test_airos_fetches_system_cfg_with_latin1_fallback(self):
        backup = self.make_backup('ubiquiti-airos')
        client = FakeSSHClient()
        backup = self.run_phase(backup, client)
        self.assertEqual(client.commands, [], 'phase one does nothing on airOS')
        FakeSCPClient.files = {'/tmp/system.cfg': 'wireless.1.ssid=caf\xe9'.encode('latin-1')}
        backup = self.run_phase(backup, client)
        self.assertTrue(backup.success)
        self.assertIn('caf\xe9', backup.backup_text)
        self.assertIn('[decode=latin-1 fallback]', backup.task_console_output)

    def test_failure_retries_after_retry_interval_minutes(self):
        backup = self.make_backup('routeros')
        now = timezone.now()
        with at(now), mock.patch(f'{SSH}.connect_to_ssh', side_effect=OSError('refused')):
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
        with mock.patch(f'{SSH}.connect_to_ssh') as connect:
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
        with mock.patch(f'{SSH}.connect_to_ssh', return_value=FakeSSHClient()), \
                mock.patch(f'{SSH}.SCPClient', FakeSCPClient), temporary_media_root():
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
