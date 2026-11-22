import datetime
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from fleet_commander import command_functions as cf
from fleet_commander.models import (Command, CommandJob, CommandSchedule, CommandTask,
                                    CommandVariant)
from router_manager.models import RouterInformation, RouterStatus
from routerfleet.testing import FakeSSHClient, fake_ssh, make_group, make_information, make_router

SSH = 'fleet_commander.command_functions'


class PlaceholderTests(SimpleTestCase):
    def variant(self, expect):
        return CommandVariant(verify_payload='x', verify_expect=expect)

    def test_expectations_are_resolved(self):
        variant = self.variant('installed-version: {{ expected_version }}\n\n  plain line  \n')
        self.assertEqual(cf.build_verification_expectations(variant, {'expected_version': '7.17'}), [
            ('installed-version: {{ expected_version }}', 'installed-version: 7.17'),
            ('plain line', 'plain line'),
        ])

    def test_unknown_placeholder_is_left_as_written(self):
        self.assertEqual(cf.build_verification_expectations(self.variant('{{ nope }}'), {}),
                         [('{{ nope }}', '{{ nope }}')])

    def test_missing_values(self):
        variant = self.variant('{{ current_version }} {{ available_version }}\n{{ current_version }}')
        self.assertEqual(cf.missing_placeholder_values(variant, {'current_version': '', 'available_version': '1'}),
                         ['current_version'])

    def test_expected_version_is_read_from_output(self):
        self.assertEqual(cf.extract_expected_version('noise\nexpected-version=7.17.1\nmore'), '7.17.1')
        self.assertEqual(cf.extract_expected_version('nothing here'), '')
        self.assertEqual(cf.extract_expected_version(None), '')

    def test_expectation_is_a_regex_or_falls_back_to_text(self):
        self.assertTrue(cf.expectation_matches(r'version: 7\.1[67]\..*', 'version: 7.17.2'))
        self.assertFalse(cf.expectation_matches(r'version: 7\.1[67]\..*', 'version: 7.15.0'))
        # Not a valid regular expression: compared as plain text
        self.assertTrue(cf.expectation_matches('[unclosed', 'has [unclosed bracket'))


class ExecutionFixture(TestCase):
    def setUp(self):
        self.router = make_router(router_type='routeros')
        self.command = Command.objects.create(name='Update', max_retry=3, retry_interval=30,
                                              verify_timeout=300, verify_interval=30)
        self.variant = CommandVariant.objects.create(command=self.command, router_type='routeros',
                                                     payload='/one\n\n/two')
        self.job = CommandJob.objects.create(command=self.command)
        self.task = CommandTask.objects.create(job=self.job, router=self.router,
                                               command_variant=self.variant)

    def run_task(self, client):
        with fake_ssh(SSH, client):
            cf.execute_command_task(self.task)
        self.task.refresh_from_db()
        return self.task

    def add_verification(self, expect='installed-version: {{ expected_version }}'):
        self.variant.verify_payload = '/verify'
        self.variant.verify_expect = expect
        self.variant.save()


class PlainExecutionTests(ExecutionFixture):
    def test_success(self):
        client = FakeSSHClient({'/one': 'out one', '/two': 'out two'})
        task = self.run_task(client)
        self.assertEqual(task.status, 'success')
        self.assertEqual(client.commands, ['/one', '/two'])
        self.assertEqual(task.command_executed, '/one\n/two')
        self.assertEqual(task.retry_count, 1)
        self.assertIsNotNone(task.finished_at)
        # Output only kept when asked for
        self.assertIsNone(task.command_output)
        self.assertTrue(client.closed)
        self.assertIsNone(RouterStatus.objects.get(router=self.router).command_lock)
        self.job.refresh_from_db()
        self.assertIsNotNone(self.job.completed)

    def test_output_is_captured_when_asked(self):
        self.command.capture_output = True
        self.command.save()
        task = self.run_task(FakeSSHClient({'/one': 'A', '/two': 'B'}))
        self.assertEqual(task.command_output, 'A\nB')

    def test_failing_line_stops_the_payload_and_schedules_a_retry(self):
        client = FakeSSHClient({'/one': (1, '', 'bad thing')})
        task = self.run_task(client)
        self.assertEqual(client.commands, ['/one'])
        self.assertEqual(task.status, 'pending')
        self.assertEqual(task.error_message, 'bad thing')
        self.assertIsNotNone(task.next_retry)

    def test_failing_line_without_stderr(self):
        task = self.run_task(FakeSSHClient({'/one': (2, '', '')}))
        self.assertEqual(task.error_message, 'Command exited with code 2')

    def test_max_retry_counts_the_first_attempt(self):
        failing = FakeSSHClient({'/one': (1, '', 'no')})
        for _ in range(3):
            task = self.run_task(failing)
        self.assertEqual(task.status, 'error')
        self.assertEqual(task.retry_count, 3)

    def test_retry_count_above_max_stops_before_connecting(self):
        self.task.retry_count = 3
        self.task.save()
        with mock.patch('routerlib.device_session.connect_to_ssh') as connect:
            cf.execute_command_task(self.task)
        connect.assert_not_called()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'error')
        self.assertEqual(self.task.error_message, 'Max retries reached')

    def test_connection_lost_mid_payload(self):
        task = self.run_task(FakeSSHClient({'/one': OSError('reset by peer')}))
        self.assertIn('Connection lost while running: /one', task.error_message)
        self.assertEqual(task.status, 'pending')

    def test_connect_failure_is_retried(self):
        with mock.patch('routerlib.device_session.connect_to_ssh', side_effect=OSError('unreachable')):
            cf.execute_command_task(self.task)
        self.task.refresh_from_db()
        self.assertEqual(self.task.error_message, 'unreachable')
        self.assertEqual(self.task.status, 'pending')
        self.assertIsNone(RouterStatus.objects.get(router=self.router).command_lock)

    def test_variant_is_looked_up_when_missing(self):
        self.task.command_variant = None
        self.task.save()
        task = self.run_task(FakeSSHClient())
        self.assertEqual(task.command_variant, self.variant)
        self.assertEqual(task.status, 'success')

    def test_no_enabled_variant_for_the_type(self):
        self.task.command_variant = None
        self.task.save()
        self.variant.enabled = False
        self.variant.save()
        task = self.run_task(FakeSSHClient())
        self.assertEqual(task.status, 'error')
        self.assertIn('No enabled variant', task.error_message)

    def test_router_that_no_longer_exists(self):
        self.router.delete()
        self.task.refresh_from_db()
        cf.execute_command_task(self.task)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'error')
        self.assertEqual(self.task.error_message, 'Router no longer exists')


class AbortTests(ExecutionFixture):
    def test_aborted_before_start_does_nothing(self):
        cf.abort_command_task(self.task)
        with mock.patch('routerlib.device_session.connect_to_ssh') as connect:
            cf.execute_command_task(self.task)
        connect.assert_not_called()

    def test_abort_during_payload_stops_the_remaining_lines(self):
        def abort_then_answer(command):
            CommandTask.objects.filter(pk=self.task.pk).update(status='aborted')
            return 'downloaded'
        client = FakeSSHClient({'/one': abort_then_answer})
        task = self.run_task(client)
        # /two - the install in real life - never went out
        self.assertEqual(client.commands, ['/one'])
        self.assertEqual(task.status, 'aborted')

    def test_only_pending_tasks_can_be_aborted(self):
        self.task.status = 'success'
        self.task.save()
        self.assertFalse(cf.abort_command_task(self.task))

    def test_abort_reason_and_user(self):
        user = mock.Mock(username='alice')
        self.assertTrue(cf.abort_command_task(self.task, user))
        self.task.refresh_from_db()
        self.assertEqual(self.task.error_message, 'Aborted by alice')
        self.assertIsNotNone(self.task.finished_at)

    def test_abort_tasks_of_schedule(self):
        schedule = CommandSchedule.objects.create(command=self.command)
        job = CommandJob.objects.create(command=self.command, schedule=schedule)
        pending = CommandTask.objects.create(job=job, router=make_router())
        done = CommandTask.objects.create(job=job, router=make_router(), status='success')
        self.assertEqual(cf.abort_tasks_of_schedule(schedule, reason='off'), 1)
        pending.refresh_from_db()
        done.refresh_from_db()
        self.assertEqual((pending.status, pending.error_message), ('aborted', 'off'))
        self.assertEqual(done.status, 'success')
        job.refresh_from_db()
        self.assertIsNotNone(job.completed)
        # A manual job is untouched
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'pending')

    def test_unsaved_schedule_aborts_nothing(self):
        self.assertEqual(cf.abort_tasks_of_schedule(CommandSchedule(command=self.command)), 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'pending')


class VerificationTests(ExecutionFixture):
    def setUp(self):
        super().setUp()
        self.add_verification()
        self.variant.payload = '/set-channel\n:put ("expected-version=" . x)\n/install'
        self.variant.save()
        make_information(self.router, os_version='7.16', available_version='7.16.3')

    def payload_client(self, verify_output='installed-version: 7.17'):
        return FakeSSHClient({
            ':put ("expected-version=" . x)': 'expected-version=7.17',
            '/install': OSError('device rebooting'),
            '/verify': verify_output,
        })

    def test_success_marks_verified_and_queues_an_inventory_refresh(self):
        task = self.run_task(self.payload_client())
        self.assertEqual(task.status, 'success')
        self.assertTrue(task.verified)
        self.assertTrue(task.payload_executed)
        self.assertEqual(task.verify_context['expected_version'], '7.17')
        self.assertEqual(task.verify_context['current_version'], '7.16')
        self.assertIn('expected-version=7.17', task.command_output)
        self.assertIn('--- attempt 1 at', task.verification_output)
        self.assertTrue(RouterInformation.objects.get(router=self.router).update_requested)

    def test_payload_runs_once_and_only_verification_is_retried(self):
        client = self.payload_client(verify_output='installed-version: 7.16')
        task = self.run_task(client)
        self.assertEqual(task.status, 'pending')
        self.assertIsNotNone(task.next_retry)
        self.assertEqual(task.retry_count, 1)
        self.assertEqual(client.commands.count('/install'), 1)

        task = self.run_task(client)
        self.assertEqual(client.commands.count('/install'), 1, 'the payload must not run twice')
        self.assertEqual(task.verification_attempts, 2)
        self.assertEqual(task.retry_count, 1, 'verification attempts are not retries')

    def test_verification_gives_up_after_the_timeout(self):
        start = timezone.now()
        client = self.payload_client(verify_output='installed-version: 7.16')
        with mock.patch('django.utils.timezone.now', return_value=start):
            task = self.run_task(client)
        self.assertEqual(task.status, 'pending')
        with mock.patch('django.utils.timezone.now', return_value=start + datetime.timedelta(seconds=301)):
            task = self.run_task(client)
        self.assertEqual(task.status, 'error')
        self.assertIn("no match for 'installed-version: {{ expected_version }}'", task.error_message)

    def test_retry_waits_at_least_five_seconds(self):
        self.command.verify_interval = 0
        self.command.save()
        now = timezone.now()
        with mock.patch('django.utils.timezone.now', return_value=now):
            task = self.run_task(self.payload_client(verify_output='nope'))
        self.assertEqual(task.next_retry, now + datetime.timedelta(seconds=5))

    def test_payload_that_does_not_report_the_version_fails_at_once_with_a_hint(self):
        client = FakeSSHClient({'/verify': 'installed-version: 7.17'})
        task = self.run_task(client)
        self.assertEqual(task.status, 'error')
        self.assertIn("'{{ expected_version }}'", task.error_message)
        self.assertIn('expected-version=', task.error_message)
        self.assertNotIn('/verify', client.commands, 'no point verifying what can never match')

    def test_unread_device_fails_at_once(self):
        RouterInformation.objects.filter(router=self.router).delete()
        self.add_verification('{{ current_version }}')
        task = self.run_task(FakeSSHClient())
        self.assertEqual(task.status, 'error')
        self.assertIn('Update the router information', task.error_message)

    def test_verification_command_failure_is_reported(self):
        client = self.payload_client()
        client.responses['/verify'] = (1, '', 'not allowed')
        with mock.patch('django.utils.timezone.now', return_value=timezone.now()):
            task = self.run_task(client)
        self.assertIn('not allowed', task.verification_output)

    def test_unreachable_for_verification(self):
        context = {'expected_version': '1'}
        self.task.verify_context = context
        self.task.payload_executed = True
        self.task.save()
        with mock.patch('routerlib.device_session.connect_to_ssh', side_effect=OSError('still booting')):
            cf.execute_command_task(self.task)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'pending')
        self.assertIn('Unable to connect: still booting', self.task.verification_output)

    def test_variant_without_expectation_lines(self):
        self.variant.verify_expect = '   \n  '
        self.variant.verify_payload = '/verify'
        self.variant.save()
        self.task.verify_context = {}
        self.task.save()
        state, message = cf.verify_command_task(self.task, self.variant, self.command)
        self.assertEqual(state, 'failed')
        self.assertIn('Nothing to verify', message)

    def test_verification_output_is_bounded(self):
        self.task.verification_output = 'x' * (cf.MAX_VERIFICATION_OUTPUT + 5000)
        self.task.verify_context = {'expected_version': '7.17'}
        self.task.save()
        with fake_ssh(SSH, FakeSSHClient({'/verify': 'installed-version: 7.17'})):
            cf.verify_command_task(self.task, self.variant, self.command)
        self.assertLessEqual(len(self.task.verification_output), cf.MAX_VERIFICATION_OUTPUT)


class JobCreationTests(TestCase):
    def setUp(self):
        self.command = Command.objects.create(name='C')
        self.variant = CommandVariant.objects.create(command=self.command, router_type='routeros', payload='/x')
        self.now = timezone.now()

    def schedule(self, **fields):
        fields.setdefault('next_run', self.now - datetime.timedelta(seconds=30))
        fields.setdefault('start_at', self.now - datetime.timedelta(days=1))
        fields.setdefault('repeat_interval', 1440)
        return CommandSchedule.objects.create(command=self.command, **fields)

    def test_due_schedule_creates_a_job_with_one_task_per_device(self):
        a, b = make_router(), make_router()
        openwrt = make_router(router_type='openwrt')
        schedule = self.schedule()
        schedule.router.add(a, b, openwrt)
        result = cf.create_jobs_from_schedules()
        self.assertEqual(result, {'jobs_created': 1, 'tasks_created': 3, 'runs_missed': 0})
        job = CommandJob.objects.get()
        self.assertEqual((job.exec_source, job.schedule), ('schedule', schedule))
        # The device type without a variant still gets its task, which fails visibly
        self.assertIsNone(job.tasks.get(router=openwrt).command_variant)
        schedule.refresh_from_db()
        self.assertIsNotNone(schedule.last_run)
        self.assertGreater(schedule.next_run, self.now)

    def test_late_schedule_is_missed_not_run(self):
        schedule = self.schedule(next_run=self.now - datetime.timedelta(minutes=10))
        schedule.router.add(make_router())
        result = cf.create_jobs_from_schedules()
        self.assertEqual(result['runs_missed'], 1)
        self.assertFalse(CommandJob.objects.exists())
        schedule.refresh_from_db()
        self.assertIsNotNone(schedule.last_missed)

    def test_schedule_that_cannot_run_switches_itself_off_and_stops_its_work(self):
        router = make_router()
        schedule = self.schedule()
        schedule.router.add(router)
        old_job = CommandJob.objects.create(command=self.command, schedule=schedule)
        old_task = CommandTask.objects.create(job=old_job, router=router)
        self.variant.delete()
        cf.create_jobs_from_schedules()
        schedule.refresh_from_db()
        old_task.refresh_from_db()
        self.assertFalse(schedule.enabled)
        self.assertEqual(old_task.status, 'aborted')
        self.assertIn('can no longer run', old_task.error_message)

    def test_future_and_disabled_schedules_are_ignored(self):
        self.schedule(next_run=self.now + datetime.timedelta(hours=1)).router.add(make_router())
        self.schedule(enabled=False).router.add(make_router())
        self.assertEqual(cf.create_jobs_from_schedules()['jobs_created'], 0)

    def test_manual_job(self):
        a, b, c = make_router(), make_router(), make_router(enabled=False)
        job = cf.create_manual_job(self.command, routers=type(a).objects.filter(pk__in=[a.pk, c.pk]),
                                   router_groups=[make_group(routers=[a, b])])
        self.assertEqual(job.exec_source, 'manual')
        self.assertEqual({t.router for t in job.tasks.all()}, {a, b})

    def test_manual_job_without_targets(self):
        self.assertIsNone(cf.create_manual_job(self.command))


class CheckJobCompletionTests(TestCase):
    def test_completes_when_nothing_is_pending(self):
        command = Command.objects.create(name='C')
        job = CommandJob.objects.create(command=command)
        cf.check_job_completion(job)
        job.refresh_from_db()
        self.assertIsNotNone(job.completed)
