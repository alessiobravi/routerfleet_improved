"""The corners of the audit trail's receivers, the read-only admin, and the
removal of a deleted backup's file: the paths the everyday tests do not take."""
from unittest import mock

from django.contrib import admin
from django.test import RequestFactory, TestCase

from audit_log import registry, signals
from audit_log.bulk import collect, suspend
from audit_log.models import ChangeRecord, LoginRecord
from fleet_commander.models import Command, CommandSchedule
from router_manager.models import Router, RouterGroup
from routerfleet.testing import make_group, make_router, make_user


def new_records(before):
    return list(ChangeRecord.objects.order_by('created', 'id')[before:])


class ConnectTests(TestCase):
    """Connecting skips a membership that names no field, and connecting twice
    is harmless."""

    def test_a_membership_without_a_field_is_skipped(self):
        real_build = registry.build

        def build_with_a_bad_membership():
            real_build()
            registry.watch(RouterGroup, fields=['name'], m2m=['no_such_field'])

        try:
            with mock.patch.object(registry, 'build', side_effect=build_with_a_bad_membership):
                signals.connect()
            self.assertEqual(registry.watched(RouterGroup).m2m, ('no_such_field',))
        finally:
            signals.connect()
        self.assertEqual(registry.watched(RouterGroup).m2m, ('routers',))

    def test_the_through_model_of_an_unknown_field_is_none(self):
        self.assertIsNone(signals.m2m_through(RouterGroup, 'no_such_field'))
        self.assertIsNone(signals.m2m_through(Router, 'name'))
        self.assertIs(signals.m2m_through(RouterGroup, 'routers'), RouterGroup.routers.through)


class SaveReceiverTests(TestCase):
    """A fixture load writes nothing, a save without a row read before writes
    nothing, a suspended save writes nothing, and an error never escapes."""

    def test_a_raw_save_is_ignored(self):
        router = make_router()
        before = ChangeRecord.objects.count()
        signals.on_pre_save(Router, router, raw=True)
        self.assertFalse(hasattr(router, '_audit_before'))
        signals.on_post_save(Router, router, raw=True)
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_a_change_without_a_row_read_before_writes_nothing(self):
        router = make_router()
        before = ChangeRecord.objects.count()
        signals.on_post_save(Router, router, created=False)
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_a_change_inside_suspend_writes_nothing(self):
        router = make_router(port=22)
        before = ChangeRecord.objects.count()
        with suspend():
            router.port = 2200
            router.save()
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_errors_are_logged_and_dropped(self):
        router = make_router()
        group = make_group()
        with mock.patch.object(registry, 'watched', side_effect=RuntimeError('test')), \
                self.assertLogs('audit_log.signals', 'WARNING') as logs:
            signals.on_pre_save(Router, router)
            signals.on_post_save(Router, router)
            signals.on_post_delete(Router, router)
            signals.on_m2m_changed(RouterGroup.routers.through, group, 'post_add')
        self.assertEqual(len(logs.output), 4)

    def test_an_entry_that_can_not_be_written_is_dropped(self):
        record = mock.Mock()
        record.save.side_effect = RuntimeError('test')
        with self.assertLogs('audit_log.signals', 'WARNING'):
            signals._write(record)


class DeleteReceiverTests(TestCase):
    """A deletion of an unwatched model, of one that keeps no deletions, or one
    inside suspend() writes nothing."""

    def test_unwatched_or_not_kept_deletions_write_nothing(self):
        from console.models import ConsoleSession
        before = ChangeRecord.objects.count()
        signals.on_post_delete(ConsoleSession, mock.Mock())
        from routerfleet_tools.models import WebadminSettings
        signals.on_post_delete(WebadminSettings, mock.Mock())
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_a_deletion_inside_suspend_writes_nothing(self):
        router = make_router()
        before = ChangeRecord.objects.count()
        with suspend():
            router.delete()
        self.assertEqual(ChangeRecord.objects.count(), before)


class MembershipReceiverTests(TestCase):
    """Removing and clearing members, a removal of a stranger, the far side of
    the relation, and a long list of names."""

    def setUp(self):
        self.group = make_group(name='G')
        self.first = make_router(name='r-first')
        self.second = make_router(name='r-second')
        self.group.routers.add(self.first, self.second)

    def test_removing_and_clearing_are_recorded(self):
        before = ChangeRecord.objects.count()
        self.group.routers.remove(self.first)
        self.group.routers.clear()
        removed, cleared = new_records(before)
        self.assertEqual(list(removed.changes.values()), [{'old': ['r-first'], 'new': []}])
        self.assertEqual(list(cleared.changes.values()), [{'old': ['r-second'], 'new': []}])

    def test_removing_a_non_member_writes_nothing(self):
        stranger = make_router(name='r-stranger')
        before = ChangeRecord.objects.count()
        self.group.routers.remove(stranger)
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_a_removal_without_the_set_before_writes_nothing(self):
        before = ChangeRecord.objects.count()
        signals.on_m2m_changed(RouterGroup.routers.through, self.group, 'post_remove',
                               model=Router, pk_set={self.first.pk})
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_other_actions_write_nothing(self):
        before = ChangeRecord.objects.count()
        signals.on_m2m_changed(RouterGroup.routers.through, self.group, 'pre_add',
                               model=Router, pk_set={self.first.pk})
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_the_far_side_and_unwatched_or_foreign_senders_write_nothing(self):
        before = ChangeRecord.objects.count()
        self.first.routergroup_set.remove(self.group)
        signals.on_m2m_changed(RouterGroup.routers.through, self.first, 'post_add', reverse=True)
        from routerfleet_tools.models import WebadminSettings
        signals.on_m2m_changed(RouterGroup.routers.through, WebadminSettings(name='x'), 'post_add')
        schedule_through = CommandSchedule.router.through
        signals.on_m2m_changed(schedule_through, self.group, 'post_add', model=Router, pk_set={self.first.pk})
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_a_later_membership_of_a_schedule_is_found(self):
        schedule = CommandSchedule.objects.create(command=Command.objects.create(name='c'))
        before = ChangeRecord.objects.count()
        schedule.exclude_router.add(self.first)
        record = new_records(before)[0]
        self.assertIn('r-first', str(record.changes))

    def test_a_membership_change_inside_suspend_writes_nothing(self):
        third = make_router(name='r-third')
        before = ChangeRecord.objects.count()
        with suspend():
            self.group.routers.add(third)
        self.assertEqual(ChangeRecord.objects.count(), before)

    def test_a_membership_change_inside_a_batch_is_counted(self):
        third = make_router(name='r-third')
        before = ChangeRecord.objects.count()
        with collect('test batch'):
            self.group.routers.add(third)
        records = new_records(before)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].note, 'test batch')

    def test_a_long_list_is_capped(self):
        third = make_router(name='r-third')
        before = ChangeRecord.objects.count()
        with mock.patch.object(registry, 'M2M_LIST_LIMIT', 1):
            self.group.routers.clear()
            self.group.routers.add(self.first, self.second, third)
        added = new_records(before)[-1]
        new = list(added.changes.values())[0]['new']
        self.assertEqual(new, ['r-first', 'and 2 more'])

    def test_names_of_nothing_are_empty(self):
        self.assertEqual(signals._names(Router, set()), {})
        self.assertEqual(signals._names(None, {1}), {})

    def test_a_stash_holds_several_fields(self):
        holder = mock.Mock(spec=[])
        signals._stash(holder, 'a', {1})
        signals._stash(holder, 'b', {2})
        self.assertEqual(signals._unstash(holder, 'a'), {1})
        self.assertEqual(signals._unstash(holder, 'b'), {2})
        self.assertIsNone(signals._unstash(holder, 'a'))


class SignInReceiverTests(TestCase):
    """Signing in and out with nothing to record, without a session key, and
    with an error that must not break the sign-in."""

    def test_a_sign_in_without_a_user_writes_nothing(self):
        before = LoginRecord.objects.count()
        signals.on_user_logged_in(None, request=None, user=None)
        self.assertEqual(LoginRecord.objects.count(), before)

    def test_errors_while_signing_in_or_out_are_dropped(self):
        user = make_user(20)
        with mock.patch.object(LoginRecord, 'save', side_effect=RuntimeError('test')), \
                self.assertLogs('audit_log.signals', 'WARNING') as logs:
            signals.on_user_logged_in(None, request=None, user=user)
            signals.on_user_login_failed(None, credentials={'username': 'x'}, request=None)
        with mock.patch.object(LoginRecord.objects, 'filter', side_effect=RuntimeError('test')), \
                self.assertLogs('audit_log.signals', 'WARNING') as more:
            signals.on_user_logged_out(None, request=None, user=user)
        self.assertEqual(len(logs.output) + len(more.output), 3)

    def test_a_sign_out_without_a_session_key_closes_the_latest_entry(self):
        user = make_user(20)
        request = RequestFactory().get('/')
        request.user = user
        signals.on_user_logged_in(None, request=request, user=user)
        record = LoginRecord.objects.get(actor=user)
        self.assertEqual(record.session_key, '')
        self.assertIsNone(record.logged_out_at)
        signals.on_user_logged_out(None, request=request, user=user)
        record.refresh_from_db()
        self.assertIsNotNone(record.logged_out_at)
        # Nothing left open: a second sign-out finds nothing to close
        signals.on_user_logged_out(None, request=None, user=user)


class AuditAdminTests(TestCase):
    """The admin shows the trail and offers nothing that changes it."""

    def test_nothing_can_be_added_changed_or_deleted(self):
        request = RequestFactory().get('/')
        request.user = make_user(50)
        for model in (LoginRecord, ChangeRecord):
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
            self.assertIsNone(model_admin.actions)
            self.assertEqual(model_admin.get_readonly_fields(request),
                             [field.name for field in model._meta.fields])


class BackupFileRemovalTests(TestCase):
    """A deleted backup's file is removed when it exists; a missing file or a
    file that can not be removed never breaks the delete."""

    def instance(self, exists=True, error=None):
        stored = mock.Mock()
        stored.name = 'backups/test.backup'
        stored.storage.exists.return_value = exists
        if error:
            stored.storage.exists.side_effect = error
        return mock.Mock(backup_binary=stored), stored

    def test_an_existing_file_is_removed(self):
        from backup_data.signals import remove_binary_file
        instance, stored = self.instance()
        remove_binary_file(None, instance)
        stored.storage.delete.assert_called_once_with('backups/test.backup')

    def test_a_missing_file_is_left_alone(self):
        from backup_data.signals import remove_binary_file
        instance, stored = self.instance(exists=False)
        remove_binary_file(None, instance)
        stored.storage.delete.assert_not_called()

    def test_a_file_that_can_not_be_removed_is_logged(self):
        from backup_data.signals import remove_binary_file
        instance, stored = self.instance(error=OSError('test'))
        with self.assertLogs('backup_data.signals', 'WARNING'):
            remove_binary_file(None, instance)

    def test_a_backup_without_a_file_does_nothing(self):
        from backup_data.signals import remove_binary_file
        remove_binary_file(None, mock.Mock(backup_binary=None))
