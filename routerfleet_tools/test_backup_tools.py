"""The backup tools: clean-up and export."""
import csv
import io
import os
import zipfile

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import TestCase, TransactionTestCase
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
    def test_a_device_can_decide_otherwise_than_its_profile(self):
        # b's profile keeps every backup; b itself says: only changes
        Router.objects.filter(pk=self.b.pk).update(keep_unchanged='changes')
        self.b.refresh_from_db()
        self.backup(self.b, 'X', days_ago=1)
        self.backup(self.b, 'X')
        self.assertEqual(backup_tools.audit().duplicates, 1)
        later = self.backup(self.b, 'X', days_ago=-1)
        self.assertTrue(later.drop_if_unchanged(), 'new unchanged backups are not stored again')
        # a's profile keeps only changes; a itself says: keep everything
        Router.objects.filter(pk=self.a.pk).update(keep_unchanged='keep')
        self.a.refresh_from_db()
        self.backup(self.a, 'Y', days_ago=1)
        again = self.backup(self.a, 'Y')
        self.assertFalse(again.drop_if_unchanged())
        self.assertEqual(backup_tools.audit().held_back, 1)

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
        # ... but not hidden: the page says why nothing is cleaned
        self.assertEqual(backup_tools.audit().held_back, 1)
        self.client.force_login(make_user(50))
        page = self.client.get('/tools/backup_cleanup/')
        self.assertContains(page, '1 duplicate')
        self.assertContains(page, 'left alone')
        self.assertNotContains(page, 'Nothing to clean: no duplicates')
        # Which profile decides, linked, so a change to the wrong one is visible
        self.assertContains(page, f'/backup/manage_profile/?uuid={self.every.uuid}')
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

    def test_colliding_names_extra_texts_and_a_safe_manifest(self):
        # "edge a" already becomes edge_a; a device named "edge_a" must not share its folder
        twin = Router.objects.create(name='edge_a', address='192.0.2.3', router_type='routeros', backup_profile=self.every)
        evil = Router.objects.create(name='=HYPERLINK("x")', address='192.0.2.4', router_type='routeros',
                                     backup_profile=self.every)
        self.backup(self.a, 'first', binary=None)
        self.backup(twin, 'second', binary=None)
        juniper = self.backup(self.b, 'set config', binary=None)
        RouterBackup.objects.filter(pk=juniper.pk).update(extra_texts=[{'name': 'set', 'text': 'set system host-name b'}])
        self.backup(evil, 'evil', binary=None)
        archive, count = backup_tools.export_latest()
        bundle = self.open_zip(archive)
        names = bundle.namelist()
        self.assertEqual(count, 4)
        self.assertIn('edge_a/edge_a.rsc', names)
        self.assertIn(f'edge_a-{twin.id}/edge_a.rsc', names, 'its own folder')
        self.assertEqual(bundle.read(f'edge_a-{twin.id}/edge_a.rsc'), b'second')
        self.assertIn('edge-b/edge-b-set.rsc', names, 'the extra named text')
        rows = list(csv.reader(io.StringIO(bundle.read('manifest.csv').decode())))
        self.assertIn("'=HYPERLINK(\"x\")", [row[0] for row in rows], 'shown as text, never a formula')

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


class DatabaseMaintenanceTests(TestCase):
    def test_tools_page_shows_the_database(self):
        self.client.force_login(make_user(50))
        page = self.client.get('/tools/')
        self.assertContains(page, 'Optimize the database')
        self.assertContains(page, 'Backup clean-up')

    def test_optimize_needs_the_confirmation(self):
        from unittest import mock
        self.client.force_login(make_user(50))
        with mock.patch('routerfleet_tools.database.optimize', return_value=(2097152, 1048576)) as optimize:
            self.client.post('/tools/', {'action': 'optimize'})
            optimize.assert_not_called()
            response = self.client.post('/tools/', {'action': 'optimize', 'confirmation': 'optimize'})
        optimize.assert_called_once_with(full=False)
        self.assertRedirects(response, '/tools/', fetch_redirect_response=False)

    def test_inside_a_transaction_it_refuses(self):
        from routerfleet_tools import database
        with self.assertRaises(RuntimeError):
            database.optimize()
        database.size()  # None for an in-memory test database, a number otherwise
        database.analyze()


class DatabaseOptimizeCommandTests(TransactionTestCase):
    def test_the_command_runs(self):
        import io
        from django.core.management import call_command
        out = io.StringIO()
        call_command('optimize_database', stdout=out)
        self.assertIn('Database optimized', out.getvalue())


class DeleteBinariesTests(Base):
    def setUp(self):
        super().setUp()
        self.openwrt = Router.objects.create(name='ow', address='192.0.2.9', router_type='openwrt',
                                             backup_profile=self.only_changes)
        self.client.force_login(make_user(50))

    def test_only_the_chosen_types_lose_their_binaries(self):
        import os
        mikrotik = self.backup(self.a, 'A', days_ago=1)
        other = self.backup(self.openwrt, 'B', days_ago=1)
        path = mikrotik.backup_binary.path
        summary = {row['slug']: row for row in backup_tools.binary_summary()}
        self.assertEqual(summary['routeros']['count'], 1)
        page = self.client.get('/tools/backup_cleanup/')
        self.assertContains(page, 'Delete the chosen binary backups')
        # Without the confirmation nothing happens
        self.client.post('/tools/backup_cleanup/', {'action': 'delete_binaries', 'types': ['routeros']})
        mikrotik.refresh_from_db()
        self.assertTrue(mikrotik.backup_binary)
        self.client.post('/tools/backup_cleanup/', {'action': 'delete_binaries', 'types': ['routeros'],
                                                    'confirmation': 'delete_binaries'})
        mikrotik.refresh_from_db()
        other.refresh_from_db()
        self.assertFalse(mikrotik.backup_binary)
        self.assertFalse(os.path.exists(path), 'the file is gone from the disk')
        self.assertEqual(RouterBackup.objects.get(pk=mikrotik.pk).backup_text, 'A', 'the text is kept')
        self.assertTrue(other.backup_binary, 'OpenWrt was not chosen')
        self.assertTrue(ChangeRecord.objects.filter(note='Binary backups deleted').exists())

    def test_unknown_types_are_ignored(self):
        self.backup(self.a, 'A')
        self.client.post('/tools/backup_cleanup/', {'action': 'delete_binaries', 'types': ['no-such-type'],
                                                    'confirmation': 'delete_binaries'})
        self.assertTrue(RouterBackup.objects.exclude(backup_binary='').exclude(backup_binary__isnull=True).exists())


class OptimizeOutcomeTests(TestCase):
    """A plain PostgreSQL VACUUM is not reported as if it shrank the files:
    it makes space reusable, and its maps of free space may add a little."""

    def test_the_words_for_each_case(self):
        from routerfleet_tools.database import outcome
        mb = 1048576
        plain = outcome(int(17.3 * mb), int(18.2 * mb), False, 'postgresql')
        self.assertIn('reusable', plain)
        self.assertIn('Only VACUUM FULL gives space back to the disk', plain)
        self.assertNotIn('From', plain)
        self.assertEqual(outcome(20 * mb, 15 * mb, True, 'postgresql'), 'From 20.0 MB to 15.0 MB (5.0 MB given back to the disk).')
        self.assertIn('nothing to give back', outcome(10 * mb, 10 * mb, False, 'sqlite'))
        self.assertIn('unknown size', outcome(None, None, True, 'postgresql'))
