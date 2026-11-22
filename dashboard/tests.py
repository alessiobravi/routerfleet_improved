import re
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

    def test_about_credits_the_author_and_the_earlier_projects(self):
        page = self.client.get('/about/').content.decode()
        self.assertIn('<strong>RouterFleet Improved</strong> by Alessio Bravi', page)
        # Every copyright holder of the licence is credited - read from LICENSE,
        # so no name is written into the code and a new holder is checked too
        import pathlib
        import re
        licence = (pathlib.Path(__file__).resolve().parent.parent / 'LICENSE').read_text()
        holders = re.findall(r'(?m)^Copyright \(c\) [0-9-]+ (.+)$', licence)
        self.assertGreater(len(holders), 1)
        for holder in holders:
            self.assertIn(holder.strip(), page)
        # Full width, and the buttons centred
        self.assertIn('<div class="col-12">', page)
        self.assertIn('<div class="text-center my-3">', page)
        self.assertIn('RouterFleet Improved by Alessio Bravi', self.client.get('/status/').content.decode())

    def test_the_home_page_is_the_status_page(self):
        self.assertRedirects(self.client.get('/'), '/status/', fetch_redirect_response=False)
        menu = self.client.get('/status/').content.decode()
        self.assertIn('href="/about/"', menu)
        # About is the last entry of the menu
        self.assertGreater(menu.index('href="/about/"'), menu.index('href="/audit_log/login_history/"'))

    def cards(self):
        return {card['title']: card for card in self.client.get('/status/').context['cards']}

    def test_cards_follow_what_they_count(self):
        from backup_data.models import RouterBackup
        from router_manager.models import Router
        cards = self.cards()
        # Nothing failed and every device is monitored: both cards green
        self.assertEqual((cards['Failed backups \u00b7 24h']['state'], cards['Failed backups \u00b7 24h']['value']), ('ok', 0))
        self.assertEqual(cards['Unmonitored devices']['state'], 'ok')
        # No version information: the update cards are not there at all
        self.assertNotIn('Missing OS updates', cards)
        self.assertNotIn('Devices up to date', cards)

        router = Router.objects.create(name='u1', address='192.0.2.9', router_type='routeros', monitoring=False)
        RouterBackup.objects.create(router=router, schedule_type='daily', error=True)
        cards = self.cards()
        self.assertEqual((cards['Failed backups \u00b7 24h']['state'], cards['Failed backups \u00b7 24h']['chip']), ('bad', 'Act'))
        self.assertEqual(cards['Unmonitored devices']['state'], 'info')
        self.assertTrue(cards['Failed backups \u00b7 24h']['spark'], 'backups keep history: a sparkline')
        self.assertFalse(cards['Backup queue']['spark'], 'the queue keeps none: no invented sparkline')

    def test_the_page_renders_the_cards_and_the_charts_on_top(self):
        page = self.client.get('/status/').content.decode()
        self.assertLess(page.index('id="backupChart"'), page.index('class="rf-cards'))
        self.assertIn('class="rf-card ok"', page)
        self.assertIn('Failed backups', page)


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


class MenuHighlightTests(TestCase):
    """The menu marks the page you are on, and only that one."""

    def active_entries(self, path):
        import re
        page = self.client.get(path).content.decode()
        return re.findall(r'<a href="([^"]+)" class="nav-link\s+active"', page)

    def test_my_settings_is_its_own_entry(self):
        self.client.force_login(make_user(50))
        self.assertEqual(self.active_entries('/user/settings/'), ['/user/settings/'])
        self.assertEqual(self.active_entries('/user/settings/totp/'), ['/user/settings/'])
        self.assertEqual(self.active_entries('/user/list/'), ['/user/list/'])
        self.assertEqual(self.active_entries('/status/'), ['/status/'])
        self.assertEqual(self.active_entries('/about/'), ['/about/'])

    def test_my_settings_sits_with_about_at_the_bottom(self):
        self.client.force_login(make_user(10))
        menu = self.client.get('/status/').content.decode()
        # The sidebar entry, not the gear icon of the top bar that links there too
        entry = menu.index('<a href="/user/settings/" class="nav-link ')
        self.assertLess(menu.index('href="/audit_log/login_history/"'), entry)
        self.assertLess(entry, menu.index('href="/about/"'))


class LogoTests(TestCase):
    def test_the_logo_is_in_the_brand_area_and_the_tab(self):
        self.client.force_login(make_user(10))
        page = self.client.get('/status/').content.decode()
        self.assertIn('<img src="/static/routerfleet/logo.svg" alt="" class="brand-image"', page)
        self.assertIn('<link rel="icon" href="/static/routerfleet/logo.svg"', page)
        self.client.logout()
        self.assertIn('rel="icon" href="/static/routerfleet/logo.svg"', self.client.get('/accounts/login/').content.decode())

    def test_the_readme_logo_is_the_application_logo(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        self.assertEqual((root / 'docs/images/routerfleet-improved.svg').read_text(),
                         (root / 'static_files/routerfleet/logo.svg').read_text())


class CardClickThroughTests(TestCase):
    """Every status card opens the list of what it counts - and the list holds
    exactly as many items as the card says."""

    def setUp(self):
        from django.utils import timezone
        from backup_data.models import RouterBackup
        from router_manager.models import Router, RouterStatus
        self.client.force_login(make_user(50))
        specs = [('on1', True, True, True), ('on2', True, True, True), ('off1', True, True, False),
                 ('dis1', False, True, True), ('unmon1', True, False, True)]
        self.routers = {}
        for name, enabled, monitoring, online in specs:
            router = Router.objects.create(name=name, address='192.0.2.1', router_type='routeros',
                                           enabled=enabled, monitoring=monitoring)
            RouterStatus.objects.update_or_create(router=router, defaults={'status_online': online})
            self.routers[name] = router
        RouterBackup.objects.create(router=self.routers['on1'], schedule_type='daily', error=True)
        old = RouterBackup.objects.create(router=self.routers['on2'], schedule_type='daily', error=True)
        RouterBackup.objects.filter(pk=old.pk).update(created=timezone.now() - timezone.timedelta(days=3))
        RouterBackup.objects.create(router=self.routers['on2'], schedule_type='daily', success=True)

    def listed(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return {router.name for router in response.context['router_list']}, response

    def test_each_router_card_lists_exactly_what_it_counts(self):
        status = self.client.get('/status/').context
        expected = {
            'online': status['router_online_count'], 'offline': status['router_offline_count'],
            'disabled': status['router_disabled_count'], 'unmonitored': status['router_not_monitored_count'],
            'backup_failed': 1,
            'up_to_date': status['update_state']['up_to_date'],
            'update_waiting': status['update_state']['update_waiting'],
            'no_information': status['update_state']['no_information'],
        }
        for state, count in expected.items():
            with self.subTest(state=state):
                names, response = self.listed(f'/router/list/?state={state}')
                self.assertEqual(len(names), count)
                self.assertContains(response, 'Showing only:')
        self.assertEqual(self.listed('/router/list/?state=backup_failed')[0], {'on1'},
                         'only failures of the last 24 hours')
        self.assertEqual(self.listed('/router/list/?state=offline')[0], {'off1'})

    def test_a_state_shows_every_group(self):
        from router_manager.models import RouterGroup
        group = RouterGroup.objects.create(name='some-group')
        group.routers.add(self.routers['on2'])
        self.assertEqual(self.listed(f'/router/list/?filter_group={group.uuid}')[0], {'on2'})
        # The state wins: what the card counted, whatever group was in the address
        names, response = self.listed(f'/router/list/?state=offline&filter_group={group.uuid}')
        self.assertEqual(names, {'off1'})
        self.assertIsNone(response.context['filter_group'])

    def test_an_unknown_state_is_not_found(self):
        self.assertEqual(self.client.get('/router/list/?state=nonsense').status_code, 404)

    def test_backup_cards_open_the_last_24_hours(self):
        status = self.client.get('/status/').context
        response = self.client.get('/backup/backup_list/?since=24h')
        self.assertEqual(len(response.context['backup_list']), status['success_backup_last_24h'])
        self.assertContains(response, 'the last 24 hours')
        errors = self.client.get('/backup/backup_list/?type=errors&since=24h')
        self.assertEqual(len(errors.context['backup_list']), status['error_backup_last_24h'])
        self.assertEqual(len(self.client.get('/backup/backup_list/?type=errors').context['backup_list']), 2)

    def test_the_cards_link_where_they_should(self):
        page = self.client.get('/status/').content.decode()
        for href in ('/router/list/?state=backup_failed', '/router/list/?state=offline', '/backup/backup_list/?type=queue',
                     '/backup/backup_list/?since=24h', '/router/list/?state=unmonitored', '/router/list/?state=online',
                     '/router/list/?state=disabled', '/router/list/?state=no_information'):
            self.assertRegex(page, f'<a href="{re.escape(href)}" class="rf-card ')

    def test_a_day_of_the_chart_opens_that_days_backups(self):
        from django.utils import timezone
        from backup_data.models import RouterBackup
        today = timezone.localdate().isoformat()
        response = self.client.get(f'/backup/backup_list/?type=errors&date={today}')
        self.assertEqual(len(response.context['backup_list']), 1)
        self.assertContains(response, f'Showing only: <strong>{today}</strong>')
        self.assertEqual(len(self.client.get('/backup/backup_list/?type=errors&date=2000-01-01').context['backup_list']), 0)
        self.assertEqual(self.client.get('/backup/backup_list/?type=errors&date=nonsense').status_code, 200)
        data = self.client.get('/backup_statistics_data/?days=7').json()
        self.assertEqual(data['dates'][-1], today, 'calendar days, today last')
        self.assertEqual(len(data['dates']), 7)


class SchedulerCardTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(10))

    def card(self):
        cards = self.client.get('/status/').context['cards']
        return next(card for card in cards if card['title'] == 'Scheduler & monitoring')

    def test_stopped_until_both_have_run_recently(self):
        from django.utils import timezone
        from routerfleet_tools.models import WebadminSettings
        self.assertEqual((self.card()['state'], self.card()['value']), ('bad', 'Stopped'))
        now = timezone.now()
        WebadminSettings.objects.update_or_create(name='webadmin_settings', defaults={
            'cron_last_run': now, 'monitoring_last_run': now})
        card = self.card()
        self.assertEqual((card['state'], card['value']), ('ok', 'Running'))
        WebadminSettings.objects.update(cron_last_run=now - timezone.timedelta(minutes=6))
        card = self.card()
        self.assertEqual(card['state'], 'bad')
        self.assertIn('scheduler 6 min ago', card['note'])
