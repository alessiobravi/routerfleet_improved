import datetime
import io

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from fleet_commander.forms import CommandScheduleForm, CommandVariantForm
from fleet_commander.models import (Command, CommandJob, CommandSchedule, CommandTask, CommandVariant,
                                    ScheduleDefaults)
from router_manager.models import RouterStatus
from routerfleet.testing import make_group, make_router, make_user


class CommandViewTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(40))

    def test_default_commands_are_seeded_once(self):
        self.client.get('/fleet_commander/')
        names = set(Command.objects.values_list('name', flat=True))
        self.assertEqual(names, {'Show Version', 'Update Router Firmware'})
        update = Command.objects.get(name='Update Router Firmware')
        self.assertEqual(update.verify_timeout, 600)
        variant = update.variants.get(router_type='routeros')
        self.assertIn('expected-version=', variant.payload)
        self.assertEqual(variant.verify_expect, 'installed-version: {{ expected_version }}')
        self.client.get('/fleet_commander/')
        self.assertEqual(Command.objects.count(), 2)

    def test_command_in_use_cannot_be_deleted(self):
        command = Command.objects.create(name='C')
        CommandVariant.objects.create(command=command, router_type='routeros', payload='/x')
        self.client.get(f'/fleet_commander/command/manage/?uuid={command.uuid}&action=delete&confirmation=delete')
        self.assertTrue(Command.objects.filter(pk=command.pk).exists())

    def test_execute_creates_a_job(self):
        command = Command.objects.create(name='C')
        CommandVariant.objects.create(command=command, router_type='routeros', payload='/x')
        router = make_router()
        response = self.client.post(f'/fleet_commander/command/execute/?command_uuid={command.uuid}',
                                    {'routers': [router.pk]})
        job = CommandJob.objects.get()
        self.assertRedirects(response, f'/fleet_commander/job/details/?uuid={job.uuid}',
                             fetch_redirect_response=False)
        self.assertEqual(job.exec_source, 'manual')

    def test_execute_without_variants(self):
        command = Command.objects.create(name='C')
        response = self.client.get(f'/fleet_commander/command/execute/?command_uuid={command.uuid}')
        self.assertRedirects(response, f'/fleet_commander/command/details/?uuid={command.uuid}',
                             fetch_redirect_response=False)

    def test_run_on_many_through_the_session(self):
        command = Command.objects.create(name='C')
        CommandVariant.objects.create(command=command, router_type='routeros', payload='/x')
        a, b = make_router(), make_router()
        self.client.post('/fleet_commander/command/execute_multiple/', {'routers[]': [str(a.uuid), str(b.uuid)]})
        self.assertEqual(len(self.client.session['router_selection']), 2)
        self.assertEqual(self.client.get('/fleet_commander/command/execute_multiple/').status_code, 200)
        self.client.post('/fleet_commander/command/execute_multiple/',
                         {'router_uuids': [str(a.uuid), str(b.uuid)], 'command': str(command.uuid)})
        self.assertEqual(CommandJob.objects.get().tasks.count(), 2)
        self.assertNotIn('router_selection', self.client.session)


class VariantFormTests(TestCase):
    def setUp(self):
        self.command = Command.objects.create(name='C')

    def test_one_variant_per_type(self):
        CommandVariant.objects.create(command=self.command, router_type='routeros', payload='/x')
        form = CommandVariantForm({'router_type': 'routeros', 'payload': '/y', 'enabled': True},
                                  command=self.command)
        self.assertFalse(form.is_valid())
        self.assertIn('router_type', form.errors)

    def test_verification_needs_both_halves(self):
        form = CommandVariantForm({'router_type': 'openwrt', 'payload': '/y', 'verify_payload': '/v'},
                                  command=self.command)
        self.assertFalse(form.is_valid())
        self.assertIn('verify_expect', form.errors)


class ScheduleViewTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(40))
        self.command = Command.objects.create(name='C')
        CommandVariant.objects.create(command=self.command, router_type='routeros', payload='/x')
        self.router = make_router()

    def form(self, data, instance=None):
        return CommandScheduleForm(data, instance=instance, command=self.command)

    def test_new_schedule_starts_from_the_defaults(self):
        defaults = ScheduleDefaults.load()
        defaults.repeat_interval = 120
        defaults.save()
        form = CommandScheduleForm(command=self.command)
        self.assertEqual(form.fields['repeat_interval'].initial, '2h')
        self.assertGreater(form.fields['start_at'].initial, timezone.now())

    def test_existing_schedule_shows_what_it_was_saved_with(self):
        schedule = CommandSchedule.objects.create(command=self.command, repeat_interval=0)
        self.assertEqual(CommandScheduleForm(instance=schedule, command=self.command)
                         .fields['repeat_interval'].initial, 'One time')

    def test_needs_a_target_and_refuses_all_excluded(self):
        start = (timezone.now() + datetime.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
        self.assertFalse(self.form({'start_at': start, 'repeat_interval': '1d'}).is_valid())
        group = make_group(routers=[self.router])
        form = self.form({'start_at': start, 'repeat_interval': '1d', 'router_group': [group.pk],
                          'exclude_router': [self.router.pk]})
        self.assertFalse(form.is_valid())
        self.assertIn('Every device of this schedule is excluded', str(form.errors))

    def test_interval_is_validated(self):
        start = (timezone.now() + datetime.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
        form = self.form({'start_at': start, 'repeat_interval': '5x', 'router': [self.router.pk]})
        self.assertIn('repeat_interval', form.errors)

    def test_save_sets_next_run(self):
        start = timezone.localtime() + datetime.timedelta(hours=1)
        response = self.client.post(f'/fleet_commander/schedule/manage/?command_uuid={self.command.uuid}',
                                    {'start_at': start.strftime('%Y-%m-%dT%H:%M'), 'repeat_interval': '1d',
                                     'router': [self.router.pk], 'enabled': 'on'})
        self.assertEqual(response.status_code, 302)
        schedule = CommandSchedule.objects.get()
        self.assertEqual(schedule.repeat_interval, 1440)
        self.assertIsNotNone(schedule.next_run)

    def test_switching_off_stops_dispatched_work(self):
        schedule = CommandSchedule.objects.create(command=self.command, enabled=True, repeat_interval=1440,
                                                  start_at=timezone.now())
        schedule.router.add(self.router)
        job = CommandJob.objects.create(command=self.command, schedule=schedule)
        task = CommandTask.objects.create(job=job, router=self.router)
        start = timezone.localtime(schedule.start_at).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(f'/fleet_commander/schedule/manage/?uuid={schedule.uuid}',
                                    {'start_at': start, 'repeat_interval': '1d', 'router': [self.router.pk]},
                                    follow=True)
        task.refresh_from_db()
        self.assertEqual(task.status, 'aborted')
        self.assertIn('Schedule switched off by', task.error_message)
        self.assertIn('1 task it had started was stopped', str(list(response.context['messages'])[0]))

    def test_delete_stops_dispatched_work(self):
        schedule = CommandSchedule.objects.create(command=self.command)
        job = CommandJob.objects.create(command=self.command, schedule=schedule)
        task = CommandTask.objects.create(job=job, router=self.router)
        self.client.get(f'/fleet_commander/schedule/manage/?uuid={schedule.uuid}&action=delete&confirmation=delete')
        task.refresh_from_db()
        self.assertIn('Schedule deleted by', task.error_message)
        self.assertFalse(CommandSchedule.objects.exists())


class AbortViewTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(20))
        command = Command.objects.create(name='C')
        self.job = CommandJob.objects.create(command=command)
        self.task = CommandTask.objects.create(job=self.job, router=make_router())

    def test_task_abort_needs_the_word(self):
        self.client.get(f'/fleet_commander/task/abort/?uuid={self.task.uuid}&confirmation=stop')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'pending')
        self.client.get(f'/fleet_commander/task/abort/?uuid={self.task.uuid}&confirmation=abort')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'aborted')

    def test_job_abort(self):
        self.client.get(f'/fleet_commander/job/abort/?uuid={self.job.uuid}&confirmation=abort')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'aborted')
        response = self.client.get(f'/fleet_commander/job/abort/?uuid={self.job.uuid}&confirmation=abort',
                                   follow=True)
        self.assertIn('no pending tasks left', ' '.join(str(m) for m in response.context['messages']))


class PerformTasksCronTests(TestCase):
    def setUp(self):
        self.command = Command.objects.create(name='C')

    def pending(self, router, **job_fields):
        job = CommandJob.objects.create(command=self.command, **job_fields)
        return CommandTask.objects.create(job=job, router=router)

    def test_skips_busy_devices_future_retries_and_switched_off_schedules(self):
        from unittest import mock
        free = make_router()
        backing_up = make_router()
        RouterStatus.objects.filter(router=backing_up).update(backup_lock=timezone.now())
        commanded = make_router()
        RouterStatus.objects.filter(router=commanded).update(command_lock=timezone.now())
        later = make_router()
        off_schedule = CommandSchedule.objects.create(command=self.command, enabled=False)

        due = self.pending(free)
        self.pending(backing_up)
        self.pending(commanded)
        CommandTask.objects.filter(pk=self.pending(later).pk).update(
            next_retry=timezone.now() + datetime.timedelta(hours=1))
        self.pending(make_router(), schedule=off_schedule)

        with mock.patch('fleet_commander.views.execute_command_task') as execute:
            data = self.client.get('/cron/perform_command_tasks/').json()
        self.assertEqual(data['tasks_performed'], 1)
        self.assertEqual(execute.call_args.args[0].pk, due.pk)


class ScheduleDefaultsCommandTests(TestCase):
    def call(self, *args):
        out = io.StringIO()
        call_command('schedule_defaults', *args, stdout=out)
        return out.getvalue()

    def test_show(self):
        out = self.call()
        self.assertIn('Start time:      03:00', out)
        self.assertIn('Repeat interval: 7d', out)

    def test_set_and_reset(self):
        self.assertIn('saved', self.call('--start-time', '22:30', '--repeat', '24h'))
        defaults = ScheduleDefaults.load()
        self.assertEqual((defaults.start_time, defaults.repeat_interval), (datetime.time(22, 30), 1440))
        self.call('--repeat', '0')
        self.assertEqual(ScheduleDefaults.load().repeat_interval, 0)
        self.call('--reset')
        defaults = ScheduleDefaults.load()
        self.assertEqual((defaults.start_time, defaults.repeat_interval), (datetime.time(3, 0), 10080))

    def test_bad_values(self):
        with self.assertRaises(CommandError):
            self.call('--start-time', '25:99')
        with self.assertRaises(CommandError):
            self.call('--repeat', 'weekly')

    def test_defaults_page(self):
        self.client.force_login(make_user(40))
        self.client.post('/fleet_commander/schedule/defaults/', {'start_time': '04:15', 'repeat_interval': '30m'})
        defaults = ScheduleDefaults.load()
        self.assertEqual((defaults.start_time, defaults.repeat_interval), (datetime.time(4, 15), 30))
