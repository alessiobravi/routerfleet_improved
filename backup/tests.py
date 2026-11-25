from django.core.files.base import ContentFile
from django.test import TestCase

from backup.forms import BackupProfileForm
from backup.models import BackupProfile
from backup_data.models import RouterBackup
from router_manager.models import BackupSchedule, RouterStatus
from routerfleet.testing import make_profile, make_router, make_user, temporary_media_root
from django.utils import timezone


class ProfileTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(40))

    def test_list_creates_the_default_profile(self):
        self.client.get('/backup/profile_list/')
        self.assertTrue(BackupProfile.objects.filter(name='default').exists())

    def test_default_and_in_use_profiles_cannot_be_deleted(self):
        default = make_profile(name='default')
        used = make_profile()
        make_router(backup_profile=used)
        unused = make_profile()
        for profile in (default, used, unused):
            self.client.post(f'/backup/manage_profile/?uuid={profile.uuid}', {'action': 'delete', 'confirmation': 'delete'})
        remaining = set(BackupProfile.objects.values_list('pk', flat=True))
        self.assertEqual(remaining, {default.pk, used.pk})

    def test_saving_a_profile_resets_its_schedules_and_error(self):
        profile = make_profile(profile_error_information='old error')
        router = make_router(backup_profile=profile)
        BackupSchedule.objects.create(router=router, next_daily_backup=timezone.now())
        form = BackupProfileForm(instance=profile)
        data = {name: field.value() for name, field in ((n, form[n]) for n in form.fields)}
        data = {k: v for k, v in data.items() if v is not None and v is not False}
        response = self.client.post(f'/backup/manage_profile/?uuid={profile.uuid}', data)
        self.assertRedirects(response, '/backup/profile_list/', fetch_redirect_response=False)
        profile.refresh_from_db()
        self.assertEqual(profile.profile_error_information, '')
        self.assertFalse(BackupSchedule.objects.filter(router=router).exists())


class BackupViewTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(30))
        self.router = make_router()
        self.a = RouterBackup.objects.create(router=self.router, schedule_type='daily', success=True,
                                             backup_text='line 1\r\nline 2   \n', backup_text_filename='a.rsc')
        self.b = RouterBackup.objects.create(router=self.router, schedule_type='daily', success=True,
                                             backup_text='line 1\nline 3\n')

    def test_lists(self):
        for view, expected in (('', self.b), ('?type=errors', None), ('?type=queue', None)):
            backups = list(self.client.get('/backup/backup_list/' + view).context['backup_list'])
            if expected:
                self.assertIn(expected, backups)

    def test_compare_normalises_line_endings_and_trailing_space(self):
        diff = self.client.get(f'/backup/compare/?uuid={self.a.uuid}&compare_uuid={self.b.uuid}').context['diff_str']
        self.assertIn('-line 2', diff)
        self.assertIn('+line 3', diff)
        self.assertNotIn('\r', diff)
        self.assertNotIn('line 2   ', diff)

    def test_compare_with_a_backup_that_has_no_text(self):
        empty = RouterBackup.objects.create(router=self.router, schedule_type='daily')
        response = self.client.get(f'/backup/compare/?uuid={empty.uuid}&compare_uuid={self.b.uuid}&display=all')
        self.assertEqual(response.status_code, 200)

    def test_compare_offers_the_phone_view_and_keeps_content_as_data(self):
        RouterBackup.objects.filter(pk=self.b.pk).update(backup_text='line 1\n</script><script>alert(1)</script>')
        page = self.client.get(f'/backup/compare/?uuid={self.a.uuid}&compare_uuid={self.b.uuid}').content.decode()
        for mode in ('changes', 'side-by-side', 'inline'):
            self.assertIn(f'data-mode="{mode}"', page)
        self.assertNotIn('<script>alert(1)</script>', page, 'configuration text only ever as JSON data')

    def test_config_change_popup(self):
        data = self.client.get(f'/backup/config_change/?uuid={self.b.uuid}').json()
        self.assertEqual(data['previous']['uuid'], str(self.a.uuid))
        self.assertIn('+line 3', data['diff'])
        self.assertIn(f'compare_uuid={self.b.uuid}', data['compare_url'])

    def test_config_change_first_backup_and_unknown(self):
        self.assertEqual(self.client.get(f'/backup/config_change/?uuid={self.a.uuid}').status_code, 404)
        self.assertEqual(self.client.get(
            '/backup/config_change/?uuid=00000000-0000-0000-0000-000000000000').status_code, 404)

    def test_download_text_and_binary(self):
        response = self.client.get(f'/backup/download/?uuid={self.a.uuid}&type=text')
        self.assertEqual(response['Content-Disposition'], 'attachment; filename=a.rsc')
        response = self.client.get(f'/backup/download/?uuid={self.b.uuid}&type=text')
        self.assertIn('.missing_backup_name.rsc', response['Content-Disposition'])
        with temporary_media_root():
            self.a.backup_binary.save('a.backup', ContentFile(b'BIN'))
            response = self.client.get(f'/backup/download/?uuid={self.a.uuid}&type=binary')
            self.assertEqual(b''.join(response.streaming_content), b'BIN')
        self.assertEqual(self.client.get(f'/backup/download/?uuid={self.a.uuid}&type=other').status_code, 404)

    def test_delete_needs_the_id_and_frees_the_lock(self):
        RouterStatus.objects.filter(router=self.router).update(backup_lock=timezone.now())
        self.client.post(f'/backup/delete/?uuid={self.a.uuid}', {'confirmation': 'delete'})
        self.assertTrue(RouterBackup.objects.filter(pk=self.a.pk).exists())
        self.client.post(f'/backup/delete/?uuid={self.a.uuid}', {'confirmation': f'delete{self.a.id}'})
        self.assertFalse(RouterBackup.objects.filter(pk=self.a.pk).exists())
        self.assertIsNone(RouterStatus.objects.get(router=self.router).backup_lock)

    def test_details_and_anticipate(self):
        pending = RouterBackup.objects.create(router=self.router, schedule_type='daily',
                                              next_retry=timezone.now() + timezone.timedelta(hours=1))
        response = self.client.get(f'/backup/backup_details/?uuid={self.b.uuid}&show_console=true')
        self.assertTrue(response.context['show_console'])
        self.assertIn(self.a, response.context['backup_list'])
        self.client.post(f'/backup/backup_details/?uuid={pending.uuid}', {'action': 'anticipate'})
        pending.refresh_from_db()
        self.assertLessEqual(pending.next_retry, timezone.now())


class BackupListPerformanceTests(TestCase):
    """The backup list stays fast however many backups there are: a fixed
    number of queries, no configuration text read, newest rows first."""

    def setUp(self):
        from django.test.utils import CaptureQueriesContext
        self.capture = CaptureQueriesContext
        routers = [make_router(name=f'perf{i}') for i in range(5)]
        RouterBackup.objects.bulk_create([
            RouterBackup(router=routers[i % 5], schedule_type='daily', success=True, backup_text='x' * 5000,
                         backup_text_hash=f'{i:064x}', task_console_output='y' * 1000) for i in range(600)])
        self.client.force_login(make_user(20))

    def test_queries_do_not_grow_with_the_rows(self):
        from django.db import connection
        for path in ('/backup/backup_list/', '/backup/backup_list/?all=1', '/backup/backup_list/?type=errors',
                     '/backup/backup_list/?type=queue'):
            with self.subTest(path=path), self.capture(connection) as queries:
                self.assertEqual(self.client.get(path).status_code, 200)
            self.assertLess(len(queries), 25, path)
            listed = [q['sql'] for q in queries.captured_queries if 'backup_data_routerbackup' in q['sql']
                      and 'ORDER BY' in q['sql']]
            for sql in listed:
                self.assertNotIn('"backup_text"', sql, 'the configuration text is never read for the list')
                self.assertNotIn('task_console_output', sql)

    def test_the_newest_are_shown_and_the_rest_reachable(self):
        page = self.client.get('/backup/backup_list/')
        self.assertEqual(len(page.context['backup_list']), 500)
        self.assertTrue(page.context['limited'])
        self.assertContains(page, 'The newest 500 of 600 backups')
        self.assertContains(page, 'all=1')
        everything = self.client.get('/backup/backup_list/?all=1')
        self.assertEqual(len(everything.context['backup_list']), 600)
        self.assertFalse(everything.context['limited'])


class BackupListPaneTests(TestCase):
    """The backup list: the devices with a backup on the left, All devices
    first; a device's history on the right; the status page's filters keep
    applying."""

    def setUp(self):
        from routerfleet.testing import make_router, make_user
        from backup_data.models import RouterBackup
        self.client.force_login(make_user(20))
        self.a, self.b = make_router(name='alpha'), make_router(name='beta')
        for router in (self.a, self.a, self.b):
            RouterBackup.objects.create(router=router, success=True, schedule_type='daily', backup_text='x')

    def test_all_devices_then_one(self):
        page = self.client.get('/backup/backup_list/')
        self.assertEqual(page.context['all_count'], 3)
        self.assertEqual([d['router__name'] for d in page.context['devices']], ['alpha', 'beta'])
        self.assertContains(page, 'All devices')
        self.assertEqual(len(page.context['backup_list']), 3)
        one = self.client.get(f'/backup/backup_list/?device={self.a.uuid}')
        self.assertEqual(one.context['chosen'], self.a)
        self.assertEqual(len(one.context['backup_list']), 2)
        self.assertContains(one, 'id="compare-two"')

    def test_the_status_page_filters_and_the_tab_are_kept(self):
        page = self.client.get(f'/backup/backup_list/?type=errors&since=24h&device={self.a.uuid}')
        self.assertEqual(len(page.context['backup_list']), 0)
        self.assertIn('type=errors', page.context['base_query'])
        self.assertIn('since=24h', page.context['base_query'])
        self.assertNotIn('device', page.context['base_query'])
