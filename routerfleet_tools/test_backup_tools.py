"""The backup tools: clean-up and export."""
import csv
import io
import os
import zipfile

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from audit_log.models import ChangeRecord
from backup.models import BackupProfile
from backup_data.models import RouterBackup
from router_manager.models import Router, RouterGroup
from routerfleet.testing import make_user, temporary_media_root
from routerfleet_tools import backup_tools


class Base(TestCase):
    def setUp(self):
        media = temporary_media_root()
        media.__enter__()
        self.addCleanup(media.__exit__, None, None, None)
        self.only_changes = BackupProfile.objects.create(name='changes')
        self.every = BackupProfile.objects.create(name='every', keep_unchanged=True)
        self.a = Router.objects.create(name='edge a', address='192.0.2.1', router_type='routeros', backup_profile=self.only_changes)
        self.b = Router.objects.create(name='edge-b', address='192.0.2.2', router_type='routeros', backup_profile=self.every)

    def backup(self, router, text, days_ago=0, kind='daily', binary=b'binary-content'):
        backup = RouterBackup.objects.create(router=router, schedule_type=kind, success=True, finish_time=timezone.now(),
                                             backup_text=text, backup_text_filename=f'{router.name}.rsc')
        if binary:
            backup.backup_binary.save(f'routerfleet-backup-{backup.id}.backup', ContentFile(binary))
        RouterBackup.objects.filter(pk=backup.pk).update(created=timezone.now() - timezone.timedelta(days=days_ago, seconds=-backup.id))
        backup.refresh_from_db()
        return backup


class CleanupTests(Base):
    def test_duplicates_are_found_and_freed_as_unchanged_backups(self):
        first = self.backup(self.a, 'A', days_ago=3)
        dup1 = self.backup(self.a, 'A', days_ago=2)
        changed = self.backup(self.a, 'B', days_ago=1)
        dup2 = self.backup(self.a, 'B')
        report = backup_tools.audit()
        self.assertEqual(report.duplicates, 2)
        self.assertGreater(report.reclaimable_bytes, 0)
        _, freed = backup_tools.clean()
        self.assertEqual(freed['duplicates'], 2)
        for backup, stored in ((dup1, first), (dup2, changed)):
            backup.refresh_from_db()
            self.assertEqual((backup.backup_text, bool(backup.backup_binary), backup.same_as), ('', False, stored))
            self.assertTrue(backup.success)
        first.refresh_from_db()
        self.assertTrue(first.backup_text and first.backup_binary)
        self.assertEqual(backup_tools.audit().duplicates, 0)
        self.assertTrue(ChangeRecord.objects.filter(note='Backup clean-up').exists())

    def test_profiles_that_keep_every_backup_are_left_alone_unless_asked(self):
        self.backup(self.b, 'X', days_ago=1)
        dup = self.backup(self.b, 'X')
        self.assertEqual(backup_tools.audit().duplicates, 0)
        self.assertEqual(backup_tools.audit(include_every_profile=True).duplicates, 1)
        backup_tools.clean()
        dup.refresh_from_db()
        self.assertTrue(dup.backup_text)
        backup_tools.clean(include_every_profile=True)
        dup.refresh_from_db()
        self.assertEqual(dup.backup_text, '')

    def test_instant_backups_are_never_touched(self):
        self.backup(self.a, 'A', days_ago=1)
        instant = self.backup(self.a, 'A', kind='instant')
        backup_tools.clean()
        instant.refresh_from_db()
        self.assertTrue(instant.backup_text and instant.backup_binary)

    def test_files_and_database_are_reconciled(self):
        kept = self.backup(self.a, 'A')
        directory = os.path.join(settings.MEDIA_ROOT, 'backups')
        orphan = os.path.join(directory, 'left-behind.backup')
        with open(orphan, 'wb') as handle:
            handle.write(b'x' * 100)
        missing = self.backup(self.a, 'B', days_ago=1)
        os.remove(missing.backup_binary.path)
        report = backup_tools.audit()
        self.assertEqual([name for name, _ in report.orphan_files], ['backups/left-behind.backup'])
        self.assertEqual(report.missing_files, [missing])
        _, freed = backup_tools.clean()
        self.assertEqual((freed['orphans'], freed['missing']), (1, 1))
        self.assertFalse(os.path.exists(orphan))
        self.assertTrue(os.path.exists(kept.backup_binary.path), 'a referenced file stays')
        missing.refresh_from_db()
        self.assertFalse(missing.backup_binary)

    def test_a_lost_link_is_repaired(self):
        first = self.backup(self.a, 'A', days_ago=3)
        second = self.backup(self.a, 'A', days_ago=2)
        third = self.backup(self.a, 'A', days_ago=1)
        third.drop_if_unchanged()
        RouterBackup.objects.filter(pk=third.pk).update(same_as=None)   # its stored version was deleted
        second.refresh_from_db()
        report = backup_tools.audit()
        self.assertEqual(report.relinkable, 1)
        backup_tools.clean()
        third.refresh_from_db()
        self.assertIn(third.same_as_id, (first.id, second.id))
        self.assertEqual(third.content.backup_text, 'A')


class ExportTests(Base):
    def open_zip(self, archive):
        return zipfile.ZipFile(io.BytesIO(archive.read()))

    def test_the_latest_configuration_of_each_device_one_file_each(self):
        self.backup(self.a, 'old', days_ago=2)
        latest = self.backup(self.a, 'latest', days_ago=1)
        self.backup(self.b, 'b-config')
        archive, count = backup_tools.export_latest()
        self.assertEqual(count, 2)
        names = self.open_zip(archive).namelist()
        self.assertIn('edge_a/edge_a.rsc', names, 'unsafe characters become _')
        self.assertIn('edge-b/edge-b.rsc', names)
        self.assertIn(f'edge_a/routerfleet-backup-{latest.id}.backup', names)
        self.assertIn('manifest.csv', names)
        archive.seek(0)
        bundle = self.open_zip(archive)
        self.assertEqual(bundle.read('edge_a/edge_a.rsc'), b'latest')
        manifest = list(csv.reader(io.StringIO(bundle.read('manifest.csv').decode())))
        self.assertEqual(manifest[0][0], 'router')
        self.assertEqual(len(manifest), 3)

    def test_an_unchanged_latest_exports_the_configuration_it_repeats(self):
        self.backup(self.a, 'same', days_ago=1)
        unchanged = self.backup(self.a, 'same')
        unchanged.drop_if_unchanged()
        archive, _ = backup_tools.export_latest()
        bundle = self.open_zip(archive)
        self.assertEqual(bundle.read('edge_a/edge_a.rsc'), b'same')
        self.assertTrue(list(csv.reader(io.StringIO(bundle.read('manifest.csv').decode())))[1][4], 'unchanged_since filled')

    def test_without_binaries_and_by_group(self):
        self.backup(self.a, 'a')
        self.backup(self.b, 'b')
        group = RouterGroup.objects.create(name='g')
        group.routers.add(self.b)
        archive, count = backup_tools.export_latest(include_binary=False, group=group)
        names = self.open_zip(archive).namelist()
        self.assertEqual(count, 1)
        self.assertEqual(sorted(names), ['edge-b/edge-b.rsc', 'manifest.csv'])

    def test_the_export_is_recorded(self):
        self.backup(self.a, 'a')
        backup_tools.export_latest()
        self.assertTrue(ChangeRecord.objects.filter(note='Backup export').exists())


class ToolPagesTests(Base):
    def test_only_administrators(self):
        self.client.force_login(make_user(40))
        for url in ('/tools/', '/tools/backup_cleanup/', '/tools/backup_export/'):
            self.assertContains(self.client.get(url), 'Access Denied')
        self.assertContains(self.client.post('/tools/backup_export/'), 'Access Denied')

    def test_cleanup_page_needs_the_confirmation(self):
        self.client.force_login(make_user(50))
        self.backup(self.a, 'A', days_ago=1)
        dup = self.backup(self.a, 'A')
        self.assertContains(self.client.get('/tools/backup_cleanup/'), '1</div><div class="small text-muted">duplicates')
        self.client.get('/tools/backup_cleanup/?action=clean&confirmation=clean')
        self.client.post('/tools/backup_cleanup/', {'action': 'clean', 'confirmation': 'wrong'})
        dup.refresh_from_db()
        self.assertTrue(dup.backup_text, 'neither a GET nor a wrong word cleans')
        self.client.post('/tools/backup_cleanup/', {'action': 'clean', 'confirmation': 'clean'})
        dup.refresh_from_db()
        self.assertEqual(dup.backup_text, '')

    def test_export_downloads_a_zip(self):
        self.client.force_login(make_user(50))
        self.backup(self.a, 'A')
        response = self.client.post('/tools/backup_export/', {'include_binary': 'on'})
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('attachment; filename="routerfleet-latest-backups-', response['Content-Disposition'])
        bundle = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
        self.assertIn('manifest.csv', bundle.namelist())

    def test_export_with_nothing_to_export(self):
        self.client.force_login(make_user(50))
        self.assertRedirects(self.client.post('/tools/backup_export/'), '/tools/backup_export/', fetch_redirect_response=False)
