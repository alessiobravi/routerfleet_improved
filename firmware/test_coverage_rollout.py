"""The rollout engine's rarer turns: refusals before anything starts, a slow
upload, steps that end oddly, the scheduler's guards. Devices are fakes."""
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from backup_data.models import RouterBackup
from firmware import rollout as engine
from firmware.models import FirmwareImage, FirmwareRollout, RolloutEvent, RolloutTarget
from firmware.tests import make_image, routeros_device
from fleet_commander.models import CommandVariant
from routerfleet.testing import FakeSSHClient, edit_type, make_router, temporary_media_root


class RolloutCase(TestCase):
    def setUp(self):
        self.media = temporary_media_root()
        self.media.__enter__()
        self.addCleanup(self.media.__exit__, None, None, None)
        self.router = routeros_device('rb-cov', '7.24.5 (stable)')

    def online_target(self, state='waiting', to_version='7.25.2', router=None, **fields):
        rollout = FirmwareRollout.objects.create(source='online', channel=fields.pop('channel', 'stable'))
        return RolloutTarget.objects.create(rollout=rollout, router=router or self.router, state=state,
                                            to_version=to_version, **fields)

    def file_target(self, state, **fields):
        image = make_image(version='7.25.2')
        rollout = FirmwareRollout.objects.create(image=image)
        return RolloutTarget.objects.create(rollout=rollout, router=self.router, state=state, **fields)

    def step(self, target, client=None):
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client or FakeSSHClient({})):
            engine.step(target)
        target.refresh_from_db()
        return target


class RefusalTests(RolloutCase):
    """What check_target and the first steps refuse before a device is touched."""

    def test_a_device_not_reached_over_ssh(self):
        monitored = make_router(router_type='monitoring', name='mon-cov')
        target = self.online_target(router=monitored)
        self.assertEqual(engine.check_target(target), 'the device is not reached over SSH')

    def test_a_value_that_is_not_plain_is_never_put_in_a_command(self):
        target = self.online_target(to_version='7.25.2;reboot')
        self.assertIn('not a plain version or name', engine.check_target(target))
        self.assertIn('7.25.2;reboot', engine.check_target(target))

    def test_a_type_without_an_online_update(self):
        CommandVariant.objects.filter(command__system_key='firmware_online_update', router_type='routeros') \
            .update(payload='')
        self.assertIn('has no online update procedure', engine.check_target(self.online_target()))

    def test_no_backup_possible_stops_the_rollout(self):
        self.router.backup_profile = None
        self.router.save()
        target = self.step(self.online_target())
        self.assertEqual(target.state, 'failed')
        self.assertIn('no pre-update backup: Device has no backup profile', target.message)

    def test_a_backup_that_disappeared(self):
        target = self.step(self.online_target(state='backing_up'))
        self.assertEqual(target.state, 'failed')
        self.assertIn('pre-update backup disappeared', target.message)

    def test_a_backup_still_running_within_its_time_is_waited_for(self):
        backup = RouterBackup.objects.create(router=self.router, schedule_type='instant')
        target = self.step(self.online_target(state='backing_up', backup=backup,
                                              deadline=timezone.now() + timezone.timedelta(minutes=5)))
        self.assertEqual(target.state, 'backing_up')


class StepTests(RolloutCase):
    """Uploading, verifying, installing and the waits after a reboot."""

    def test_a_slow_upload_keeps_its_lock_fresh(self):
        target = self.file_target('uploading', deadline=timezone.now() + timezone.timedelta(minutes=30))
        seen = {}

        class SlowSCP:
            def __init__(self, transport, socket_timeout=None, progress=None):
                self.progress = progress

            def put(self, local, remote):
                later = timezone.now() + timezone.timedelta(minutes=2)
                with mock.patch('django.utils.timezone.now', return_value=later):
                    self.progress(b'fw', 100, 50)
                    self.progress(b'fw', 100, 60)    # within the minute: not written again
                seen['lock'] = RolloutTarget.objects.get(pk=target.pk).lock
                seen['later'] = later
                seen['remote'] = remote
        with mock.patch('scp.SCPClient', SlowSCP):
            target = self.step(target)
        self.assertEqual(seen['lock'], seen['later'])
        self.assertEqual(seen['remote'], f'/{target.rollout.image.filename}')
        self.assertEqual(target.state, 'verifying')
        self.assertIsNone(target.lock, 'released after the step')

    def test_a_type_that_does_not_verify_goes_on_to_install(self):
        edit_type('routeros', firmware_verify='none')
        target = self.step(self.file_target('verifying'))
        self.assertEqual(target.state, 'installing')
        self.assertIn('not verified', target.message)

    def test_a_session_that_will_not_close_after_installing(self):
        client = FakeSSHClient({})
        client.close = mock.Mock(side_effect=OSError('gone'))
        target = self.step(self.online_target(state='installing'), client)
        self.assertEqual(target.state, 'rebooting')

    def test_no_reading_right_after_the_install_started(self):
        target = self.online_target(state='rebooting', message='Installing 7.25.2; the device reboots')
        with mock.patch('routerlib.router_functions.get_router_information') as read:
            target = self.step(target)
        read.assert_not_called()
        self.assertEqual(target.state, 'rebooting')

    def test_a_session_that_will_not_close_after_the_bootloader_upgrade(self):
        rollout = FirmwareRollout.objects.create(source='bootloader')
        target = RolloutTarget.objects.create(rollout=rollout, router=self.router, state='bootloader')
        client = FakeSSHClient({})
        client.close = mock.Mock(side_effect=OSError('gone'))
        with mock.patch('firmware.rollout._read_firmware', return_value=(True, '', '7.24.5', '7.25.2')):
            target = self.step(target, client)
        self.assertEqual((target.state, target.to_firmware), ('boot_waiting', '7.25.2'))

    def test_no_reading_right_after_the_bootloader_upgrade_started(self):
        rollout = FirmwareRollout.objects.create(source='bootloader')
        target = RolloutTarget.objects.create(rollout=rollout, router=self.router, state='boot_waiting',
                                              to_firmware='7.25.2', message='Upgrading the bootloader firmware')
        with mock.patch('firmware.rollout._read_firmware') as read:
            target = self.step(target)
        read.assert_not_called()
        self.assertEqual(target.state, 'boot_waiting')


class MoveAndStepGuardTests(RolloutCase):
    """_move after an abort, a state without a step, a step that raises."""

    def test_the_bootloader_is_not_started_after_an_abort(self):
        target = self.online_target(state='rebooting')
        FirmwareRollout.objects.filter(pk=target.rollout_id).update(state='aborted')
        engine._move(target, 'bootloader', 'Back online. Upgrading the bootloader firmware next.')
        target.refresh_from_db()
        self.assertEqual(target.state, 'done')
        self.assertIn('bootloader firmware was not started', target.message)

    def test_a_move_without_a_message_records_no_event(self):
        target = self.online_target(state='rebooting')
        engine._move(target, 'rebooting')
        self.assertFalse(RolloutEvent.objects.filter(target=target).exists())

    def test_a_state_without_a_step_only_takes_and_releases_the_lock(self):
        target = self.step(self.online_target(state='done'))
        self.assertEqual(target.state, 'done')
        self.assertIsNone(target.lock)

    def test_an_unexpected_error_fails_the_device_with_its_step_named(self):
        target = self.online_target()
        with mock.patch.dict(engine.STEPS, {'waiting': mock.Mock(side_effect=RuntimeError('boom'))}), \
                self.assertLogs('firmware.rollout', level='WARNING'):
            target = self.step(target)
        self.assertEqual(target.state, 'failed')
        self.assertEqual(target.message, 'waiting: boom')
        target.rollout.refresh_from_db()
        self.assertEqual(target.rollout.state, 'failed')

    def test_a_failing_notification_never_breaks_the_rollout(self):
        target = self.online_target(state='done')
        with mock.patch('message_center.functions.notify_firmware_update', side_effect=RuntimeError('smtp down')), \
                self.assertLogs('firmware.rollout', level='WARNING') as logs:
            engine._notify(target)
        self.assertIn('notification for rb-cov failed', ' '.join(logs.output))


class SchedulerTests(RolloutCase):
    """advance, advance_all, abort and removing an image's file."""

    def test_a_rollout_without_devices_stays_as_it_is(self):
        rollout = FirmwareRollout.objects.create(source='online', channel='stable')
        engine.advance(rollout)
        rollout.refresh_from_db()
        self.assertEqual(rollout.state, 'running')

    def test_one_rollout_failing_does_not_stop_the_others(self):
        FirmwareRollout.objects.create(source='online', channel='stable')
        aborted = self.online_target(state='installing').rollout
        FirmwareRollout.objects.filter(pk=aborted.pk).update(state='aborted')
        with mock.patch.object(engine, 'advance', side_effect=RuntimeError('db')), \
                mock.patch.object(engine, 'finish_aborted', side_effect=RuntimeError('db')) as finish, \
                self.assertLogs('firmware.rollout', level='WARNING') as logs:
            engine.advance_all()
        finish.assert_called_once()
        text = ' '.join(logs.output)
        self.assertIn('could not advance', text)
        self.assertIn('could not finish its devices', text)

    def test_aborting_twice_changes_nothing(self):
        target = self.online_target()
        rollout = target.rollout
        engine.abort(rollout)
        rollout.refresh_from_db()
        finished = rollout.finished_at
        engine.abort(rollout)
        rollout.refresh_from_db()
        self.assertEqual((rollout.state, rollout.finished_at), ('aborted', finished))

    def test_aborted_by_a_named_user(self):
        rollout = self.online_target().rollout
        engine.abort(rollout, user=mock.Mock(username='ops'))
        rollout.refresh_from_db()
        self.assertTrue(rollout.message.startswith('Aborted by ops.'))

    def test_removing_files(self):
        import os
        image = make_image(version='7.25.3')
        path = image.file.path
        engine.remove_file(image)
        self.assertFalse(os.path.exists(path))
        engine.remove_file(image)                                       # already gone: nothing to do
        engine.remove_file(FirmwareImage(version='1'))                  # no file at all
        image = make_image(version='7.25.4')
        with mock.patch('os.remove', side_effect=OSError('busy')), \
                self.assertLogs('firmware.rollout', level='WARNING'):
            engine.remove_file(image)
