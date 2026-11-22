import datetime

from django.test import TestCase
from django.utils import timezone

from backup_data.models import RouterBackup
from dashboard.views import human_readable_size, get_directory_statistics
from router_manager.models import RouterStatus
from routerfleet.testing import make_information, make_router, make_user


class StatusPageTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(10))

    def test_counts(self):
        up = make_router()
        make_information(up, os_version='7.17', available_version='7.17')
        make_router(status_online=False)
        make_router(monitoring=False)
        make_router(enabled=False)
        RouterBackup.objects.create(router=up, schedule_type='daily', success=True)
        RouterBackup.objects.create(router=up, schedule_type='daily', error=True)
        RouterBackup.objects.create(router=up, schedule_type='daily')
        context = self.client.get('/status/').context
        self.assertEqual(context['total_router_count'], 4)
        self.assertEqual((context['router_enabled_count'], context['router_disabled_count']), (3, 1))
        self.assertEqual(context['router_offline_count'], 1)
        self.assertEqual(context['router_not_monitored_count'], 1)
        self.assertEqual(context['queue'], 1)
        self.assertEqual((context['success_backup_last_24h'], context['error_backup_last_24h']), (1, 1))
        self.assertEqual(context['update_state']['up_to_date'], 1)

    def test_about_page_links_come_from_settings(self):
        with self.settings(PROJECT_REPOSITORY_URL='https://example.org/repo',
                           PROJECT_SUPPORT_URL='https://example.org/help'):
            response = self.client.get('/about/')
        self.assertContains(response, 'https://example.org/repo')
        self.assertContains(response, 'https://example.org/help')
        self.assertContains(response, 'MIT License')

    def test_the_home_page_is_the_status_page(self):
        self.assertRedirects(self.client.get('/'), '/status/', fetch_redirect_response=False)
        menu = self.client.get('/status/').content.decode()
        self.assertIn('href="/about/"', menu)
        # About is the last entry of the menu
        self.assertGreater(menu.index('href="/about/"'), menu.index('href="/audit_log/login_history/"'))

    def test_cards_follow_what_they_count(self):
        from backup_data.models import RouterBackup
        from router_manager.models import Router
        page = self.client.get('/status/').content.decode()
        # Nothing failed and every device is monitored: both cards green
        self.assertRegex(page, r'info-box bg-success">\s*<span class="info-box-icon"><i class="fas fa-check-circle"></i></span>\s*<div class="info-box-content">\s*<span class="info-box-text">Failed Backups')
        self.assertIn('Unmonitored Routers', page)
        self.assertNotIn('Not Monitored', page)
        # No version information: the update cards are not shown at all
        self.assertNotIn('Routers Up To Date', page)
        self.assertNotIn('Routers Missing Updates', page)

        router = Router.objects.create(name='u1', address='10.0.0.9', router_type='routeros', monitoring=False)
        RouterBackup.objects.create(router=router, schedule_type='daily', error=True)
        page = self.client.get('/status/').content.decode()
        self.assertIn('info-box bg-danger">\n                            <span class="info-box-icon"><i class="fas fa-times-circle"></i></span>', page)
        self.assertRegex(page, r'info-box bg-secondary">\s*<span class="info-box-icon"><i class="fas fa-question-circle"></i></span>\s*<div class="info-box-content">\s*<span class="info-box-text">Unmonitored Routers')


class ChartDataTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(10))

    def test_backup_statistics(self):
        RouterBackup.objects.create(router=make_router(), schedule_type='daily', success=True)
        data = self.client.get('/backup_statistics_data/?days=3').json()
        self.assertEqual(len(data['dates']), 3)

    def test_router_status_data(self):
        router = make_router(status_online=False)
        RouterStatus.objects.filter(router=router).update(
            last_status_change=timezone.now() - datetime.timedelta(days=10))
        data = self.client.get('/router_status_data/?days=5').json()
        self.assertEqual(data['offline_data'], [1] * 5)

    def test_days_must_be_an_allowed_value(self):
        for path in ('/backup_statistics_data/', '/router_status_data/'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path + '?days=4').status_code, 400)
                self.assertEqual(self.client.get(path + '?days=abc').status_code, 400)


class StorageTests(TestCase):
    def test_human_readable(self):
        self.assertEqual(human_readable_size(512), '512.00 B')
        self.assertEqual(human_readable_size(2048), '2.00 KB')
        self.assertEqual(human_readable_size(1024 ** 4), '1.00 TB')
        # Nothing beyond TB: a petabyte reads as 1024 TB
        self.assertEqual(human_readable_size(1024 ** 5), '1024.00 TB')

    def test_invalid_path(self):
        self.assertEqual(get_directory_statistics('/no/such/dir')['storage_warning'], 'Invalid path')
