"""What goes into a device's update command is a plain version or name: a
vendor channel answer with anything else is dropped, and a rollout carrying
one never starts."""
from unittest import mock

from django.test import TestCase

from firmware import options, rollout


class PlainVersionTests(TestCase):
    def fetch(self, text, pattern=r'^(.+)$'):
        options._cache.clear()
        response = mock.Mock(text=text, raise_for_status=mock.Mock())
        device_type = mock.Mock(update_channel_pattern='')
        with mock.patch('routerlib.outbound.https_get', return_value=response):
            return options.fetch_channel_version(device_type, 'https://vendor.example/stable', pattern)

    def test_a_plain_version_is_taken(self):
        self.assertEqual(self.fetch('7.19.2'), '7.19.2')
        self.assertEqual(self.fetch('25.12.0-rc1'), '25.12.0-rc1')
        self.assertEqual(self.fetch('7.99\nreboot'), '7.99', 'one line is read')

    def test_anything_more_is_dropped(self):
        for hostile in ('7.99;/system reset-configuration', '7.99 && reboot', '7.99`id`', '$(reboot)'):
            with self.subTest(hostile=hostile):
                self.assertEqual(self.fetch(hostile), '')

    def test_a_rollout_with_an_unsafe_version_does_not_start(self):
        target = mock.Mock()
        with mock.patch('firmware.rollout.firmware_values',
                        return_value={'channel': 'stable', 'firmware_version': '7.99;reboot'}):
            self.assertIn('7.99;reboot', rollout.unsafe_values(target))
        with mock.patch('firmware.rollout.firmware_values',
                        return_value={'channel': 'stable', 'firmware_version': '7.19.2', 'firmware_path': '/flash/a b.npk'}):
            self.assertEqual(rollout.unsafe_values(target), '')


class AbortTests(TestCase):
    """An abort stops the devices that had not started installing, and follows
    the ones that had to their end - none is left halfway."""

    def test_abort_then_the_scheduler_finishes_the_installing_ones(self):
        from routerfleet.testing import make_router
        from firmware.models import FirmwareRollout, RolloutTarget
        rollout_row = FirmwareRollout.objects.create(source='online', channel='stable')
        uploading = RolloutTarget.objects.create(rollout=rollout_row, router=make_router(), position=0, state='uploading')
        rebooting = RolloutTarget.objects.create(rollout=rollout_row, router=make_router(), position=1, state='rebooting')
        waiting = RolloutTarget.objects.create(rollout=rollout_row, router=make_router(), position=2, state='waiting')
        rollout.abort(rollout_row)
        for target in (uploading, rebooting, waiting):
            target.refresh_from_db()
        self.assertEqual((uploading.state, waiting.state), ('skipped', 'skipped'))
        self.assertEqual(rebooting.state, 'rebooting', 'already installing: followed, not dropped')
        with mock.patch('firmware.rollout.step') as step:
            rollout.advance_all()
        self.assertEqual([call.args[0].pk for call in step.call_args_list], [rebooting.pk])
