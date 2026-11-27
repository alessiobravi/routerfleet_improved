"""The installation's own health on every page: a stopped scheduler or
monitoring, a filling backup disk - in the status bar, and on the status page."""
import datetime
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from routerfleet.testing import make_user
from routerfleet_tools import health
from routerfleet_tools.models import WebadminSettings

GB = 1024 ** 3


def disk_usage(percent, total=100 * GB):
    used = total * percent // 100
    return mock.patch('routerfleet_tools.health.shutil.disk_usage', return_value=(total, used, total - used))


class HealthTestCase(TestCase):
    def setUp(self):
        cache.delete('rf-system-warnings')
        self.addCleanup(cache.delete, 'rf-system-warnings')
        self.webadmin = WebadminSettings.objects.create(name='webadmin_settings')

    def alive(self, cron_minutes=1, monitoring_minutes=1):
        now = timezone.now()
        WebadminSettings.objects.filter(pk=self.webadmin.pk).update(
            cron_last_run=now - datetime.timedelta(minutes=cron_minutes),
            monitoring_last_run=now - datetime.timedelta(minutes=monitoring_minutes))


class SchedulerTests(HealthTestCase):
    def test_running_when_both_called_in_lately(self):
        self.alive()
        self.assertEqual(health.scheduler()['stopped'], [])

    def test_stopped_when_late_or_never_seen(self):
        self.assertEqual(health.scheduler()['stopped'], ['scheduler', 'monitoring'], 'never called in')
        self.alive(cron_minutes=6)
        self.assertEqual(health.scheduler()['stopped'], ['scheduler'])
        self.alive(monitoring_minutes=11)
        self.assertEqual(health.scheduler()['stopped'], ['monitoring'])


class PaceTests(TestCase):
    def test_bytes_a_day_from_the_first_and_last_sample(self):
        self.assertEqual(health.pace([['2026-11-01', 100], ['2026-11-05', 500]]), 100)

    def test_unknown_with_too_few_days_or_when_not_filling(self):
        self.assertIsNone(health.pace([]))
        self.assertIsNone(health.pace([['2026-11-01', 100]]))
        self.assertIsNone(health.pace([['2026-11-01', 100], ['2026-11-02', 900]]), 'one day says little')
        self.assertIsNone(health.pace([['2026-11-01', 900], ['2026-11-10', 100]]), 'shrinking')
        self.assertIsNone(health.pace([['bad', 'data'], ['2026-11-10', 100]]))


class SampleTests(HealthTestCase):
    def test_one_sample_a_day_the_last_thirty_kept(self):
        start = datetime.date(2026, 10, 1)
        with disk_usage(50):
            for day in range(35):
                health.record_sample(start + datetime.timedelta(days=day))
            self.assertFalse(health.record_sample(start + datetime.timedelta(days=34)), 'once a day')
        samples = WebadminSettings.objects.get().disk_samples
        self.assertEqual(len(samples), 30)
        self.assertEqual(samples[-1][0], '2026-11-04')

    def test_the_housekeeping_run_takes_it(self):
        with disk_usage(50):
            self.client.get('/cron/housekeeping/')
        self.assertEqual(len(WebadminSettings.objects.get().disk_samples), 1)

    def test_days_left_at_the_current_pace(self):
        WebadminSettings.objects.filter(pk=self.webadmin.pk).update(
            disk_samples=[['2026-11-01', 40 * GB], ['2026-11-11', 50 * GB]])   # 1 GB a day
        with disk_usage(50):
            space = health.disk()
        self.assertEqual((space['per_day'], space['days_left']), (GB, 50))


class WarningTests(HealthTestCase):
    def test_nothing_when_all_is_well(self):
        self.alive()
        with disk_usage(40):
            self.assertEqual(health.warnings(), [])

    def test_a_stopped_scheduler(self):
        self.alive(cron_minutes=30)
        with disk_usage(40):
            [warning] = health.warnings()
        self.assertEqual((warning['level'], warning['text']), ('bad', 'Scheduler stopped'))

    def test_the_disk_in_steps(self):
        self.alive()
        for percent, level in ((79, None), (80, 'warn'), (90, 'bad')):
            with disk_usage(percent):
                found = health.warnings()
            self.assertEqual(found[0]['level'] if found else None, level, percent)

    def test_the_days_left_raise_it(self):
        self.alive()
        WebadminSettings.objects.filter(pk=self.webadmin.pk).update(
            disk_samples=[['2026-11-01', 0], ['2026-11-11', 50 * GB]])          # 5 GB a day
        with disk_usage(50):                                                    # 50 GB free: 10 days
            [warning] = health.warnings()
        self.assertEqual(warning['level'], 'warn')
        self.assertIn('full in ~10 days', warning['text'])


class StatusBarTests(HealthTestCase):
    def test_every_page_shows_the_warning(self):
        self.client.force_login(make_user(10))
        with disk_usage(40):
            for url in ('/status/', '/router/list/', '/backup/backup_list/'):
                self.assertContains(self.client.get(url), 'Scheduler and monitoring stopped')

    def test_nothing_shown_when_all_is_well(self):
        self.alive()
        self.client.force_login(make_user(10))
        with disk_usage(40):
            self.assertNotContains(self.client.get('/router/list/'), 'rf-sb-warn ')

    def test_the_status_page_card_shows_the_disk(self):
        self.alive()
        self.client.force_login(make_user(10))
        with disk_usage(85):
            page = self.client.get('/status/').content.decode()
        self.assertIn('Backup disk', page)
        self.assertIn('85%', page)
        self.assertIn('how fast it fills is known after a few days', page)
