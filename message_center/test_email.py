"""Email notifications: the mail server, each user's choices, the new events."""
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from backup_data.models import RouterBackup
from fleet_commander.command_functions import create_manual_job
from fleet_commander.models import Command, CommandTask, CommandVariant
from message_center import email
from message_center.functions import concatenate_notifications, notify_backup_success, send_notification_message
from message_center.models import Message, MessageChannel, Notification, SmtpSettings
from router_manager.models import Router
from routerfleet.testing import make_router, make_user

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def configure(**extra):
    smtp = SmtpSettings.load()
    for key, value in dict(enabled=True, host='smtp.example.net', port=587, security='starttls', username='rf',
                           password='smtp-secret-1', sender_address='routerfleet@example.net', **extra).items():
        setattr(smtp, key, value)
    smtp.save()
    return smtp


@override_settings(ROUTERFLEET_EMAIL_BACKEND=LOCMEM)
class SmtpSettingsPageTests(TestCase):
    def setUp(self):
        self.admin = make_user(50)
        self.admin.email = 'admin@example.net'
        self.admin.save()
        self.client.force_login(self.admin)

    def save(self, **data):
        base = {'action': 'save', 'host': 'smtp.example.net', 'port': '587', 'security': 'starttls',
                'username': 'rf', 'password': 'smtp-secret-1', 'sender_address': 'routerfleet@example.net',
                'sender_name': 'RouterFleet Improved', 'enabled': 'on'}
        base.update(data)
        return self.client.post('/message_center/email/', base)

    def test_only_administrators(self):
        self.client.force_login(make_user(40))
        self.assertContains(self.client.get('/message_center/email/'), 'Access Denied')

    def test_saved_encrypted_and_never_shown(self):
        self.save()
        smtp = SmtpSettings.load()
        self.assertTrue(smtp.ready)
        self.assertEqual(smtp.password, 'smtp-secret-1')
        page = self.client.get('/message_center/email/')
        self.assertNotContains(page, 'smtp-secret-1')
        self.assertContains(page, 'stored - leave empty to keep it')
        self.save(password='')                                   # empty keeps it
        self.assertEqual(SmtpSettings.load().password, 'smtp-secret-1')
        self.save(password='', remove_password='on')
        self.assertEqual(SmtpSettings.load().password, '')

    def test_validation(self):
        self.assertContains(self.save(port='0'), 'between 1 and 65535')
        self.assertContains(self.save(sender_address='not-an-address'), 'not a valid email')
        self.assertContains(self.save(host='bad host;'), 'host name or an IP address')
        self.assertContains(self.save(host=''), 'at least the server')

    def test_test_email(self):
        self.save()
        self.client.post('/message_center/email/', {'action': 'test'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['admin@example.net'])
        self.assertIn('RouterFleet Improved <routerfleet@example.net>', mail.outbox[0].from_email)


@override_settings(ROUTERFLEET_EMAIL_BACKEND=LOCMEM)
class MySettingsEmailTests(TestCase):
    def setUp(self):
        self.user = make_user(10)
        self.client.force_login(self.user)

    def test_address_and_choices_make_the_personal_channel(self):
        self.client.post('/user/settings/', {'action': 'email', 'email': 'me@example.net',
                                             'backup_fail': 'on', 'daily_backup_report': 'on'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'me@example.net')
        channel = MessageChannel.objects.get(user=self.user)
        self.assertEqual((channel.channel_type, channel.enabled), ('email', True))
        self.assertEqual((channel.backup_fail, channel.daily_backup_report, channel.backup_success,
                          channel.status_change_offline), (True, True, False, False))
        page = self.client.get('/user/settings/')
        self.assertContains(page, 'id="email-backup_fail" name="backup_fail" checked')
        self.assertContains(page, 'Email is not set up yet')
        self.assertNotContains(self.client.get('/message_center/channel_list/'), channel.name)

    def test_nothing_chosen_or_no_address_means_off(self):
        self.client.post('/user/settings/', {'action': 'email', 'email': 'me@example.net'})
        self.assertFalse(MessageChannel.objects.get(user=self.user).enabled)
        self.client.post('/user/settings/', {'action': 'email', 'email': '', 'backup_fail': 'on'})
        self.assertFalse(MessageChannel.objects.get(user=self.user).enabled)
        self.client.post('/user/settings/', {'action': 'email', 'email': 'bad', 'backup_fail': 'on'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')

    def test_the_queue_delivers_to_the_users_current_address(self):
        configure()
        self.client.post('/user/settings/', {'action': 'email', 'email': 'me@example.net', 'backup_fail': 'on'})
        channel = MessageChannel.objects.get(user=self.user)
        message = Message.objects.create(channel=channel, subject='Backup failed report\nBcc: evil@example.net',
                                         message='Backup failed report\n\n- Backup 1 for router edge')
        send_notification_message(message)
        message.refresh_from_db()
        self.assertEqual(message.status, 'sent')
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['me@example.net'])
        self.assertNotIn('\n', sent.subject, 'a line break can not add headers')
        self.assertEqual(sent.bcc, [])

    def test_a_server_error_never_stores_the_password(self):
        configure()
        self.client.post('/user/settings/', {'action': 'email', 'email': 'me@example.net', 'backup_fail': 'on'})
        message = Message.objects.create(channel=MessageChannel.objects.get(user=self.user), subject='s', message='m')
        with mock.patch('message_center.email.EmailMessage.send', side_effect=OSError('auth rf:smtp-secret-1 rejected')):
            send_notification_message(message)
        message.refresh_from_db()
        self.assertNotIn('smtp-secret-1', message.error_message)
        self.assertIn('***', message.error_message)


@override_settings(ROUTERFLEET_EMAIL_BACKEND=LOCMEM)
class NewEventTests(TestCase):
    def setUp(self):
        self.channel = MessageChannel.objects.create(name='noc', channel_type='ntfy', destination='noc-topic',
                                                     backup_success=True, command_failed=True)

    def test_existing_channels_do_not_get_the_new_events(self):
        old = MessageChannel.objects.create(name='old', channel_type='ntfy', destination='t')
        self.assertFalse(old.backup_success or old.command_failed)

    def test_successful_backups_are_grouped(self):
        router = make_router(name='edge-1')
        for _ in range(3):
            backup = RouterBackup.objects.create(router=router, schedule_type='daily', success=True,
                                                 finish_time=timezone.now())
            notify_backup_success(backup)
        self.assertEqual(Notification.objects.filter(notification_type='backup_success').count(), 3)
        concatenate_notifications('backup_success')
        message = Message.objects.get(channel=self.channel)
        self.assertEqual(message.subject, 'Successful backups')
        self.assertEqual(message.message.count('edge-1'), 3)

    def test_a_job_with_failures_notifies_once(self):
        command = Command.objects.create(name='Update')
        CommandVariant.objects.create(command=command, router_type='routeros', payload='/x')
        job = create_manual_job(command, routers=Router.objects.filter(pk__in=[make_router().pk, make_router().pk]))
        tasks = list(job.tasks.all())
        CommandTask.objects.filter(pk=tasks[0].pk).update(status='success', finished_at=timezone.now())
        CommandTask.objects.filter(pk=tasks[1].pk).update(status='error', finished_at=timezone.now(),
                                                          error_message='timed out')
        job.update_counters()
        job.update_counters()
        message = Message.objects.get(channel=self.channel)
        self.assertIn('1 failed', message.message)
        self.assertIn('timed out', message.message)


@override_settings(ROUTERFLEET_EMAIL_BACKEND=LOCMEM)
class SharedMailboxTests(TestCase):
    def test_an_email_channel_for_a_team_address(self):
        configure()
        self.client.force_login(make_user(40))
        self.client.post('/message_center/manage_channel/', {
            'name': 'NOC mailbox', 'channel_type': 'email', 'destination': 'noc@example.net', 'enabled': 'on',
            'backup_fail': 'on'})
        channel = MessageChannel.objects.get(name='NOC mailbox')
        self.assertEqual(channel.email_address, 'noc@example.net')
        self.assertEqual(mail.outbox[-1].to, ['noc@example.net'], 'a test message is sent before saving')

    def test_one_line(self):
        self.assertEqual(email.one_line('a\r\nBcc: x', 50), 'a Bcc: x')
