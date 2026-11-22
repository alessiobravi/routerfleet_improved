import datetime
import io
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import RequestFactory, TestCase
from django.utils import timezone

from audit_log import context
from audit_log.bulk import collect, suspend
from audit_log.models import AuditLogImmutable, ChangeRecord, LoginRecord
from fleet_commander.models import Command, CommandSchedule
from routerfleet.testing import make_group, make_router, make_sshkey, make_user


def new_records(before):
    return list(ChangeRecord.objects.order_by('created', 'id')[before:])


class ChangeCaptureTests(TestCase):
    def test_create_change_delete(self):
        before = ChangeRecord.objects.count()
        router = make_router(name='edge-1', port=22)
        router.port = 2222
        router.save()
        uuid = router.uuid
        router.delete()
        created, changed, deleted = new_records(before)[-3:]
        self.assertEqual((created.action, created.object_repr, created.object_uuid), ('created', 'edge-1', uuid))
        self.assertEqual(changed.action, 'changed')
        self.assertEqual(changed.changes, {'Port': {'old': 22, 'new': 2222}})
        self.assertEqual(deleted.action, 'deleted')

    def test_secrets_are_compared_but_never_stored(self):
        router = make_router(password='first')
        before = ChangeRecord.objects.count()
        router.password = 'second'
        router.save()
        record = new_records(before)[0]
        self.assertEqual(record.changes, {'Password': {'old': '(set)', 'new': '(set)'}})
        self.assertNotIn('second', str(record.changes))
        router.password = ''
        router.save()
        self.assertEqual(new_records(before)[1].changes['Password'], {'old': '(set)', 'new': '(empty)'})

    def test_private_key_is_redacted(self):
        before = ChangeRecord.objects.count()
        make_sshkey()
        self.assertNotIn('FAKE-TEST-PRIVATE-KEY-MATERIAL', str(new_records(before)[0].changes))

    def test_a_save_that_changes_nothing_writes_nothing(self):
        router = make_router()
        before = ChangeRecord.objects.count()
        router.save()
        router.save(update_fields=['updated'])
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_schedule_bookkeeping_is_not_recorded(self):
        schedule = CommandSchedule.objects.create(command=Command.objects.create(name='c'))
        before = ChangeRecord.objects.count()
        schedule.next_run = timezone.now()
        schedule.save()
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_membership_changes(self):
        group = make_group(name='G')
        router = make_router(name='r1')
        before = ChangeRecord.objects.count()
        group.routers.add(router)
        record = new_records(before)[0]
        self.assertEqual(record.object_repr, 'G')
        self.assertIn('r1', str(record.changes))

    def test_unwatched_models_are_not_recorded(self):
        before = ChangeRecord.objects.count()
        from routerfleet_tools.models import WebadminSettings
        WebadminSettings.objects.create(name='x')
        self.assertEqual(ChangeRecord.objects.count(), before)


class ActorTests(TestCase):
    def test_command_line_when_there_is_no_request(self):
        before = ChangeRecord.objects.count()
        make_router()
        self.assertEqual(new_records(before)[0].actor_kind, 'command')

    def test_signed_in_user_through_the_interface(self):
        user = make_user(40)
        self.client.force_login(user)
        before = ChangeRecord.objects.count()
        self.client.post('/router/manage_group/', {'name': 'from-ui'}, HTTP_X_REAL_IP='198.51.100.7',
                         HTTP_USER_AGENT='TestAgent/1')
        record = new_records(before)[0]
        self.assertEqual((record.actor, record.actor_kind, record.actor_name, record.actor_level),
                         (user, 'user', user.username, 40))
        self.assertEqual((record.ip, record.user_agent), ('198.51.100.7', 'TestAgent/1'))

    def test_cron_request_has_no_user(self):
        request = RequestFactory().get('/cron/x/')
        request.user = mock.Mock(is_authenticated=False)
        self.assertEqual(context.actor(request), (None, 'cron'))

    def test_actor_survives_account_deletion(self):
        user = make_user(40)
        self.client.force_login(user)
        self.client.post('/router/manage_group/', {'name': 'g'})
        name = user.username
        user.delete()
        record = ChangeRecord.objects.filter(actor_name=name).first()
        self.assertIsNotNone(record)
        self.assertIsNone(record.actor)


class ClientAddressTests(TestCase):
    def addr(self, **meta):
        return context.client_address(RequestFactory().get('/', **meta))

    def test_real_ip_wins(self):
        self.assertEqual(self.addr(HTTP_X_REAL_IP='10.0.0.1', HTTP_X_FORWARDED_FOR='1.1.1.1, 2.2.2.2'),
                         ('10.0.0.1', '1.1.1.1, 2.2.2.2'))

    def test_last_forwarded_entry_not_the_first(self):
        self.assertEqual(self.addr(HTTP_X_FORWARDED_FOR='6.6.6.6, 203.0.113.5')[0], '203.0.113.5')

    def test_remote_addr_last(self):
        self.assertEqual(self.addr(REMOTE_ADDR='192.0.2.9')[0], '192.0.2.9')

    def test_no_request(self):
        self.assertEqual(context.client_address(None), ('', ''))


class BulkTests(TestCase):
    def test_collect_writes_one_entry(self):
        before = ChangeRecord.objects.count()
        with collect('Mass add'):
            for _ in range(3):
                make_router()
        records = new_records(before)
        self.assertEqual([(r.action, r.note, r.affected_count) for r in records], [('bulk', 'Mass add', 3)])

    def test_collect_that_does_nothing_writes_nothing(self):
        before = ChangeRecord.objects.count()
        with collect('Nothing'):
            pass
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_suspend_writes_nothing(self):
        before = ChangeRecord.objects.count()
        with suspend():
            make_router()
            with collect('inner'):
                make_router()
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_default_commands_are_not_recorded(self):
        self.client.force_login(make_user(20))
        before = ChangeRecord.objects.count()
        self.client.get('/fleet_commander/')
        self.assertTrue(Command.objects.exists())
        self.assertEqual(ChangeRecord.objects.count(), before)


class ImmutabilityTests(TestCase):
    def setUp(self):
        self.record = ChangeRecord.objects.create(action='changed')
        self.login = LoginRecord.objects.create(username='x')

    def test_instance_delete_is_refused(self):
        with self.assertRaises(AuditLogImmutable):
            self.record.delete()
        with self.assertRaises(AuditLogImmutable):
            self.login.delete()

    def test_queryset_delete_is_refused(self):
        from django.db import transaction
        with self.assertRaises(AuditLogImmutable), transaction.atomic():
            ChangeRecord.objects.all().delete()
        self.assertTrue(ChangeRecord.objects.filter(pk=self.record.pk).exists())


class PruneCommandTests(TestCase):
    def setUp(self):
        with mock.patch('django.utils.timezone.now', return_value=timezone.now() - datetime.timedelta(days=400)):
            self.old_change = ChangeRecord.objects.create(action='changed')
            self.old_login = LoginRecord.objects.create(username='old')
        self.new_change = ChangeRecord.objects.create(action='changed')

    def prune(self, *args):
        out = io.StringIO()
        call_command('prune_audit_log', *args, stdout=out)
        return out.getvalue()

    def test_age_is_required(self):
        with self.assertRaises(CommandError):
            call_command('prune_audit_log')

    def test_age_must_be_a_positive_number(self):
        for value in ('0', 'abc', '-5'):
            with self.assertRaises(CommandError):
                self.prune('--older-than', value)

    def test_dry_run_removes_nothing(self):
        out = self.prune('--older-than', '365', '--dry-run')
        self.assertIn('2 entries would be removed', out)
        self.assertEqual(ChangeRecord.objects.count(), 2)

    def test_prunes_both_logs(self):
        self.assertIn('2 entries removed', self.prune('--older-than', '365'))
        self.assertEqual(list(ChangeRecord.objects.all()), [self.new_change])
        self.assertFalse(LoginRecord.objects.exists())

    def test_only_one_log(self):
        self.prune('--older-than', '365', '--login-history')
        self.assertTrue(ChangeRecord.objects.filter(pk=self.old_change.pk).exists())
        self.assertFalse(LoginRecord.objects.exists())

    def test_the_door_closes_again(self):
        self.prune('--older-than', '365')
        with self.assertRaises(AuditLogImmutable):
            self.new_change.delete()


class LoginHistoryTests(TestCase):
    def setUp(self):
        self.user = make_user(30, username='alice', password='right-password')

    def test_successful_sign_in_and_sign_out(self):
        self.client.post('/accounts/login/', {'username': 'alice', 'password': 'right-password'},
                         HTTP_X_REAL_IP='192.0.2.50', HTTP_USER_AGENT='Firefox')
        record = LoginRecord.objects.get()
        self.assertTrue(record.successful)
        self.assertEqual((record.actor, record.user_level, record.ip, record.user_agent),
                         (self.user, 30, '192.0.2.50', 'Firefox'))
        self.assertTrue(record.session_key)
        self.assertIsNone(record.logged_out_at)
        self.client.post('/accounts/logout/')
        record.refresh_from_db()
        self.assertIsNotNone(record.logged_out_at)

    def test_refused_attempt_records_the_name_as_typed_even_for_nobody(self):
        self.client.post('/accounts/login/', {'username': 'mallory', 'password': 'guess'})
        record = LoginRecord.objects.get()
        self.assertFalse(record.successful)
        self.assertEqual((record.username, record.actor, record.actor_kind), ('mallory', None, 'anonymous'))

    def test_anonymous_logout_records_nothing(self):
        self.client.post('/accounts/logout/')
        self.assertFalse(LoginRecord.objects.exists())


class ViewTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(50))

    def test_change_detail_lists_fields_in_recorded_order(self):
        # Recorded order for a router: Name, Enabled, Address, Port. Stored in
        # another order on purpose - PostgreSQL's jsonb sorts keys by length,
        # which would give Name, Port, Enabled, Address
        record = ChangeRecord.objects.create(action='changed', model_label='router_manager.Router', object_repr='r',
                                             changes={'Port': {'old': 22, 'new': 2222},
                                                      'Address': {'old': 'a', 'new': 'b'},
                                                      'Name': {'old': 'x', 'new': 'y'},
                                                      'Enabled': {'old': True, 'new': False},
                                                      'Retired field': {'old': 1, 'new': 2}})
        data = self.client.get(f'/audit_log/change_detail/?uuid={record.uuid}').json()
        order = ['Name', 'Enabled', 'Address', 'Port', 'Retired field']
        self.assertEqual([c['field'] for c in data['changes']], order)
        self.assertEqual(record.field_summary, ', '.join(order))
        self.assertEqual(data['model'], 'Router')

    def test_change_detail_unknown_or_malformed(self):
        self.assertEqual(self.client.get('/audit_log/change_detail/?uuid=nope').status_code, 404)
        self.assertEqual(self.client.get(
            '/audit_log/change_detail/?uuid=00000000-0000-0000-0000-000000000000').status_code, 404)

    def test_bulk_entry_display(self):
        record = ChangeRecord.objects.create(action='bulk', note='Retention', affected_count=4)
        self.assertEqual((record.display_object, record.field_summary), ('Retention', '4 rows'))

    def test_pages_render_and_cap_rows(self):
        self.assertEqual(self.client.get('/audit_log/change_log/').context['row_limit'], 1000)
        self.assertEqual(self.client.get('/audit_log/login_history/').status_code, 200)

    def test_open_session_marker(self):
        alice = make_user(10, username='alice2', password='pw-alice-2')
        other = self.client_class()
        other.post('/accounts/login/', {'username': 'alice2', 'password': 'pw-alice-2'})
        response = self.client.get('/audit_log/login_history/')
        record = LoginRecord.objects.get(actor=alice)
        self.assertIn(record.session_key, response.context['open_sessions'])
