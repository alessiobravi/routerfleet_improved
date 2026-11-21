import datetime
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from backup_data.models import RouterBackup
from message_center import functions
from message_center.forms import MessageChannelForm, MessageSettingsForm
from message_center.models import Message, MessageChannel, MessageSettings, Notification
from routerfleet.testing import make_router, make_user
from routerfleet_tools.models import WebadminSettings


def at(moment):
    return mock.patch('django.utils.timezone.now', return_value=moment)


def channel(name='ops', channel_type='ntfy', **fields):
    fields.setdefault('destination', 'topic')
    return MessageChannel.objects.create(name=name, channel_type=channel_type, **fields)


class NotifyTests(TestCase):
    def test_status_change_is_concatenated_by_default(self):
        channel()
        router = make_router(status_online=False)
        functions.notify_router_status_update(router)
        self.assertEqual(Notification.objects.get().notification_type, 'status_offline')
        self.assertFalse(Message.objects.exists())

    def test_no_notification_when_no_channel_wants_it(self):
        channel(status_change_offline=False)
        functions.notify_router_status_update(make_router(status_online=False))
        self.assertFalse(Notification.objects.exists())

    def test_without_concatenation_messages_go_out_directly(self):
        MessageSettings.objects.create(concatenate_status_change=False, concatenate_backup_fails=False)
        channel()
        router = make_router(name='edge-9', status_online=True)
        functions.notify_router_status_update(router)
        message = Message.objects.get()
        self.assertEqual(message.subject, 'Router status change: Online')
        self.assertIn('edge-9', message.message)

        backup = RouterBackup.objects.create(router=router, schedule_type='daily', error_message='boom')
        functions.notify_backup_fail(backup)
        self.assertIn('Error message: boom', Message.objects.get(subject__startswith='Backup failed').message)

    def test_backup_fail_needs_a_channel(self):
        backup = RouterBackup.objects.create(router=make_router(), schedule_type='daily')
        functions.notify_backup_fail(backup)
        self.assertFalse(Notification.objects.exists())

    def test_lock_expiry_goes_to_every_enabled_channel(self):
        channel('a', backup_fail=False, status_change_offline=False)
        channel('b')
        channel('off', enabled=False)
        backup = RouterBackup.objects.create(router=make_router(), schedule_type='daily')
        functions.notify_backup_task_lock_expired(backup)
        self.assertEqual(sorted(Message.objects.values_list('channel__name', flat=True)), ['a', 'b'])


class ConcatenationTests(TestCase):
    def setUp(self):
        self.channel = channel()
        self.now = timezone.now()

    def concatenate(self):
        return self.client.get('/cron/concatenate_notifications/').json()

    def test_waits_for_the_delay(self):
        Notification.objects.create(notification_type='status_offline', router=make_router(status_online=False))
        self.concatenate()
        self.assertFalse(Message.objects.exists())

    def test_folds_into_one_message(self):
        a = make_router(name='a', status_online=False)
        b = make_router(name='b', status_online=False)
        with at(self.now - datetime.timedelta(minutes=5)):
            Notification.objects.create(notification_type='status_offline', router=a)
            Notification.objects.create(notification_type='status_offline', router=b)
        data = self.concatenate()
        self.assertEqual(data['notification_type']['status_offline'], 2)
        message = Message.objects.get()
        self.assertEqual(message.subject, 'Router status change: Offline')
        self.assertIn('Router a', message.message)
        self.assertIn('Router b', message.message)
        self.assertFalse(Notification.objects.exists())

    def test_a_flap_inside_the_window_sends_nothing(self):
        router = make_router(status_online=True)
        Notification.objects.create(notification_type='status_offline', router=router)
        Notification.objects.create(notification_type='status_online', router=router)
        self.concatenate()
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Message.objects.exists())

    def test_overflow_waits_for_the_next_run(self):
        settings = MessageSettings.objects.create(max_length=120)
        routers = [make_router(name=f'router-with-a-long-name-{i}', status_online=False) for i in range(5)]
        with at(self.now - datetime.timedelta(minutes=5)):
            for router in routers:
                Notification.objects.create(notification_type='status_offline', router=router)
        self.concatenate()
        self.assertLessEqual(len(Message.objects.get().message), settings.max_length)
        self.assertTrue(Notification.objects.exists(), 'what did not fit stays for the next run')

    def test_backup_failures_wait_their_own_delay(self):
        backup = RouterBackup.objects.create(router=make_router(), schedule_type='daily')
        with at(self.now - datetime.timedelta(minutes=5)):
            Notification.objects.create(notification_type='backup_fail', router=backup.router,
                                        router_backup=backup)
        self.concatenate()
        self.assertFalse(Message.objects.exists())
        Notification.objects.update(created=self.now - datetime.timedelta(minutes=11))
        self.concatenate()
        self.assertEqual(Message.objects.get().subject, 'Backup failed report')

    def test_unknown_type(self):
        self.assertEqual(functions.concatenate_notifications('nonsense'), 0)


class SendTests(TestCase):
    def response(self, status=200, text='ok'):
        return mock.Mock(status_code=status, text=text)

    def send(self, message, **patches):
        functions.send_notification_message(message)
        message.refresh_from_db()
        return message

    def test_ntfy_posts_with_title(self):
        message = Message.objects.create(channel=channel(destination='my-topic'), subject='S', message='body')
        with mock.patch('message_center.functions.requests.post', return_value=self.response()) as post:
            message = self.send(message)
        post.assert_called_once_with('https://ntfy.sh/my-topic', data=b'body', headers={'Title': 'S'}, timeout=15)
        self.assertEqual(message.status, 'sent')
        self.assertIsNotNone(message.completed)

    def test_telegram_and_callmebot_use_get(self):
        tg = Message.objects.create(channel=channel('tg', 'telegram', destination='42', token='T'), message='hi')
        cb = Message.objects.create(channel=channel('cb', 'callmebot', destination='+39', token='K'), message='yo')
        with mock.patch('message_center.functions.requests.get', return_value=self.response()) as get:
            self.send(tg)
            self.send(cb)
        urls = [c.args[0] for c in get.call_args_list]
        self.assertEqual(urls[0], 'https://api.telegram.org/botT/sendMessage?chat_id=42&text=hi')
        self.assertEqual(urls[1], 'https://api.callmebot.com/whatsapp.php?phone=+39&text=yo&apikey=K')

    def test_failure_is_stored_and_retried(self):
        MessageSettings.objects.create(retry_interval=60)
        message = Message.objects.create(channel=channel(), message='m')
        now = timezone.now()
        with at(now), mock.patch('message_center.functions.requests.post',
                                 return_value=self.response(401, 'bad token')):
            message = self.send(message)
        self.assertEqual((message.status, message.retry_count), ('pending', 1))
        self.assertEqual((message.error_status_code, message.error_message), (401, 'bad token'))
        self.assertEqual(message.next_retry, now + datetime.timedelta(seconds=60))

    def test_request_exception(self):
        message = Message.objects.create(channel=channel(), message='m')
        with mock.patch('message_center.functions.requests.post', side_effect=OSError('dns')):
            message = self.send(message)
        self.assertIn('Request exception dns', message.error_message)

    def test_gives_up_after_max_retry(self):
        message = Message.objects.create(channel=channel(), message='m', retry_count=4)
        with mock.patch('message_center.functions.requests.post') as post:
            message = self.send(message)
        post.assert_not_called()
        self.assertEqual(message.status, 'failed')

    def test_only_pending_messages_are_sent(self):
        message = Message.objects.create(channel=channel(), message='m', status='sent')
        with mock.patch('message_center.functions.requests.post') as post:
            self.send(message)
        post.assert_not_called()

    def test_cron_sends_new_before_retries(self):
        ch = channel()
        fresh = Message.objects.create(channel=ch, message='new')
        Message.objects.create(channel=ch, message='retry', next_retry=timezone.now() - datetime.timedelta(seconds=1))
        with mock.patch('message_center.functions.requests.post', return_value=self.response()):
            data = self.client.get('/cron/send_messages/').json()
        self.assertEqual(data['messages_sent'], 1)
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, 'sent')

    def test_update_notice_is_appended_only_when_an_update_is_known(self):
        ch = channel()
        message = Message.objects.create(channel=ch, message='hello')
        with mock.patch('message_center.functions.requests.post', return_value=self.response()):
            self.client.get('/cron/send_messages/')
        message.refresh_from_db()
        self.assertEqual(message.message, 'hello')

        WebadminSettings.objects.update_or_create(name='webadmin_settings', defaults={'update_available': True})
        second = Message.objects.create(channel=ch, message='again')
        with mock.patch('message_center.functions.requests.post', return_value=self.response()):
            self.client.get('/cron/send_messages/')
        second.refresh_from_db()
        self.assertIn('A new version of RouterFleet is available', second.message)


@override_settings(TIME_ZONE='UTC')
class DailyReportTests(TestCase):
    def setUp(self):
        self.channel = channel()
        MessageSettings.objects.create(daily_report_time='07:00')
        self.seven = timezone.now().replace(hour=7, minute=0, second=0, microsecond=0)

    def run_at(self, moment):
        with at(moment):
            return self.client.get('/cron/daily_reports/').json()

    def test_before_the_report_time_waits(self):
        data = self.run_at(self.seven - datetime.timedelta(minutes=30))
        self.assertEqual(data['status'], 'waiting for backup time')
        self.assertFalse(Message.objects.exists())

    def test_inside_the_window_one_report_per_run_backup_first(self):
        make_router(status_online=False)
        data = self.run_at(self.seven + datetime.timedelta(minutes=10))
        self.assertTrue(data['valid_report_window'])
        self.assertEqual(list(Message.objects.values_list('subject', flat=True)), ['Daily backup report'])
        self.run_at(self.seven + datetime.timedelta(minutes=20))
        self.assertEqual(sorted(Message.objects.values_list('subject', flat=True)),
                         ['Daily backup report', 'Daily status report'])
        # Not again the same day
        self.run_at(self.seven + datetime.timedelta(minutes=30))
        self.assertEqual(Message.objects.count(), 2)

    def test_outside_the_window_sends_nothing(self):
        self.run_at(self.seven + datetime.timedelta(minutes=90))
        self.assertFalse(Message.objects.exists())

    def test_status_report_lists_offline_devices(self):
        make_router(name='down-1', status_online=False)
        make_router(name='up-1')
        functions.generate_status_report({'report_time_exception': False})
        body = Message.objects.get().message
        self.assertIn('Monitored routers: 2', body)
        self.assertIn('Offline: 1', body)
        self.assertIn('down-1', body)

    def test_reports_keep_the_timezone_warning(self):
        functions.generate_backup_report({'report_time_exception': True})
        functions.generate_status_report({'report_time_exception': True})
        for message in Message.objects.all():
            self.assertTrue(message.message.startswith('Warning: Error calculating report time'),
                            message.subject)

    def test_backup_report_counts(self):
        router = make_router()
        RouterBackup.objects.create(router=router, schedule_type='daily', success=True)
        RouterBackup.objects.create(router=router, schedule_type='daily', error=True)
        functions.generate_backup_report({'report_time_exception': False})
        body = Message.objects.get().message
        self.assertIn('Backups completed: 1', body)
        self.assertIn('Backups failed: 1', body)
        self.assertIn('Backup ', body)


class FormTests(TestCase):
    def test_settings_bounds(self):
        form = MessageSettingsForm({'max_length': 100, 'max_retry': 9, 'retry_interval': 5,
                                    'status_change_delay': 5, 'backup_fails_delay': 5,
                                    'daily_report_time': '25:99'})
        self.assertFalse(form.is_valid())
        for field in ('max_length', 'max_retry', 'retry_interval', 'status_change_delay',
                      'backup_fails_delay', 'daily_report_time'):
            self.assertIn(field, form.errors, field)

    def test_ntfy_needs_a_topic(self):
        form = MessageChannelForm({'name': 'n', 'channel_type': 'ntfy', 'enabled': True})
        self.assertFalse(form.is_valid())
        self.assertIn('destination', form.errors)

    def test_telegram_sends_a_test_message_before_saving(self):
        data = {'name': 'tg', 'channel_type': 'telegram', 'destination': '1', 'token': 'T', 'enabled': True}
        with mock.patch('message_center.forms.requests.get',
                        return_value=mock.Mock(status_code=401, text='Unauthorized')):
            form = MessageChannelForm(data)
            self.assertFalse(form.is_valid())
            self.assertIn('Unauthorized', str(form.errors))
        with mock.patch('message_center.forms.requests.get', return_value=mock.Mock(status_code=200)):
            self.assertTrue(MessageChannelForm(data).is_valid())


class ChannelFormTests(TestCase):
    def form(self, **data):
        values = {'name': 'n', 'enabled': True}
        values.update(data)
        return MessageChannelForm(values)

    def test_callmebot_needs_token_and_destination_and_a_working_test(self):
        self.assertIn('requires a token', str(self.form(channel_type='callmebot').errors))
        with mock.patch('message_center.forms.requests.get', return_value=mock.Mock(status_code=500, text='quota')):
            self.assertIn('quota', str(self.form(channel_type='callmebot', destination='1', token='k').errors))
        with mock.patch('message_center.forms.requests.get', return_value=mock.Mock(status_code=200)):
            self.assertTrue(self.form(channel_type='callmebot', destination='1', token='k').is_valid())

    def test_telegram_needs_token(self):
        self.assertIn('requires a token', str(self.form(channel_type='telegram', destination='1').errors))

    def test_ntfy_test_message_and_failures(self):
        import requests
        with mock.patch('message_center.forms.requests.post', return_value=mock.Mock(status_code=200)) as post:
            form = self.form(channel_type='ntfy', destination='topic', token='ignored')
            self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['token'], '')
        self.assertEqual(post.call_args.args[0], 'https://ntfy.sh/topic')
        with mock.patch('message_center.forms.requests.post', return_value=mock.Mock(status_code=429, text='slow down')):
            self.assertIn('slow down', str(self.form(channel_type='ntfy', destination='t').errors))
        with mock.patch('message_center.forms.requests.post',
                        side_effect=requests.exceptions.ConnectionError('offline')):
            self.assertIn('Network error', str(self.form(channel_type='ntfy', destination='t').errors))

    def test_disabled_channel_sends_no_test(self):
        with mock.patch('message_center.forms.requests.get') as get:
            self.assertTrue(self.form(channel_type='telegram', destination='1', token='t', enabled=False).is_valid())
        get.assert_not_called()

    def test_length_limits(self):
        form = self.form(channel_type='ntfy', destination='x' * 101, enabled=False)
        self.assertFalse(form.is_valid())


class ViewTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(40))

    def test_unknown_channel_is_not_found(self):
        response = self.client.get('/message_center/manage_channel/?uuid=00000000-0000-0000-0000-000000000000')
        self.assertEqual(response.status_code, 404)

    def test_delete_channel_needs_confirmation(self):
        ch = channel()
        self.client.post(f'/message_center/manage_channel/?uuid={ch.uuid}', {'action': 'delete', 'confirmation': 'no'})
        self.assertTrue(MessageChannel.objects.filter(pk=ch.pk).exists())
        self.client.post(f'/message_center/manage_channel/?uuid={ch.uuid}', {'action': 'delete', 'confirmation': 'delete'})
        self.assertFalse(MessageChannel.objects.filter(pk=ch.pk).exists())

    def test_list_and_history(self):
        self.assertEqual(self.client.get('/message_center/channel_list/').status_code, 200)
        self.assertEqual(self.client.get('/message_center/message_history/').status_code, 200)
