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
        self.assertEqual(message.subject, 'Device status change: Online')
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
        self.assertEqual(message.subject, 'Device status change: Offline')
        self.assertIn('Device a', message.message)
        self.assertIn('Device b', message.message)
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
        with mock.patch('message_center.channels.requests.post', return_value=self.response()) as post:
            message = self.send(message)
        post.assert_called_once_with('https://ntfy.sh/my-topic', data=b'body', headers={'Priority': '3', 'Title': 'S'},
                                     timeout=15, allow_redirects=False)
        self.assertEqual(message.status, 'sent')
        self.assertIsNotNone(message.completed)

    def test_telegram_uses_get(self):
        tg = Message.objects.create(channel=channel('tg', 'telegram', destination='42', token='123:ABC'), message='hi')
        with mock.patch('message_center.channels.requests.get', return_value=self.response()) as get:
            self.send(tg)
        self.assertEqual(get.call_args.args[0], 'https://api.telegram.org/bot123:ABC/sendMessage')
        self.assertEqual(get.call_args.kwargs['params'], {'chat_id': '42', 'text': 'hi'})

    def test_callmebot_is_refused_with_its_reason(self):
        cb = Message.objects.create(channel=channel('cb', 'callmebot', destination='+12025550143', token='K'), message='yo')
        with mock.patch('message_center.channels.requests.get') as get:
            cb = self.send(cb)
        get.assert_not_called()
        self.assertIn('no longer supported', cb.error_message)

    def test_message_text_cannot_add_parameters(self):
        # A device called "edge&chat_id=other": the text stays one value
        import requests
        tg = Message.objects.create(channel=channel('tg', 'telegram', destination='42', token='1:t'),
                                    message='edge&chat_id=other #1 is offline')
        with mock.patch('message_center.channels.requests.get', return_value=self.response()) as get:
            self.send(tg)
        prepared = requests.Request('GET', get.call_args.args[0], params=get.call_args.kwargs['params']).prepare()
        self.assertIn('text=edge%26chat_id%3Dother+%231+is+offline', prepared.url)
        self.assertEqual(prepared.url.count('chat_id='), 1)

    def test_ntfy_own_server_token_and_priority(self):
        ch = channel('own', 'ntfy', destination='alerts', token='tk_abc123')
        ch.server_url, ch.priority = 'https://ntfy.example.net', 5
        ch.save()
        message = Message.objects.create(channel=ch, subject='Down\nX-Evil: 1', message='body')
        with mock.patch('console.network.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('203.0.113.7', 443))]), \
                mock.patch('message_center.channels.requests.post', return_value=self.response()) as post:
            self.send(message)
        self.assertEqual(post.call_args.args[0], 'https://ntfy.example.net/alerts')
        headers = post.call_args.kwargs['headers']
        self.assertEqual((headers['Priority'], headers['Authorization'], headers['Title']), ('5', 'Bearer tk_abc123', 'Down X-Evil: 1'))

    def test_ntfy_server_on_routerfleets_own_network_is_refused(self):
        ch = channel('inside', 'ntfy', destination='alerts')
        ch.server_url = 'http://routerfleet-postgres:5432'
        ch.save()
        message = Message.objects.create(channel=ch, message='body')
        with mock.patch('message_center.channels.requests.post') as post:
            message = self.send(message)
        post.assert_not_called()
        self.assertIn('RouterFleet itself', message.error_message)

    def test_a_failed_send_never_stores_the_token(self):
        import requests
        tg = Message.objects.create(channel=channel('tg', 'telegram', destination='42', token='123:SECRET'), message='hi')
        boom = requests.exceptions.ConnectionError(
            "HTTPSConnectionPool(host='api.telegram.org'): Max retries exceeded with url: /bot123:SECRET/sendMessage")
        with mock.patch('message_center.channels.requests.get', side_effect=boom):
            tg = self.send(tg)
        self.assertEqual(tg.status, 'pending')
        self.assertNotIn('SECRET', tg.error_message)
        self.assertIn('***', tg.error_message)

    def test_a_channel_saved_before_validation_cannot_steer_the_request(self):
        # Channels saved by earlier versions were never checked: a topic or token
        # that would leave its place in the URL is refused before any request
        ntfy = Message.objects.create(channel=channel(destination='../v1/account'), message='x')
        tg = Message.objects.create(channel=channel('tg', 'telegram', destination='42', token='x/../../evil'), message='y')
        with mock.patch('message_center.channels.requests.post') as post, \
                mock.patch('message_center.channels.requests.get') as get:
            ntfy, tg = self.send(ntfy), self.send(tg)
        post.assert_not_called()
        get.assert_not_called()
        for message in (ntfy, tg):
            self.assertIn('not valid', message.error_message)

    def test_quoting_still_applies_to_valid_values(self):
        from message_center import channels
        with mock.patch('message_center.channels.requests.post', return_value=self.response()) as post:
            channels.send('ntfy', None, 'topic_1-a', 'x')
        self.assertEqual(post.call_args.args[0], 'https://ntfy.sh/topic_1-a')

    def test_failure_is_stored_and_retried(self):
        MessageSettings.objects.create(retry_interval=60)
        message = Message.objects.create(channel=channel(), message='m')
        now = timezone.now()
        with at(now), mock.patch('message_center.channels.requests.post',
                                 return_value=self.response(401, 'bad token')):
            message = self.send(message)
        self.assertEqual((message.status, message.retry_count), ('pending', 1))
        self.assertEqual((message.error_status_code, message.error_message), (401, 'bad token'))
        self.assertEqual(message.next_retry, now + datetime.timedelta(seconds=60))

    def test_request_exception(self):
        message = Message.objects.create(channel=channel(), message='m')
        with mock.patch('message_center.channels.requests.post', side_effect=OSError('dns')):
            message = self.send(message)
        self.assertIn('Request exception dns', message.error_message)

    def test_gives_up_after_max_retry(self):
        message = Message.objects.create(channel=channel(), message='m', retry_count=4)
        with mock.patch('message_center.channels.requests.post') as post:
            message = self.send(message)
        post.assert_not_called()
        self.assertEqual(message.status, 'failed')

    def test_only_pending_messages_are_sent(self):
        message = Message.objects.create(channel=channel(), message='m', status='sent')
        with mock.patch('message_center.channels.requests.post') as post:
            self.send(message)
        post.assert_not_called()

    def test_cron_sends_new_before_retries(self):
        ch = channel()
        fresh = Message.objects.create(channel=ch, message='new')
        Message.objects.create(channel=ch, message='retry', next_retry=timezone.now() - datetime.timedelta(seconds=1))
        with mock.patch('message_center.channels.requests.post', return_value=self.response()):
            data = self.client.get('/cron/send_messages/').json()
        self.assertEqual(data['messages_sent'], 1)
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, 'sent')

    def test_update_notice_is_appended_only_when_an_update_is_known(self):
        ch = channel()
        message = Message.objects.create(channel=ch, message='hello')
        with mock.patch('message_center.channels.requests.post', return_value=self.response()):
            self.client.get('/cron/send_messages/')
        message.refresh_from_db()
        self.assertEqual(message.message, 'hello')

        WebadminSettings.objects.update_or_create(name='webadmin_settings', defaults={'update_available': True})
        second = Message.objects.create(channel=ch, message='again')
        with mock.patch('message_center.channels.requests.post', return_value=self.response()):
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
        self.assertIn('Monitored devices: 2', body)
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
        data = {'name': 'tg', 'channel_type': 'telegram', 'destination': '1', 'token': '123:ABC', 'enabled': True}
        with mock.patch('message_center.channels.requests.get',
                        return_value=mock.Mock(status_code=401, text='Unauthorized')):
            form = MessageChannelForm(data)
            self.assertFalse(form.is_valid())
            self.assertIn('Unauthorized', str(form.errors))
        with mock.patch('message_center.channels.requests.get', return_value=mock.Mock(status_code=200)):
            self.assertTrue(MessageChannelForm(data).is_valid())


class ChannelFormTests(TestCase):
    def form(self, **data):
        values = {'name': 'n', 'enabled': True}
        values.update(data)
        return MessageChannelForm(values)

    def test_callmebot_can_not_be_chosen_any_more(self):
        form = self.form(channel_type='callmebot', destination='+1202555', token='k')
        self.assertFalse(form.is_valid())
        self.assertIn('channel_type', form.errors, 'not among the choices')

    def test_ntfy_token_needs_https(self):
        form = self.form(channel_type='ntfy', destination='t', token='tk_x', server_url='http://ntfy.example.net')
        with mock.patch('console.network.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('203.0.113.7', 80))]):
            self.assertIn('only sent over HTTPS', str(form.errors))

    def test_telegram_needs_token(self):
        self.assertIn('requires a token', str(self.form(channel_type='telegram', destination='1').errors))

    def test_ntfy_test_message_and_failures(self):
        import requests
        with mock.patch('message_center.channels.requests.post', return_value=mock.Mock(status_code=200)) as post:
            form = self.form(channel_type='ntfy', destination='topic', token='tk_kept')
            self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['token'], 'tk_kept')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer tk_kept')
        self.assertEqual(post.call_args.args[0], 'https://ntfy.sh/topic')
        with mock.patch('message_center.channels.requests.post', return_value=mock.Mock(status_code=429, text='slow down')):
            self.assertIn('slow down', str(self.form(channel_type='ntfy', destination='t').errors))
        with mock.patch('message_center.channels.requests.post',
                        side_effect=requests.exceptions.ConnectionError('offline')):
            self.assertIn('Network error', str(self.form(channel_type='ntfy', destination='t').errors))

    def test_disabled_channel_sends_no_test(self):
        with mock.patch('message_center.channels.requests.get') as get:
            self.assertTrue(self.form(channel_type='telegram', destination='1', token='1:t', enabled=False).is_valid())
        get.assert_not_called()

    def test_values_must_look_like_what_the_service_issues(self):
        cases = [
            ({'channel_type': 'telegram', 'token': 'not-a-token', 'destination': '42'}, 'token'),
            ({'channel_type': 'telegram', 'token': '1:a', 'destination': '42&text=x'}, 'destination'),
            ({'channel_type': 'ntfy', 'destination': 'topic', 'token': 'bad token!'}, 'token'),
            ({'channel_type': 'ntfy', 'destination': '../v1/account'}, 'destination'),
        ]
        for data, field in cases:
            with self.subTest(data=data), mock.patch('message_center.channels.requests.get') as get, \
                    mock.patch('message_center.channels.requests.post') as post:
                form = self.form(**data)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)
                get.assert_not_called()
                post.assert_not_called()

    def test_a_network_error_in_the_test_does_not_show_the_token(self):
        import requests
        boom = requests.exceptions.ConnectionError('Max retries exceeded with url: /bot99:TOPSECRET/sendMessage')
        with mock.patch('message_center.channels.requests.get', side_effect=boom):
            errors = str(self.form(channel_type='telegram', destination='42', token='99:TOPSECRET').errors)
        self.assertIn('Network error', errors)
        self.assertNotIn('TOPSECRET', errors)

    def test_length_limits(self):
        form = self.form(channel_type='ntfy', destination='x' * 101, enabled=False)
        self.assertFalse(form.is_valid())


class ViewTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(50))

    def test_unknown_channel_is_not_found(self):
        response = self.client.get('/system/notifications/channel/?uuid=00000000-0000-0000-0000-000000000000')
        self.assertEqual(response.status_code, 404)

    def test_delete_channel_needs_confirmation(self):
        ch = channel()
        self.client.post(f'/system/notifications/channel/?uuid={ch.uuid}', {'action': 'delete', 'confirmation': 'no'})
        self.assertTrue(MessageChannel.objects.filter(pk=ch.pk).exists())
        self.client.post(f'/system/notifications/channel/?uuid={ch.uuid}', {'action': 'delete', 'confirmation': 'delete'})
        self.assertFalse(MessageChannel.objects.filter(pk=ch.pk).exists())

    def test_list_and_history(self):
        self.assertEqual(self.client.get('/system/notifications/').status_code, 200)
        # The old pages lead to System Settings
        self.assertRedirects(self.client.get('/message_center/channel_list/'), '/system/notifications/', fetch_redirect_response=False)
        ch = channel()
        self.assertRedirects(self.client.get(f'/message_center/manage_channel/?uuid={ch.uuid}'),
                             f'/system/notifications/channel/?uuid={ch.uuid}', fetch_redirect_response=False)
        # Anything but an identifier is not carried over
        self.assertRedirects(self.client.get('/message_center/manage_channel/?uuid=x%26next%3D//evil.example'),
                             '/system/notifications/channel/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/message_center/message_history/').status_code, 200)


class SendBudgetTests(TestCase):
    """A send run stops starting sends after its budget, so the next minute's
    run never takes the same pending messages: nothing is sent twice."""

    def test_the_run_stops_at_its_budget(self):
        from unittest import mock
        from message_center.models import Message
        ops = channel()
        for number in range(3):
            Message.objects.create(channel=ops, message=f'm{number}', subject='s', status='pending')
        clock = iter([0, 10, 50, 60, 70])
        with mock.patch('message_center.views.time.monotonic', side_effect=lambda: next(clock)), \
                mock.patch('message_center.views.send_notification_message') as send:
            data = self.client.get('/cron/send_messages/').json()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(data['stopped'], 'budget')
