"""Message Center: the less travelled paths of the forms, the email page, the
email sender, the channel helpers and the notification functions."""
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from backup_data.models import RouterBackup
from message_center import channels, email, functions
from message_center.forms import MessageChannelForm, MessageSettingsForm
from message_center.models import Message, MessageChannel, MessageSettings, Notification, SmtpSettings
from routerfleet.testing import make_router, make_user

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
TEST_ONLY_PASSWORD = 'test-only-smtp-password'


def configure_smtp(**extra):
    smtp = SmtpSettings.load()
    values = dict(enabled=True, host='smtp.example.net', port=587, security='starttls', username='rf',
                  password=TEST_ONLY_PASSWORD, sender_address='routerfleet@example.net', verified_at=timezone.now())
    values.update(extra)
    for key, value in values.items():
        setattr(smtp, key, value)
    smtp.save()
    return smtp


def channel(name='ops', channel_type='ntfy', **fields):
    fields.setdefault('destination', 'topic')
    return MessageChannel.objects.create(name=name, channel_type=channel_type, **fields)


class MessageSettingsFormTests(TestCase):
    """The settings form accepts good values, including an empty report time."""

    def data(self, **extra):
        values = {'max_length': 1000, 'max_retry': 3, 'retry_interval': 60, 'status_change_delay': 120,
                  'backup_fails_delay': 600, 'daily_report_time': '07:00'}
        values.update(extra)
        return values

    def test_good_values_are_accepted(self):
        form = MessageSettingsForm(self.data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_missing_report_time_skips_the_time_check(self):
        form = MessageSettingsForm(self.data(daily_report_time=''))
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors['daily_report_time'], ['This field is required.'])


@override_settings(ROUTERFLEET_EMAIL_BACKEND=LOCMEM)
class ChannelFormEdgeTests(TestCase):
    """The channel form: token length, priority, the removed type and email channels."""

    def form(self, **data):
        values = {'name': 'n', 'enabled': True}
        values.update(data)
        return MessageChannelForm(values)

    def test_a_long_token_is_refused(self):
        form = self.form(channel_type='telegram', destination='42', token='1:' + 'a' * 120, enabled=False)
        self.assertFalse(form.is_valid())
        self.assertIn('less than 100', str(form.errors['token']))

    def test_priority_out_of_range(self):
        form = self.form(channel_type='telegram', destination='42', token='1:a', priority=9, enabled=False)
        self.assertFalse(form.is_valid())
        self.assertIn('from 1 (lowest) to 5', str(form.errors['priority']))

    def test_the_removed_type_is_refused_even_when_offered(self):
        form = self.form(channel_type='callmebot', destination='x', token='k')
        form.fields['channel_type'].choices = MessageChannel._meta.get_field('channel_type').choices
        self.assertFalse(form.is_valid())
        self.assertIn(channels.REMOVED, form.errors['__all__'])

    def test_email_needs_an_address(self):
        form = self.form(channel_type='email', destination='not-an-address')
        self.assertFalse(form.is_valid())
        self.assertIn('Enter the email address', str(form.errors['destination']))

    def test_disabled_email_channel_sends_no_test_and_drops_the_token(self):
        form = self.form(channel_type='email', destination='noc@example.net', token='ignored', enabled=False)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['token'], '')
        self.assertEqual(len(mail.outbox), 0)

    def test_email_test_message_is_sent(self):
        configure_smtp()
        form = self.form(channel_type='email', destination='noc@example.net')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['noc@example.net'])

    def test_a_failed_email_test_hides_the_password(self):
        configure_smtp()
        failure = OSError(f'login refused for {TEST_ONLY_PASSWORD}')
        with mock.patch('message_center.email.send', side_effect=failure):
            form = self.form(channel_type='email', destination='noc@example.net')
            self.assertFalse(form.is_valid())
        errors = str(form.errors)
        self.assertIn('Test email failed', errors)
        self.assertNotIn(TEST_ONLY_PASSWORD, errors)


@override_settings(ROUTERFLEET_EMAIL_BACKEND=LOCMEM)
class EmailSettingsPageEdgeTests(TestCase):
    """The SMTP page: the test without an address, a failing test, and the save checks."""

    def setUp(self):
        self.admin = make_user(50)
        self.client.force_login(self.admin)

    def post(self, **data):
        return self.client.post('/system/notifications/email/', data, follow=True)

    def save(self, **data):
        base = {'action': 'save', 'host': 'smtp.example.net', 'port': '587', 'security': 'starttls',
                'username': 'rf', 'password': TEST_ONLY_PASSWORD, 'sender_address': 'routerfleet@example.net',
                'sender_name': 'RouterFleet Improved', 'enabled': 'on'}
        base.update(data)
        return self.client.post('/system/notifications/email/', base)

    def test_test_without_an_address_asks_for_one(self):
        page = self.post(action='test')
        self.assertContains(page, 'Add your email address in My Settings first')
        self.assertEqual(len(mail.outbox), 0)

    def test_a_failing_test_is_reported_without_the_password(self):
        self.admin.email = 'admin@example.net'
        self.admin.save()
        configure_smtp()
        with mock.patch('message_center.email.send', side_effect=OSError(f'no | {TEST_ONLY_PASSWORD}')):
            page = self.post(action='test')
        self.assertContains(page, 'Test email failed')
        self.assertNotContains(page, TEST_ONLY_PASSWORD)

    def test_a_port_that_is_not_a_number(self):
        self.assertContains(self.save(port='abc'), 'between 1 and 65535')

    def test_an_unknown_security(self):
        self.assertContains(self.save(security='rot13'), 'Choose how the connection is secured')

    def test_switched_on_without_a_sender(self):
        self.assertContains(self.save(sender_address=''), 'at least the server')

    def test_saved_switched_off_without_checking_the_server(self):
        with mock.patch('message_center.email.check') as check:
            page = self.save(enabled='', host='', sender_address='')
        self.assertEqual(page.status_code, 302)
        check.assert_not_called()
        smtp = SmtpSettings.load()
        self.assertFalse(smtp.enabled)
        self.assertIsNone(smtp.verified_at)

    def test_a_server_that_refuses_is_not_saved(self):
        with mock.patch('message_center.email.check', side_effect=OSError(f'535 bad login {TEST_ONLY_PASSWORD}')):
            page = self.save()
        self.assertContains(page, 'the server did not accept these settings')
        self.assertNotContains(page, TEST_ONLY_PASSWORD)
        self.assertFalse(SmtpSettings.load().enabled)


@override_settings(ROUTERFLEET_EMAIL_BACKEND=LOCMEM)
class EmailSendTests(TestCase):
    """Sending email: not set up, bad addresses, and the personal channel name."""

    def test_not_set_up(self):
        with self.assertRaisesRegex(ValueError, 'not set up'):
            email.send('noc@example.net', 'subject', 'text')

    def test_bad_recipient(self):
        configure_smtp()
        with self.assertRaisesRegex(ValueError, 'not a valid email address'):
            email.send('nobody', 'subject', 'text')

    def test_scrub_without_settings(self):
        self.assertEqual(email.scrub('plain text', None), 'plain text')

    def test_personal_channel_avoids_a_taken_name(self):
        user = User.objects.create_user(username='alice')
        channel(name='Email: alice', channel_type='email', destination='team@example.net')
        personal = email.save_personal_channel(user, 'alice@example.net', ['backup_fail'])
        self.assertEqual(personal.name, 'Email: alice (personal)')
        self.assertTrue(personal.enabled)
        self.assertTrue(personal.backup_fail)
        self.assertFalse(personal.backup_success)


class ChannelHelperTests(TestCase):
    """The channel helpers refuse a malformed ntfy server and an unknown type."""

    def test_malformed_ntfy_server(self):
        self.assertIn('Enter the server as', channels.ntfy_server_errors('ftp://ntfy.example.net', ''))

    def test_unknown_channel_type(self):
        with self.assertRaisesRegex(ValueError, 'Invalid channel type'):
            channels.send('pigeon', '', 'dest', 'text')


class NotificationFunctionTests(TestCase):
    """Grouping and direct messages for the cases the main tests leave out."""

    def test_online_changes_are_grouped(self):
        channel()
        router = make_router(name='edge-1')
        Notification.objects.create(notification_type='status_online', router=router)
        self.assertEqual(functions.concatenate_notifications('status_online'), 1)
        message = Message.objects.get()
        self.assertEqual(message.subject, 'Device status change: Online')
        self.assertIn('edge-1', message.message)

    def test_a_notification_without_a_device_still_counts(self):
        channel()
        Notification.objects.create(notification_type='status_online')
        self.assertEqual(functions.concatenate_notifications('status_online'), 1)
        self.assertEqual(Message.objects.get().message, 'Device status change: Online\n')

    def test_backup_report_is_cut_short(self):
        MessageSettings.objects.create(max_length=500)
        channel()
        router = make_router(name='r' * 60)
        for _ in range(10):
            RouterBackup.objects.create(router=router, schedule_type='daily', error=True)
        functions.generate_backup_report({'report_time_exception': False})
        self.assertIn('There are more failed backups', Message.objects.get().message)

    def test_status_report_is_cut_short(self):
        MessageSettings.objects.create(max_length=500)
        channel()
        for number in range(10):
            make_router(name=f'offline-device-{number:02d}-' + 'x' * 40, status_online=False, monitoring=True)
        functions.generate_status_report({'report_time_exception': False})
        self.assertIn('There are more offline devices', Message.objects.get().message)

    def test_grouped_online_change_without_a_channel_records_nothing(self):
        channel(status_change_online=False)
        functions.notify_router_status_update(make_router(status_online=True))
        self.assertFalse(Notification.objects.exists())

    def test_direct_offline_message(self):
        MessageSettings.objects.create(concatenate_status_change=False)
        channel()
        functions.notify_router_status_update(make_router(name='edge-2', status_online=False))
        message = Message.objects.get()
        self.assertEqual(message.subject, 'Device status change: Offline')
        self.assertIn('edge-2', message.message)

    def test_direct_backup_failure_without_an_error_text(self):
        MessageSettings.objects.create(concatenate_backup_fails=False)
        channel()
        backup = RouterBackup.objects.create(router=make_router(), schedule_type='daily')
        functions.notify_backup_fail(backup)
        self.assertNotIn('Error message', Message.objects.get().message)

    def test_lock_expiry_without_a_channel(self):
        backup = RouterBackup.objects.create(router=make_router(), schedule_type='daily')
        functions.notify_backup_task_lock_expired(backup)
        self.assertFalse(Message.objects.exists())

    def test_lock_expiry_carries_the_error_text(self):
        channel()
        backup = RouterBackup.objects.create(router=make_router(), schedule_type='daily', error_message='timed out')
        functions.notify_backup_task_lock_expired(backup)
        self.assertIn('Error message: timed out', Message.objects.get().message)

    def test_direct_backup_success_states(self):
        MessageSettings.objects.create(concatenate_backup_fails=False)
        channel(backup_success=True)
        router = make_router(name='edge-3')
        stored = RouterBackup.objects.create(router=router, schedule_type='daily', success=True)
        functions.notify_backup_success(stored)
        unchanged = RouterBackup.objects.create(router=router, schedule_type='daily', success=True)
        RouterBackup.objects.filter(pk=unchanged.pk).update(same_as=stored)
        unchanged.refresh_from_db()
        functions.notify_backup_success(unchanged)
        changed = RouterBackup.objects.create(router=router, schedule_type='daily', success=True)
        RouterBackup.objects.filter(pk=changed.pk).update(config_change_detected=True)
        changed.refresh_from_db()
        functions.notify_backup_success(changed)
        texts = list(Message.objects.order_by('id').values_list('message', flat=True))
        self.assertTrue(texts[0].endswith('succeeded: stored.'), texts[0])
        self.assertTrue(texts[1].endswith('succeeded: unchanged.'), texts[1])
        self.assertTrue(texts[2].endswith('succeeded: configuration changed.'), texts[2])

    def test_grouped_online_change_is_recorded(self):
        channel()
        router = make_router(status_online=True)
        functions.notify_router_status_update(router)
        self.assertEqual(Notification.objects.get().notification_type, 'status_online')

    def test_grouped_backup_failure_is_recorded(self):
        channel()
        backup = RouterBackup.objects.create(router=make_router(), schedule_type='daily')
        functions.notify_backup_fail(backup)
        self.assertEqual(Notification.objects.get().router_backup, backup)

    def test_backup_success_without_a_channel(self):
        channel(backup_success=False)
        backup = RouterBackup.objects.create(router=make_router(), schedule_type='daily', success=True)
        functions.notify_backup_success(backup)
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Message.objects.exists())


class FirmwareNotificationTests(TestCase):
    """A finished firmware update reaches the channels that asked, done or failed."""

    def target(self, state, from_version=''):
        router = make_router(name='edge-fw', address='192.0.2.20')
        return SimpleNamespace(router=router, state=state, message='7.16.2', from_version=from_version,
                               rollout=SimpleNamespace(label='Rollout 1'))

    def test_nobody_asked(self):
        channel()
        functions.notify_firmware_update(self.target('done'))
        self.assertFalse(Message.objects.exists())

    def test_done_names_the_version_before(self):
        channel(firmware_update=True)
        functions.notify_firmware_update(self.target('done', from_version='7.15'))
        message = Message.objects.get()
        self.assertEqual(message.subject, 'Firmware updated: edge-fw')
        self.assertIn('Before: 7.15.', message.message)
        self.assertIn('Rollout: Rollout 1.', message.message)

    def test_done_without_a_version_before(self):
        channel(firmware_update=True)
        functions.notify_firmware_update(self.target('done'))
        self.assertNotIn('Before:', Message.objects.get().message)

    def test_failed(self):
        channel(firmware_update=True)
        functions.notify_firmware_update(self.target('failed'))
        message = Message.objects.get()
        self.assertEqual(message.subject, 'Firmware update failed: edge-fw')
        self.assertIn('failed: 7.16.2', message.message)
