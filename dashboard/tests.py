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
        # Credited on the About page; the bottom bar is the status bar (live state, version)
        status = self.client.get('/status/').content.decode()
        self.assertNotIn('RouterFleet Improved by Alessio Bravi', status)
        self.assertIn('id="rf-sb-live"', status)

    def test_the_home_page_is_the_status_page(self):
        self.assertRedirects(self.client.get('/'), '/status/', fetch_redirect_response=False)
        menu = self.client.get('/status/').content.decode()
        self.assertIn('href="/about/"', menu)
        # About is the last entry of the menu
        self.assertGreater(menu.index('<a href="/about/" class="nav-link '), menu.index('href="/audit_log/login_history/"'))

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
        self.assertEqual((cards['Failed backups \u00b7 24h']['state'], cards['Failed backups \u00b7 24h']['chip']), ('bad', 'Act now'))
        self.assertEqual(cards['Unmonitored devices']['state'], 'warn')  # yellow: worth a look
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
        from monitoring.models import RouterDownTime
        from router_manager.models import Router
        router = make_router(status_online=False)
        ten_days_ago = timezone.now() - datetime.timedelta(days=10)
        Router.objects.filter(pk=router.pk).update(created=ten_days_ago, monitoring=True)
        RouterDownTime.objects.create(router=router, start_time=ten_days_ago)
        data = self.client.get('/router_status_data/?days=5').json()
        self.assertEqual(data['offline_data'], [1] * 5)
        self.assertEqual(data['online_data'], [0] * 5)

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
        self.assertLess(entry, menu.index('<a href="/about/" class="nav-link '))


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
        self.assertContains(response, f'Showing only: <b>{today}</b>')
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


class OneActiveMenuEntryTests(TestCase):
    """Reported: the Fleet Commander page of a router lit up Router Manager too,
    because its address contains /router/. The section is the start of the
    address, and exactly one menu entry is active."""

    def test_exactly_one_entry_is_active(self):
        import re
        from router_manager.models import Router
        self.client.force_login(make_user(50))
        router = Router.objects.create(name='r', address='192.0.2.1', router_type='routeros')
        pages = {f'/fleet_commander/router/execute/?uuid={router.uuid}': '/fleet_commander/',
                 '/router/list/': '/router/list/', '/fleet_commander/': '/fleet_commander/',
                 '/tools/backup_cleanup/': '/tools/', '/status/': '/status/'}
        for path, entry in pages.items():
            with self.subTest(path=path):
                page = self.client.get(path).content.decode()
                active = re.findall(r'<a href="([^"]+)"\s+class="nav-link active', page)
                self.assertEqual(active, [entry])


class DevicesStatusChartTests(TestCase):
    """The devices chart counts disabled devices too, from the day the change
    log says they were switched off."""

    def test_disabled_devices_from_the_day_they_were_switched_off(self):
        from dashboard.views import online_series
        from router_manager.models import Router
        router = make_router(name='old-ap')
        Router.objects.filter(pk=router.pk).update(created=timezone.now() - timezone.timedelta(days=30), monitoring=True)
        router.refresh_from_db()
        router.enabled = False
        router.save()
        router.refresh_from_db()
        self.assertIsNotNone(router.enabled_changed_at, 'saving records when it was switched off')
        Router.objects.filter(pk=router.pk).update(enabled_changed_at=timezone.now() - timezone.timedelta(days=2))
        data = online_series(7)
        self.assertEqual(data['disabled_data'][:4], [0, 0, 0, 0], 'not before it was switched off')
        self.assertEqual(data['online_data'][:4], [1, 1, 1, 1], 'it was online before')
        self.assertEqual(data['disabled_data'][-3:], [1, 1, 1])
        self.assertEqual(data['online_data'][-3:], [0, 0, 0])

    def test_each_device_counted_once_and_from_the_day_it_was_added(self):
        from monitoring.models import RouterDownTime
        from dashboard.views import online_series
        from router_manager.models import Router
        now = timezone.now()
        # Anchored to the calendar days, not to the time the test runs at
        midnight = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
        old = make_router(name='a')
        new = make_router(name='b')
        Router.objects.filter(pk=old.pk).update(created=now - timezone.timedelta(days=30), monitoring=True)
        Router.objects.filter(pk=new.pk).update(created=min(now, midnight + timezone.timedelta(seconds=1)),
                                                monitoring=True)
        # 'a' went offline at noon four days ago and was back at noon the next day
        RouterDownTime.objects.create(router=old, start_time=midnight - timezone.timedelta(days=4) + timezone.timedelta(hours=12),
                                      end_time=midnight - timezone.timedelta(days=3) + timezone.timedelta(hours=12))
        data = online_series(7)
        totals = [a + b + c for a, b, c in zip(data['online_data'], data['offline_data'], data['disabled_data'])]
        self.assertEqual(totals, [1, 1, 1, 1, 1, 1, 2])
        self.assertEqual(data['offline_data'][-4:], [0, 0, 0, 0])
        self.assertEqual(sum(data['offline_data']), 1, 'offline only on the day it ended down')

    def test_unmonitored_enabled_devices_have_no_status(self):
        from dashboard.views import online_series
        from router_manager.models import Router
        router = make_router(name='quiet')
        Router.objects.filter(pk=router.pk).update(monitoring=False)
        data = online_series(7)
        self.assertEqual(data['online_data'][-1] + data['offline_data'][-1] + data['disabled_data'][-1], 0)

    def test_the_chart_is_named_devices_status(self):
        self.client.force_login(make_user(10))
        page = self.client.get('/status/')
        self.assertContains(page, 'Devices status')
        self.assertContains(page, '/static/routerfleet/status.js')
        import pathlib
        script = pathlib.Path(__file__).resolve().parent.parent / 'static_files/routerfleet/status.js'
        self.assertIn("label: 'Disabled'", script.read_text())


class MenuIconAlignmentTests(TestCase):
    """Every menu entry's icon has the fixed width (nav-icon) that lines the
    labels up - Backup's lacked it and its label sat further left."""

    def test_every_menu_icon_is_aligned(self):
        import re
        self.client.force_login(make_user(50))
        page = self.client.get('/status/').content.decode()
        sidebar = page[page.index('sidebar-menu'):page.index('</nav>', page.index('sidebar-menu'))]
        icons = re.findall(r'<a href="[^"]+"\s+class="nav-link[^"]*">\s*<i class="([^"]+)"', sidebar)
        self.assertGreater(len(icons), 8)
        for classes in icons:
            self.assertIn('nav-icon', classes.split(), classes)



class ThemeTests(TestCase):
    """Light and dark: applied before the page is drawn, switchable from the
    top bar and My Settings."""

    def test_every_page_carries_the_theme(self):
        self.client.force_login(make_user(10))
        page = self.client.get('/status/').content.decode()
        # A static script in <head>, before the stylesheets: no flash, no inline script
        boot = page.index('/static/routerfleet/theme-boot.js')
        self.assertLess(boot, page.index('<body'), 'set before drawing')
        self.assertLess(boot, page.index('adminlte.min.css'))
        self.assertIn('id="theme-toggle"', page)
        self.assertIn('/static/routerfleet/theme.js', page)
        settings_page = self.client.get('/user/settings/').content.decode()
        for choice in ('auto', 'light', 'dark'):
            self.assertIn(f'name="rf-theme" value="{choice}"', settings_page)


class StateIconTests(TestCase):
    """States are shown as icons: a green tick, the warning sign in yellow,
    orange or red; the word stays as the tooltip and for screen readers."""

    def test_the_icon_of_each_state(self):
        from routerfleet_tools.templatetags.custom_tags import state_icon
        self.assertIn('fa-circle-check rf-state rf-state-ok', state_icon('ok', 'OK'))
        for state in ('warn', 'high', 'bad'):
            self.assertIn(f'fa-triangle-exclamation rf-state rf-state-{state}', state_icon(state, 'x'))
        self.assertIn('aria-label="OK" title="OK"', state_icon('ok', 'OK'))
        self.assertIn('aria-hidden="true"', state_icon('run'))
        self.assertIn('rf-state-info', state_icon('<script>', 'a"b'))
        self.assertIn('aria-label="a&quot;b"', state_icon('info', 'a"b'))

    def test_storage_gets_more_serious_in_steps(self):
        from unittest import mock
        from dashboard import views
        for pct, state in ((10, 'ok'), (76, 'warn'), (86, 'high'), (95, 'bad')):
            with mock.patch.object(views, 'get_directory_statistics', return_value={
                    'usage_percentage': pct, 'used': '1', 'total': '2', 'storage_warning': ''}):
                card = next(c for c in views.status_cards() if c['title'] == 'Backup storage')
            self.assertEqual(card['state'], state, pct)

    def test_the_status_page_shows_icons_not_words(self):
        from routerfleet.testing import make_user
        self.client.force_login(make_user(50))
        page = self.client.get('/status/').content.decode()
        self.assertIn('rf-state-ok', page)
        self.assertNotIn('class="rf-chip ', page)


class StatusBarStylesTests(TestCase):
    """The status bar's styles touch the status bar only: none of its class
    names may be one a page already uses (0.38.2 styled the status page's own
    wrapper, rf-status, and broke its layout)."""

    def test_the_status_bar_classes_belong_to_it_alone(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        css = (root / 'static_files/routerfleet/app.css').read_text()
        block = css[css.index('/* The status bar (the footer)'):]
        names = set(re.findall(r'\.(rf-[a-z0-9-]+)', block.split('\n\n')[0] if '\n\n' in block else block))
        names -= {'rf-dot', 'rf-label', 'rf-live', 'rf-paused'}   # only ever inside the bar here
        for template in (root / 'templates').rglob('*.html'):
            if template.name == 'base.html':
                continue
            used = set(re.findall(r'class="([^"]*)"', template.read_text()))
            classes = {c for value in used for c in value.split()}
            with self.subTest(template=str(template.relative_to(root))):
                self.assertFalse(names & classes, f"{names & classes} belongs to the status bar")


class NoStoreTests(TestCase):
    """A page shown to a signed-in user is not stored by the browser."""

    def test_signed_in_pages_are_no_store(self):
        from routerfleet.testing import make_user
        self.client.force_login(make_user(50))
        self.assertIn('no-store', self.client.get('/status/')['Cache-Control'])
        self.assertIn('no-store', self.client.get('/user/settings/')['Cache-Control'])


class ChangesStampTests(TestCase):
    """/changes/: when the data the pages show last changed, so an open page
    refreshes itself after a change made elsewhere."""

    def test_the_stamp_moves_when_a_device_is_read_again(self):
        from routerfleet.testing import make_router, make_user
        from router_manager.models import RouterInformation
        router = make_router(name='stamped')
        self.client.force_login(make_user(10))
        first = self.client.get('/changes/')
        self.assertEqual(first['Cache-Control'], 'no-store')
        before = first.json()['stamp']
        self.assertTrue(before)
        info, _ = RouterInformation.objects.get_or_create(router=router)
        info.os_version = '7.25.2'
        info.save()
        self.assertGreater(self.client.get('/changes/').json()['stamp'], before)

    def test_signed_in_only(self):
        self.assertEqual(self.client.get('/changes/').status_code, 302)
