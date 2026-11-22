"""Keeping only changes, and retention that never loses the last configuration."""
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from backup.models import BackupProfile
from backup_data.models import RouterBackup
from router_manager.models import Router
from routerfleet.testing import make_user
from routerfleet.testing import temporary_media_root


class Base(TestCase):
    def setUp(self):
        media = temporary_media_root()
        media.__enter__()
        self.addCleanup(media.__exit__, None, None, None)
        self.profile = BackupProfile.objects.create(name='p')
        self.router = Router.objects.create(name='r', address='192.0.2.1', router_type='routeros',
                                            backup_profile=self.profile)

    def backup(self, text, kind='daily', days_ago=0, binary=b'bin'):
        b = RouterBackup.objects.create(router=self.router, schedule_type=kind, success=True,
                                        finish_time=timezone.now(), backup_text=text)
        if binary:
            b.backup_binary.save(f'test-{b.id}.backup', ContentFile(binary))
        if days_ago:
            RouterBackup.objects.filter(pk=b.pk).update(created=timezone.now() - timezone.timedelta(days=days_ago))
            b.refresh_from_db()
        return b


class KeepOnlyChangesTests(Base):
    def test_an_unchanged_backup_keeps_its_record_but_not_its_content(self):
        first = self.backup('/ip address add 1', days_ago=2)
        second = self.backup('/ip address add 1')
        self.assertTrue(second.drop_if_unchanged())
        second.refresh_from_db()
        self.assertEqual((second.backup_text, bool(second.backup_binary)), ('', False))
        self.assertEqual(second.same_as, first)
        self.assertTrue(second.success)
        self.assertEqual(second.backup_text_hash, first.backup_text_hash, 'the configuration is still known')
        self.assertFalse(second.config_change_detected)
        self.assertEqual(second.content, first)

    def test_a_change_is_stored_in_full(self):
        self.backup('/ip address add 1', days_ago=2)
        changed = self.backup('/ip address add 2')
        self.assertFalse(changed.drop_if_unchanged())
        changed.refresh_from_db()
        self.assertTrue(changed.backup_text and changed.backup_binary and changed.config_change_detected)

    def test_unchanged_runs_point_at_the_stored_version_not_at_each_other(self):
        first = self.backup('A', days_ago=3)
        second = self.backup('A', days_ago=2)
        second.drop_if_unchanged()
        third = self.backup('A')
        third.drop_if_unchanged()
        third.refresh_from_db()
        self.assertEqual(third.same_as, first)

    def test_keep_unchanged_stores_everything(self):
        self.profile.keep_unchanged = True
        self.profile.save()
        self.backup('A', days_ago=2)
        self.assertFalse(self.backup('A').drop_if_unchanged())

    def test_instant_backups_are_always_kept(self):
        self.backup('A', days_ago=2)
        self.assertFalse(self.backup('A', kind='instant').drop_if_unchanged())

    def test_the_default_for_profiles_is_keep_only_changes(self):
        self.assertFalse(BackupProfile.objects.create(name='new').keep_unchanged)


class ViewsReadTheStoredContentTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(make_user(50))
        self.first = self.backup('/system identity set name=edge', days_ago=1)
        self.second = self.backup('/system identity set name=edge')
        self.second.drop_if_unchanged()

    def test_details_show_and_download_the_stored_configuration(self):
        page = self.client.get(f'/backup/backup_details/?uuid={self.second.uuid}')
        self.assertContains(page, 'name=edge')
        self.assertContains(page, 'not stored again')
        text = self.client.get(f'/backup/download/?uuid={self.second.uuid}&type=text')
        self.assertEqual(text.content, b'/system identity set name=edge')
        binary = self.client.get(f'/backup/download/?uuid={self.second.uuid}&type=binary')
        self.assertEqual(b''.join(binary.streaming_content), b'bin')

    def test_comparing_with_an_unchanged_backup_shows_no_difference(self):
        response = self.client.get(f'/backup/compare/?uuid={self.first.uuid}&compare_uuid={self.second.uuid}')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('+/system', response.context['diff_str'])

    def test_the_list_marks_unchanged_backups(self):
        self.assertContains(self.client.get('/backup/backup_list/'), '>unchanged</span>')


class RetentionKeepsTheLastConfigurationTests(Base):
    def housekeeping(self):
        self.assertEqual(self.client.get('/cron/housekeeping/').status_code, 200)

    def test_a_device_offline_for_longer_than_its_retention_keeps_its_newest_backup(self):
        old = self.backup('A', days_ago=20)
        older = self.backup('B', days_ago=30)
        self.housekeeping()
        self.assertTrue(RouterBackup.objects.filter(pk=old.pk).exists(), 'the newest stored configuration stays')
        self.assertFalse(RouterBackup.objects.filter(pk=older.pk).exists(), 'older copies still expire')

    def test_a_stored_version_stays_while_a_kept_backup_points_to_it(self):
        stored = self.backup('A', days_ago=10)
        unchanged = self.backup('A', days_ago=1)
        unchanged.drop_if_unchanged()
        newer_change = self.backup('B')
        self.housekeeping()
        self.assertTrue(RouterBackup.objects.filter(pk=stored.pk).exists())
        unchanged.refresh_from_db()
        self.assertEqual(unchanged.content.backup_text, 'A')
        self.assertTrue(RouterBackup.objects.filter(pk=newer_change.pk).exists())

    def test_expired_unchanged_runs_go_and_then_their_stored_version(self):
        stored = self.backup('A', days_ago=12)
        unchanged = self.backup('A', days_ago=11)
        unchanged.drop_if_unchanged()
        self.backup('B')
        self.housekeeping()
        self.assertFalse(RouterBackup.objects.filter(pk__in=[stored.pk, unchanged.pk]).exists())
