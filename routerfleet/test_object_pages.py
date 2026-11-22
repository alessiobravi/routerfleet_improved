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


class TimelineOrderTests(TestCase):
    def test_events_of_the_same_moment_keep_their_order_newest_first(self):
        from routerfleet.object_pages import _newest
        moment = timezone.now()
        events = [{'when': moment, 'text': 'Queued'}, {'when': moment, 'text': 'Started'},
                  {'when': moment, 'text': 'Succeeded'}]
        self.assertEqual([e['text'] for e in _newest(events)], ['Succeeded', 'Started', 'Queued'])


class TaskAndBackupPageTests(TestCase):
    def test_task_page_states(self):
        from routerfleet.object_pages import task_page
        command = Command.objects.create(name='Show Version')
        CommandVariant.objects.create(command=command, router_type='routeros', payload='/x')
        job = create_manual_job(command, routers=Router.objects.filter(pk=make_router().pk))
        task = job.tasks.get()
        self.assertEqual(task_page(task)['hero']['chip'], ('wait', 'Waiting'))
        task.started_at = timezone.now()
        task.payload_executed, task.verify_deadline = True, timezone.now()
        self.assertEqual(task_page(task)['hero']['chip'], ('run', 'Verifying'))
        task.status, task.finished_at, task.verified = 'success', timezone.now(), True
        page = task_page(task)
        self.assertEqual(page['hero']['chip'], ('ok', 'Succeeded'))
        self.assertEqual(page['tiles'][3]['value'], 'verified')
        self.assertEqual(page['timeline'][0]['text'], 'Succeeded')
        task.status = 'error'
        self.assertEqual(task_page(task)['hero']['chip'], ('bad', 'Failed'))
        task.status = 'aborted'
        self.assertEqual(task_page(task)['hero']['icon'], 'fa-hand-paper')

    def test_backup_page_phases_and_console_timeline(self):
        from routerfleet.object_pages import backup_page
        router = make_router(name='edge-1')
        backup = RouterBackup.objects.create(router=router, schedule_type='daily')
        self.assertEqual(backup_page(backup)['hero']['chip'], ('wait', 'Queued'))
        stamp = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')
        backup.task_console_output = (f'[{stamp}] $ /system backup save name=x\n[{stamp}] [exit_code=0]\n'
                                      f'[{stamp}] === Retrieving backup files ===\nnot a log line\n')
        self.assertEqual(backup_page(backup)['hero']['chip'], ('run', 'Creating'))
        backup.backup_pending_retrieval = True
        self.assertEqual(backup_page(backup)['hero']['ring'], 66)
        texts = [e['text'] for e in backup_page(backup)['timeline']]
        self.assertIn('Sent: /system backup save name=x', texts)
        self.assertIn('Retrieving backup files', texts)
        self.assertFalse(any('exit_code' in t for t in texts), 'only the steps, not every line')
        backup.backup_pending_retrieval, backup.success, backup.finish_time = False, True, timezone.now()
        backup.backup_text = 'a\nb'
        page = backup_page(backup)
        self.assertEqual((page['hero']['chip'], page['hero']['ring']), (('ok', 'Stored'), 100))
        self.assertEqual(page['tiles'][0]['value'], '2 lines')
        backup.success, backup.error = False, True
        self.assertEqual(backup_page(backup)['hero']['chip'], ('bad', 'Failed'))

    def test_device_page_versions_downtime_and_console_events(self):
        from console.models import ConsoleSession
        from monitoring.models import RouterDownTime
        from router_manager.models import RouterInformation
        router = make_router()
        status = RouterStatus.objects.get(router=router)
        info = RouterInformation.objects.create(router=router, os_version='7.16', available_version='7.17')
        RouterDownTime.objects.create(router=router, start_time=timezone.now() - timezone.timedelta(hours=2),
                                      end_time=timezone.now() - timezone.timedelta(hours=1), total_down_time=3600)
        session, _ = ConsoleSession.open(make_user(30), router, 'ssh', 'backup', 'admin')
        ConsoleSession.objects.filter(pk=session.pk).update(connected_at=timezone.now(), ended_at=timezone.now(),
                                                            end_reason='the browser closed the console')
        page = router_page(router, status, info, 95, can_execute=False, can_console=True)
        self.assertEqual(page['tiles'][2]['value'], 'Update waiting')
        self.assertEqual(page['tiles'][3]['value'], '1h 0m')
        texts = [e['text'] for e in page['timeline']]
        self.assertTrue(any(t.startswith('Console opened by') for t in texts))
        self.assertIn('Went offline', texts)
        self.assertIn('Console', [a['label'] for a in page['hero']['actions']])
