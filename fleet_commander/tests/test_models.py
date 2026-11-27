import datetime
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from fleet_commander.models import (Command, CommandJob, CommandSchedule, CommandTask, CommandVariant,
                                    ScheduleDefaults, format_interval, parse_interval)
from routerfleet.testing import make_group, make_router

NOW = timezone.make_aware(datetime.datetime(2026, 6, 1, 12, 0, 0), datetime.UTC)


def at(moment):
    return mock.patch('django.utils.timezone.now', return_value=moment)


class IntervalTests(SimpleTestCase):
    def test_parse(self):
        self.assertEqual(parse_interval('7d'), 7 * 1440)
        self.assertEqual(parse_interval('24h'), 1440)
        self.assertEqual(parse_interval('30m'), 30)
        self.assertEqual(parse_interval(' 2H '), 120)

    def test_parse_single_run_spellings(self):
        for value in (None, '', '0', 'none', 'off', 'One Time', '0d'):
            self.assertEqual(parse_interval(value), 0, value)

    def test_parse_errors(self):
        for value in ('7', '7x', 'xd', '-3h'):
            with self.assertRaises(ValueError, msg=value):
                parse_interval(value)

    def test_format(self):
        self.assertEqual(format_interval(0), 'One time')
        self.assertEqual(format_interval(None), 'One time')
        self.assertEqual(format_interval(10080), '7d')
        self.assertEqual(format_interval(120), '2h')
        self.assertEqual(format_interval(90), '90m')

    def test_format_parses_back(self):
        for minutes in (0, 30, 90, 120, 1440, 10080):
            self.assertEqual(parse_interval(format_interval(minutes)), minutes)


class ScheduleFixture(TestCase):
    def setUp(self):
        self.command = Command.objects.create(name='Cmd')
        self.variant = CommandVariant.objects.create(command=self.command, router_type='routeros',
                                                     payload='/system resource print')

    def schedule(self, **fields):
        fields.setdefault('repeat_interval', 1440)
        return CommandSchedule.objects.create(command=self.command, **fields)


class NextRunTests(ScheduleFixture):
    def test_future_start_is_the_next_run(self):
        start = NOW + datetime.timedelta(hours=3)
        with at(NOW):
            self.assertEqual(self.schedule(start_at=start).calculate_next_run, start)

    def test_past_start_moves_onto_its_own_grid(self):
        start = NOW - datetime.timedelta(days=2, hours=1)
        with at(NOW):
            # Daily from 11:00 two days ago: the next slot is 11:00 tomorrow
            self.assertEqual(self.schedule(start_at=start).calculate_next_run,
                             start + datetime.timedelta(days=3))

    def test_single_run_and_disabled_have_no_next_run(self):
        with at(NOW):
            self.assertIsNone(self.schedule(start_at=NOW, repeat_interval=0).calculate_next_run)
            self.assertIsNone(self.schedule(start_at=NOW, enabled=False).calculate_next_run)

    def test_end_at_stops_the_schedule(self):
        with at(NOW):
            schedule = self.schedule(start_at=NOW - datetime.timedelta(hours=1),
                                     end_at=NOW + datetime.timedelta(hours=1))
            self.assertIsNone(schedule.calculate_next_run)

    def test_update_next_run_saves(self):
        start = NOW + datetime.timedelta(hours=1)
        with at(NOW):
            schedule = self.schedule(start_at=start)
            schedule.update_next_run()
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run, start)


class LateRunTests(ScheduleFixture):
    def test_within_two_minutes_is_not_late(self):
        with at(NOW):
            self.assertFalse(self.schedule(next_run=NOW - datetime.timedelta(seconds=119)).is_late)

    def test_beyond_two_minutes_is_late(self):
        with at(NOW):
            self.assertTrue(self.schedule(next_run=NOW - datetime.timedelta(minutes=3)).is_late)

    @override_settings(SCHEDULE_LATE_LIMIT_MINUTES=10)
    def test_limit_is_configurable(self):
        with at(NOW):
            self.assertFalse(self.schedule(next_run=NOW - datetime.timedelta(minutes=9)).is_late)

    def test_no_next_run_is_not_late(self):
        self.assertFalse(self.schedule().is_late)

    def test_missed_run_is_recorded_and_the_grid_kept(self):
        start = NOW - datetime.timedelta(days=1, hours=1)
        missed = NOW - datetime.timedelta(hours=1)
        with at(NOW):
            schedule = self.schedule(start_at=start, next_run=missed)
            next_run = schedule.mark_run_missed()
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_missed, missed)
        # Tomorrow on the same grid, not 24 h after something looked again
        self.assertEqual(next_run, missed + datetime.timedelta(days=1))

    def test_missed_single_run_has_no_next_run(self):
        with at(NOW):
            schedule = self.schedule(start_at=NOW - datetime.timedelta(hours=1), repeat_interval=0,
                                     next_run=NOW - datetime.timedelta(hours=1))
            self.assertIsNone(schedule.mark_run_missed())

    def test_a_run_that_happens_clears_last_missed(self):
        with at(NOW):
            schedule = self.schedule(start_at=NOW - datetime.timedelta(hours=1), last_missed=NOW)
            schedule.mark_run_done()
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_run, NOW)
        self.assertIsNone(schedule.last_missed)


class TargetTests(ScheduleFixture):
    def test_selected_devices_and_groups(self):
        a, b, c = make_router(), make_router(), make_router()
        schedule = self.schedule()
        schedule.router.add(a)
        schedule.router_group.add(make_group(routers=[b, c]))
        self.assertEqual(schedule.target_routers(), {a, b, c})

    def test_disabled_devices_are_not_selected(self):
        on, off = make_router(), make_router(enabled=False)
        schedule = self.schedule()
        schedule.router_group.add(make_group(routers=[on, off]))
        self.assertEqual(schedule.target_routers(), {on})

    def test_exclusion_always_wins(self):
        a, b, c = make_router(), make_router(), make_router()
        location = make_group(routers=[a, b, c])
        device_type = make_group(routers=[b])
        schedule = self.schedule()
        schedule.router.add(c)
        schedule.router_group.add(location)
        schedule.exclude_router_group.add(device_type)
        schedule.exclude_router.add(c)
        self.assertEqual(schedule.target_routers(), {a})

    def test_summary(self):
        a, b = make_router(), make_router()
        schedule = self.schedule()
        self.assertEqual(schedule.target_summary, 'No device selected')
        schedule.router.add(a, b)
        self.assertEqual(schedule.target_summary, '2 devices')
        schedule.exclude_router.add(b)
        self.assertEqual(schedule.target_summary, '1 device, 1 excluded')

    def test_disable_if_invalid(self):
        schedule = self.schedule()
        schedule.router.add(make_router())
        self.assertTrue(schedule.disable_if_invalid())

        self.variant.enabled = False
        self.variant.save()
        self.assertFalse(schedule.disable_if_invalid())
        schedule.refresh_from_db()
        self.assertFalse(schedule.enabled)

    def test_disable_when_no_target_left(self):
        schedule = self.schedule()
        self.assertFalse(schedule.disable_if_invalid())

    def test_disable_when_command_disabled(self):
        schedule = self.schedule()
        schedule.router.add(make_router())
        self.command.enabled = False
        self.command.save()
        self.assertFalse(schedule.disable_if_invalid())

    def test_delete_aborts_pending_tasks(self):
        router = make_router()
        schedule = self.schedule()
        job = CommandJob.objects.create(command=self.command, schedule=schedule, exec_source='schedule')
        task = CommandTask.objects.create(job=job, router=router, command_variant=self.variant)
        schedule.delete()
        task.refresh_from_db()
        self.assertEqual(task.status, 'aborted')
        self.assertEqual(task.error_message, 'Schedule deleted')
        # The job survives the schedule, without the link
        job.refresh_from_db()
        self.assertIsNone(job.schedule)


class ScheduleDefaultsTests(TestCase):
    def test_load_creates_one_row(self):
        first = ScheduleDefaults.load()
        second = ScheduleDefaults.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.start_time, datetime.time(3, 0))
        self.assertEqual(first.repeat_interval_display, '7d')

    @override_settings(TIME_ZONE='UTC')
    def test_next_start_is_never_in_the_past(self):
        defaults = ScheduleDefaults.load()
        with at(NOW):  # 12:00 UTC
            defaults.start_time = datetime.time(3, 0)
            self.assertEqual(defaults.next_start_at(), NOW.replace(hour=3) + datetime.timedelta(days=1))
            defaults.start_time = datetime.time(13, 0)
            self.assertEqual(defaults.next_start_at(), NOW.replace(hour=13))


class JobAndTaskTests(ScheduleFixture):
    def test_task_copies_router_identity(self):
        router = make_router(name='edge-77')
        job = CommandJob.objects.create(command=self.command)
        task = CommandTask.objects.create(job=job, router=router)
        self.assertEqual(task.router_uuid, router.uuid)
        self.assertEqual(task.router_name, 'edge-77')
        router.delete()
        task.refresh_from_db()
        self.assertIsNone(task.router)
        self.assertEqual(task.router_name, 'edge-77')

    def test_counters_progress_and_completion(self):
        job = CommandJob.objects.create(command=self.command)
        tasks = [CommandTask.objects.create(job=job, router=make_router()) for _ in range(4)]
        job.refresh_from_db()
        self.assertEqual((job.task_count, job.progress_percentage), (4, 0))
        self.assertIsNone(job.completed)

        for task, status in zip(tasks, ('success', 'error', 'aborted'), strict=False):
            task.status = status
            task.save()
        job.refresh_from_db()
        self.assertEqual((job.success_count, job.error_count, job.aborted_count), (1, 1, 1))
        self.assertEqual(job.progress_percentage, 75)
        self.assertIsNone(job.completed)

        tasks[3].status = 'success'
        tasks[3].save()
        job.refresh_from_db()
        self.assertEqual(job.progress_percentage, 100)
        self.assertIsNotNone(job.completed)

    def test_empty_job_progress(self):
        self.assertEqual(CommandJob(command=self.command).progress_percentage, 0)

    def test_variant_has_verification_needs_both_halves(self):
        self.assertFalse(self.variant.has_verification)
        self.variant.verify_payload = 'x'
        self.assertFalse(self.variant.has_verification)
        self.variant.verify_expect = 'y'
        self.assertTrue(self.variant.has_verification)

    def test_can_execute(self):
        self.assertTrue(self.command.can_execute)
        self.command.enabled = False
        self.assertFalse(self.command.can_execute)

    def test_str(self):
        job = CommandJob.objects.create(command=self.command)
        self.assertEqual(str(job), f'#{job.id}: Cmd')
        self.assertEqual(str(self.variant), 'Cmd (routeros)')
