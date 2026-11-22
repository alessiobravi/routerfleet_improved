"""The "Hero & timeline" object pages."""
from django.test import TestCase
from django.utils import timezone

from backup_data.models import RouterBackup
from fleet_commander.command_functions import create_manual_job
from fleet_commander.models import Command, CommandTask, CommandVariant
from router_manager.models import Router, RouterStatus
from routerfleet.object_pages import job_page, router_page
from routerfleet.testing import make_router, make_user


class JobPageTests(TestCase):
    def setUp(self):
        command = Command.objects.create(name='Show Version')
        CommandVariant.objects.create(command=command, router_type='routeros', payload='/x')
        routers = [make_router(name=f'r{i}') for i in range(4)]
        self.job = create_manual_job(command, routers=Router.objects.filter(pk__in=[r.pk for r in routers]))
        tasks = list(self.job.tasks.order_by('id'))
        now = timezone.now()
        CommandTask.objects.filter(pk=tasks[0].pk).update(status='success', started_at=now, finished_at=now)
        CommandTask.objects.filter(pk=tasks[1].pk).update(status='error', started_at=now, finished_at=now, error_message='timed out')
        CommandTask.objects.filter(pk=tasks[2].pk).update(started_at=now)
        self.job.update_counters()

    def test_ring_tiles_and_timeline(self):
        page = job_page(self.job, list(self.job.tasks.all()))
        self.assertEqual(page['hero']['ring'], 50)
        self.assertEqual(page['hero']['chip'], ('run', 'Running'))
        self.assertEqual([t['value'] for t in page['tiles']], [1, 1, 1, 1])
        texts = [e['text'] for e in page['timeline']]
        self.assertTrue(any('failed: timed out' in t for t in texts))
        self.assertTrue(any(t.startswith('Job created') for t in texts))

    def test_the_page_renders_the_hero(self):
        self.client.force_login(make_user(20))
        page = self.client.get(f'/fleet_commander/job/details/?uuid={self.job.uuid}')
        self.assertContains(page, 'rf-o-hero')
        self.assertContains(page, '--rf-pct: 50;')
        self.assertContains(page, 'Timeline')


class RouterPageTests(TestCase):
    def test_states_and_last_backup(self):
        router = make_router()
        status = RouterStatus.objects.get(router=router)
        RouterBackup.objects.create(router=router, schedule_type='daily', success=True, finish_time=timezone.now())
        page = router_page(router, status, None, 99.5, can_execute=True)
        self.assertEqual(page['tiles'][0]['value'], 'just now', 'from the latest successful backup')
        self.assertIn('Execute command', [a['label'] for a in page['hero']['actions']])
        router.enabled = False
        self.assertEqual(router_page(router, status, None, 99.5, False)['hero']['chip'], ('purple', 'Disabled'))

    def test_the_device_page_renders(self):
        self.client.force_login(make_user(10))
        router = make_router()
        self.assertContains(self.client.get(f'/router/details/?uuid={router.uuid}'), 'rf-o-hero')
