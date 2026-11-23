import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, ProtectedError
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from audit_log.bulk import suspend
from router_manager.models import Router
from routerlib.decorators import cron_endpoint
from user_manager.models import UserAcl
from .command_functions import build_verification_expectations, create_jobs_from_schedules, \
    execute_command_task, create_manual_job, abort_command_task, abort_tasks_of_schedule
from .forms import CommandForm, CommandVariantForm, CommandScheduleForm, CommandExecuteForm, \
    ScheduleDefaultsForm
from .models import Command, CommandVariant, CommandSchedule, CommandJob, CommandTask, \
    ScheduleDefaults
from routerlib.actions import posted_action, posted_confirmation
from .system_commands import SYSTEM_KEYS as SYSTEM_ORDER


def create_default_commands():
    new_command = Command.objects.create(
        name='Show Version',
        description = "Collects and displays version information for your routers. This command is commonly used to gather details about the router model, software version, uptime, and hardware.",
        enabled=True,
        capture_output=True,
        max_retry=3,
        retry_interval=30,
    )
    for router_type in ['routeros']:
        CommandVariant.objects.create(
            command=new_command, router_type=router_type, enabled=True,
            payload='/system resource print\n/system package print'
        )
    CommandVariant.objects.create(
        command=new_command, router_type='openwrt', enabled=True,
        payload='uname -a\ncat /etc/openwrt_release\nubus call system board'
    )
    CommandVariant.objects.create(
        command=new_command, router_type='ubiquiti-airos', enabled=True,
        payload='uname -a\ncat /etc/version\ncat /proc/uptime'
    )

    new_command = Command.objects.create(
        name='Update Router Firmware',
        description = "Updates the router's firmware to the latest version. This command is essential for maintaining security and performance by ensuring that your routers are running the most recent software.",
        enabled=True,
        capture_output=True,
        max_retry=3,
        retry_interval=30,
        verify_timeout=600,
        verify_interval=30,
    )
    for router_type in ['routeros']:
        CommandVariant.objects.create(
            command=new_command, router_type=router_type, enabled=True,
            # The download is a step of its own, so a missing update file is
            # visible in the output instead of only failing the install
            payload='/system/package/update/set channel=long-term\n'
                    '/system/package/update/check-for-updates\n'
                    ':delay 5s\n'
                    # The version to install depends on the channel that was just
                    # set, so the router has to report it: the version known to
                    # RouterFleet belongs to the channel from before
                    ':put ("expected-version=" . [/system/package/update/get latest-version])\n'
                    '/system/package/update/download\n'
                    '/system/package/update/install',
            # The install reboots the router, so the only reliable proof is the
            # version it reports once it is back online. The print and the
            # explicit line both write the version the same way, so the
            # expectation matches either of them
            verify_payload='/system/package/update/print\n'
                           ':put ("installed-version: " . [/system/resource/get version])',
            verify_expect='installed-version: {{ expected_version }}',
        )
    return


@login_required()
def view_command_list(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=20).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    from django.db.models import Count
    counted = Command.objects.annotate(variant_count=Count('variants', distinct=True),
                                       schedule_count=Count('schedules', distinct=True))
    command_list = counted.order_by('system_key', 'name')
    if not Command.objects.filter(system_key__isnull=True).exists():
        # Seeded while a page is being rendered, so nothing is recorded: the
        # application made these for itself, nobody did
        with suspend():
            create_default_commands()
        messages.success(request, 'Default commands created successfully')
        command_list = counted.order_by('system_key', 'name')

    context = {
        'command_list': [c for c in command_list if not c.system_key],
        'system_command_list': sorted((c for c in command_list if c.system_key),
                                      key=lambda c: SYSTEM_ORDER.index(c.system_key) if c.system_key in SYSTEM_ORDER else 99),
        'page_title': 'Fleet Commander',
        'jobs_running': CommandJob.objects.filter(completed__isnull=True).count(),
        'can_manage': UserAcl.objects.filter(user=request.user, user_level__gte=40).exists(),
    }
    return render(request, 'fleet_commander/command_list.html', context)


@login_required()
def view_command_details(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=20).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    command = get_object_or_404(Command, uuid=request.GET.get('uuid'))
    context = {
        'command': command,
        'variant_list': command.variants.all().order_by('router_type'),
        'schedule_list': command.schedules.all().order_by('-created'),
        'page_title': command.name,
        'is_administrator': UserAcl.objects.filter(user=request.user, user_level__gte=50).exists(),
    }
    return render(request, 'fleet_commander/command_details.html', context)


@login_required()
def view_manage_command(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=40).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})

    if request.GET.get('uuid'):
        command = get_object_or_404(Command, uuid=request.GET.get('uuid'))
        if command.system_key:
            messages.warning(request, 'System command|It can not be edited, disabled or deleted here: RouterFleet '
                                      'needs it. An administrator can edit its variants.')
            return redirect(f'/fleet_commander/command/details/?uuid={command.uuid}')
        if posted_action(request, 'delete'):
            if posted_confirmation(request) == 'delete':
                try:
                    command.delete()
                    messages.success(request, 'Command deleted successfully')
                except ProtectedError:
                    messages.warning(request, 'Cannot delete command because it is referenced by existing variants, schedules, or jobs.')
                    return redirect(f'/fleet_commander/command/details/?uuid={command.uuid}')
            else:
                messages.warning(request, 'Command not deleted|Invalid confirmation')
                return redirect(f'/fleet_commander/command/details/?uuid={command.uuid}')
            return redirect('/fleet_commander/')
    else:
        command = None

    form = CommandForm(request.POST or None, instance=command)
    if form.is_valid():
        saved = form.save()
        messages.success(request, 'Command saved successfully')
        if command:
            return redirect(f'/fleet_commander/command/details/?uuid={saved.uuid}')
        else:
            return redirect('/fleet_commander/')

    form_description_content = '''
    <strong>Enabled</strong>
    <p>Enable or disable the command. Disabled commands cannot be executed.</p>
    
    <strong>Capture Output</strong>
    <p>If unchecked, RouterFleet executes the command and immediately closes the connection. If checked, RouterFleet will wait for the command to finish to capture its output. Wait times can be long and may delay the execution queue if you have many commands pending. <strong>Only check this if you really need the output.</strong> The output of a command with a verification is always captured, it is what the verification is checked against.</p>
    
    <strong>Max Retry and Retry Interval</strong>
    <p>Maximum number of retries if the command fails, and the interval (in seconds) between each attempt.</p>

    <strong>Verification</strong>
    <p>The result of a command can be verified instead of trusting its exit code. The verification commands and
    their expected result are defined per variant (router type). A task is only successful when the device
    answers what the variant expects. This is the reliable way to check an update: a router that reboots while
    it is being updated never returns an exit code at all.</p>

    <strong>Verify Timeout and Verify Interval</strong>
    <p>How long (in seconds) RouterFleet keeps verifying a command, and how long it waits between two attempts.
    The payload is only executed once, a later attempt only verifies again, so a device that reboots is not
    updated twice. The timeout has to be long enough for the device to come back online.</p>
    '''

    context = {
        'form': form,
        'page_title': 'Manage Command',
        'instance': command,
        'form_description': {
            'size': '',
            'content': form_description_content
        },
    }
    return render(request, 'generic_form.html', context)


@login_required()
def view_execute_command(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=40).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})

    command = get_object_or_404(Command, uuid=request.GET.get('command_uuid'), enabled=True,
                                system_key__isnull=True)
    if not command.can_execute:
        messages.warning(request, 'No variants defined for this command. Please create a variant before executing.')
        return redirect(f'/fleet_commander/command/details/?uuid={command.uuid}')
    form = CommandExecuteForm(request.POST or None, command=command)

    if form.is_valid():
        routers, groups = form.cleaned_data['routers'], form.cleaned_data['router_groups']
        plan = _execution_plan(command, routers=routers, router_groups=groups)
        if plan['count']:
            hidden = [('routers', str(r.pk)) for r in routers] + [('router_groups', str(g.pk)) for g in groups]
            return _confirm_then_run(request, command, plan, hidden, f'/fleet_commander/command/details/?uuid={command.uuid}',
                                     routers=routers, router_groups=groups)
        messages.warning(request, 'No targets selected or found enabled')

    context = {
        'form': form,
        'page_title': f'Execute Command: {command.name}',
        'command': command,
    }
    return render(request, 'generic_form.html', context)


def _execution_plan(command, routers=None, router_groups=None):
    """What a manual job would send, as the confirmation page shows it: per
    device type, the exact payload and verification, the devices it goes to,
    and the devices left out. The fingerprint changes when any of it does."""
    import hashlib
    targets = set()
    if routers is not None:
        targets.update(routers.filter(enabled=True))
    for group in router_groups or []:
        targets.update(group.routers.filter(enabled=True))
    variants = {v.router_type: v for v in CommandVariant.objects.filter(command=command, enabled=True).order_by('id')}
    by_type, skipped = {}, []
    for router in sorted(targets, key=lambda r: r.name.lower()):
        variant = variants.get(router.router_type)
        if variant is None:
            skipped.append(router)
            continue
        by_type.setdefault(router.router_type, {'variant': variant, 'routers': []})['routers'].append(router)
    groups = [dict(entry, router_type=kind) for kind, entry in sorted(by_type.items())]
    digest = hashlib.sha256()
    digest.update(str(command.uuid).encode())
    for entry in groups:
        variant = entry['variant']
        digest.update(f"|{variant.id}|{variant.payload}|{variant.verify_payload}|{variant.verify_expect}".encode())
        digest.update(','.join(str(r.id) for r in entry['routers']).encode())
    return {'groups': groups, 'skipped': skipped, 'count': sum(len(e['routers']) for e in groups),
            'fingerprint': digest.hexdigest()}


def _confirm_then_run(request, command, plan, hidden, back_url, routers=None, router_groups=None):
    """Execution always goes through the confirmation page: the job is created
    only from a POST that carries the fingerprint of the page the user saw, so
    a variant edited in between is shown again instead of run."""
    if request.POST.get('confirmed') and request.POST.get('fingerprint') == plan['fingerprint'] and plan['count']:
        job = create_manual_job(command, routers=routers, router_groups=router_groups, user=request.user)
        request.session.pop('router_selection', None)
        if job:
            messages.success(request, f'Job created for {job.tasks.count()} targets')
            return redirect(f'/fleet_commander/job/details/?uuid={job.uuid}')
        messages.warning(request, 'No targets selected or found enabled')
        return redirect(back_url)
    if request.POST.get('confirmed'):
        messages.warning(request, 'Not executed|What would run changed since the confirmation: check it again.')
    return render(request, 'fleet_commander/confirm_execution.html', {
        'page_title': f'Confirm: {command.name}', 'command': command, 'plan': plan,
        'hidden': hidden, 'back_url': back_url})


@login_required()
def view_execute_on_router(request):
    """One device: pick a command, see exactly what it will send, confirm."""
    if not UserAcl.objects.filter(user=request.user, user_level__gte=40).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    router = get_object_or_404(Router, uuid=request.GET.get('uuid') or request.POST.get('router'))
    back_url = f'/router/details/?uuid={router.uuid}'
    if request.method == 'POST' and request.POST.get('command'):
        command = get_object_or_404(Command, uuid=request.POST.get('command'), enabled=True,
                                    system_key__isnull=True)
        routers = Router.objects.filter(pk=router.pk)
        plan = _execution_plan(command, routers=routers)
        hidden = [('router', str(router.uuid)), ('command', str(command.uuid))]
        return _confirm_then_run(request, command, plan, hidden, back_url, routers=routers)
    commands = Command.objects.filter(enabled=True, system_key__isnull=True, variants__router_type=router.router_type,
                                      variants__enabled=True).distinct().order_by('name')
    return render(request, 'fleet_commander/execute_on_router.html', {
        'page_title': f'Execute command on {router.name}', 'router': router, 'commands': commands,
        'recent_tasks': router.commandtask_set.select_related('job__command').order_by('-created')[:10],
        'back_url': back_url})


@login_required()
def view_run_command_multiple(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=40).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})

    if request.method == 'POST':
        if 'routers[]' in request.POST:
            # First hop: selection arrives in the request body so the URL never grows
            # with the number of selected routers
            router_uuids = [str(uuid) for uuid in Router.objects.filter(
                uuid__in=request.POST.getlist('routers[]')
            ).values_list('uuid', flat=True)]
            if not router_uuids:
                messages.warning(request, 'No routers selected')
                return redirect('router_list')
            request.session['router_selection'] = router_uuids
            return redirect('fleet_commander_execute_multiple')

        router_uuids = request.POST.getlist('router_uuids')
        command_uuid = request.POST.get('command')

        if not router_uuids:
            messages.warning(request, 'No routers selected')
            return redirect('router_list')

        if not command_uuid:
            messages.warning(request, 'No command selected')
            return redirect('router_list')

        command = get_object_or_404(Command, uuid=command_uuid, enabled=True, system_key__isnull=True)
        routers = Router.objects.filter(uuid__in=router_uuids)

        if not command.can_execute:
            messages.warning(request, 'No variants defined for this command. Please create a variant before executing.')
            return redirect('router_list')

        plan = _execution_plan(command, routers=routers)
        if not plan['count']:
            messages.warning(request, 'No targets selected or found enabled')
            return redirect('router_list')
        hidden = [('router_uuids', str(r.uuid)) for r in routers] + [('command', str(command.uuid))]
        return _confirm_then_run(request, command, plan, hidden, '/router/list/', routers=routers)

    # GET request - display form
    router_uuids = request.session.get('router_selection') or request.GET.getlist('routers[]')
    if not router_uuids:
        messages.warning(request, 'No routers selected')
        return redirect('router_list')

    routers = Router.objects.filter(uuid__in=router_uuids)
    commands = Command.objects.filter(enabled=True, system_key__isnull=True).order_by('name')

    context = {
        'routers': routers,
        'commands': commands,
        'page_title': 'Run Command on Multiple Routers',
    }

    return render(request, 'fleet_commander/run_command_multiple.html', context)


@login_required()
def view_manage_command_variant(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=40).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})

    if request.GET.get('uuid'):
        variant = get_object_or_404(CommandVariant, uuid=request.GET.get('uuid'))
        command = variant.command
        if command.system_key:
            return redirect(f'/fleet_commander/system/variant/?uuid={variant.uuid}')
        if posted_action(request, 'delete'):
            if posted_confirmation(request) == 'delete':
                variant.delete()
                messages.success(request, 'Variant deleted successfully')
            else:
                messages.warning(request, 'Variant not deleted|Invalid confirmation')
            return redirect(f'/fleet_commander/command/details/?uuid={command.uuid}')
    else:
        variant = None
        command = get_object_or_404(Command, uuid=request.GET.get('command_uuid'))
        if command.system_key:
            messages.warning(request, 'System command|Its variants are fixed: one per device type.')
            return redirect(f'/fleet_commander/command/details/?uuid={command.uuid}')

    form = CommandVariantForm(request.POST or None, instance=variant, command=command)
    if form.is_valid():
        form.save()
        messages.success(request, 'Variant saved successfully')
        return redirect(f'/fleet_commander/command/details/?uuid={command.uuid}')

    form_description_content = '''
    <strong>Payload</strong>
    <p>The commands for this router type, one command per line. They are executed in order and the execution
    stops at the first command that fails.</p>

    <strong>Verification Commands</strong>
    <p>Optional. Commands that check the result of the payload, one command per line. They are executed after
    the payload, on a new connection. Leave this empty to keep the exit code of the payload as the result of
    the task.</p>

    <strong>Expected Result</strong>
    <p>One expectation per line. Every line has to be found in the output of the verification commands,
    otherwise the task fails. This is how an update is checked: for example the version the router reports has
    to be the version that was offered for it. Regular expressions are supported.</p>
    <p>The placeholders <code>{{ available_version }}</code> (the version RouterFleet found as an update for
    this router) and <code>{{ current_version }}</code> (the version the router was running before the payload)
    are replaced with the values known for the router.</p>
    <p><code>{{ expected_version }}</code> is the version the <strong>router</strong> offers. It is taken from
    a line the payload has to print, for example
    <code>:put ("expected-version=" . [/system/package/update/get latest-version])</code>. Use it for a payload
    that sets the update channel: which version gets installed depends on that channel, while
    <code>{{ available_version }}</code> is the version RouterFleet read out on the channel the router had
    before, so the two differ as soon as the payload changes the channel.</p>
    <p>While the verification does not match, the task is retried and only the verification runs again, until
    the verify timeout of the command has passed. That gives a router that reboots during an update the time
    to come back online.</p>
    '''

    context = {
        'form': form,
        'page_title': 'Manage Variant',
        'instance': variant,
        'form_description': {
            'size': '',
            'content': form_description_content
        },
    }
    return render(request, 'generic_form.html', context)


@login_required()
def view_manage_command_schedule(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=40).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})

    if request.GET.get('uuid'):
        schedule = get_object_or_404(CommandSchedule, uuid=request.GET.get('uuid'))
        command = schedule.command
        if posted_action(request, 'delete'):
            if posted_confirmation(request) == 'delete':
                # The tasks a schedule has created are rows of their own and the
                # cron goes on performing them, so removing the schedule stops
                # them first - otherwise the command still goes out after it
                stopped = abort_tasks_of_schedule(
                    schedule, request.user, f'Schedule deleted by {request.user.username}')
                schedule.delete()
                messages.success(request, 'Schedule deleted successfully'
                                 + stopped_message(stopped))
            else:
                messages.warning(request, 'Schedule not deleted|Invalid confirmation')
            return redirect(f'/fleet_commander/command/details/?uuid={command.uuid}')
    else:
        schedule = None
        command = get_object_or_404(Command, uuid=request.GET.get('command_uuid'))
        if command.system_key:
            messages.warning(request, 'System command|RouterFleet runs it itself: it can not be scheduled.')
            return redirect(f'/fleet_commander/command/details/?uuid={command.uuid}')

    # Read before the form is built. Binding a form fills the instance in from
    # what was sent, so by the time it is valid the switch is already off and
    # there is nothing left to compare against.
    was_enabled = bool(schedule and schedule.enabled)

    form = CommandScheduleForm(request.POST or None, instance=schedule, command=command)
    if form.is_valid():
        saved = form.save()
        saved.update_next_run()

        stopped = 0
        if was_enabled and not saved.enabled:
            # Switching a schedule off is meant to stop it, and what it has
            # already dispatched is still on its way to the devices
            stopped = abort_tasks_of_schedule(
                saved, request.user, f'Schedule switched off by {request.user.username}')

        messages.success(request, 'Schedule saved successfully' + stopped_message(stopped))
        return redirect(f'/fleet_commander/command/details/?uuid={command.uuid}')

    form_description_content = '''
    <strong>Start At</strong>
    <p>The designated date and time when the schedule should begin execution. All schedules require a starting point.</p>
    
    <strong>End At</strong>
    <p>Optional. If set, the schedule will not execute after this date and time.</p>
    
    <strong>Repeat Interval</strong>
    <p>How frequently the command should run. You must specify a letter indicating the unit of time:</p>
    <ul>
        <li><strong>d</strong> for days (e.g., <code>1d</code>, <code>30d</code>)</li>
        <li><strong>h</strong> for hours (e.g., <code>2h</code>, <code>24h</code>)</li>
        <li><strong>m</strong> for minutes (e.g., <code>30m</code>, <code>90m</code>)</li>
    </ul>
    <p>Leave it empty or set to <code>0</code> for a one-time execution.</p>
    
    <strong>Target</strong>
    <p>You must select at least one router or one router group to execute the scheduled command.</p>

    <strong>Excluded Routers and Excluded Router Groups</strong>
    <p>Devices that are taken out of this schedule again. An exclusion always wins, no matter how a
    device came into the schedule: a device that is selected on its own, or belongs to a selected
    group, is still not executed when it stands in an excluded group or was excluded itself. A
    device that is in both a <em>Location</em> group that should run and a <em>Type</em> group that
    should not is therefore left out - exclude the <em>Type</em> group.</p>
    <p>One device can be excluded on its own the same way, without taking its groups apart. Excluded
    devices stay in the schedule and are picked up again as soon as the exclusion is removed.</p>
    '''

    context = {
        'form': form,
        'page_title': 'Manage Schedule',
        'instance': schedule,
        'form_description': {
            'size': '',
            'content': form_description_content
        },
    }
    return render(request, 'generic_form.html', context)


@login_required()
def view_schedule_defaults(request):
    """The values a new schedule is created with.

    They belong to the installation, not to a command, so they stand here once
    instead of in every schedule. A schedule that already exists keeps what it
    was saved with - only the form of a new one is filled from here.
    """
    if not UserAcl.objects.filter(user=request.user, user_level__gte=40).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})

    schedule_defaults = ScheduleDefaults.load()

    form = ScheduleDefaultsForm(request.POST or None, instance=schedule_defaults)
    if form.is_valid():
        form.save()
        messages.success(request, 'Schedule defaults saved successfully')
        return redirect('schedule_defaults')

    form_description_content = '''
    <strong>Default Start Time</strong>
    <p>The time of day a new schedule begins at. A schedule that is created starts at the next
    time this comes around, so an installation that keeps its maintenance window at 03:00 does
    not have to have it typed in for every command.</p>

    <strong>Default Repeat Interval</strong>
    <p>How often a new schedule repeats, in the same units the schedule itself takes: <code>d</code>
    for days, <code>h</code> for hours, <code>m</code> for minutes. Empty or <code>0</code> means a
    new schedule is created for a single run.</p>

    <strong>What this does not change</strong>
    <p>These values fill the form of a schedule that is being created. Schedules that already exist
    keep the start time and the interval they were saved with, so changing a default here never
    moves a job that is already set up.</p>
    <p>The same values can be set from the command line:
    <code>python manage.py schedule_defaults --start-time 03:00 --repeat 7d</code></p>
    '''

    context = {
        'form': form,
        'page_title': 'Schedule Defaults',
        'instance': schedule_defaults,
        'form_description': {
            'size': '',
            'content': form_description_content
        },
    }
    return render(request, 'generic_form.html', context)


@login_required()
def view_job_list(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=20).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    job_list = CommandJob.objects.all().select_related('command').order_by('-created')
    context = {
        'job_list': job_list,
        'page_title': 'Job History',
        'live_active': job_list.filter(completed__isnull=True).exists(),
    }
    return render(request, 'fleet_commander/job_list.html', context)


@login_required()
def view_job_details(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=20).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    from routerfleet.object_pages import job_page, task_state
    job = get_object_or_404(CommandJob, uuid=request.GET.get('uuid'))
    task_list = list(job.tasks.select_related('router__routerstatus').order_by('-created'))
    for task in task_list:
        task.state = task_state(task)
    context = {
        'job': job,
        'task_list': task_list,
        'page': job_page(job, task_list),
        'page_title': f'Job: {job.command.name}',
    }
    return render(request, 'fleet_commander/job_details.html', context)


@login_required()
def view_task_details(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=20).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    task = get_object_or_404(CommandTask, uuid=request.GET.get('uuid'))
    verification_expectations = []
    if task.command_variant:
        verification_expectations = build_verification_expectations(
            task.command_variant, task.verify_context or {}
        )
    from routerfleet.object_pages import task_page
    context = {
        'task': task,
        'page': task_page(task),
        'verification_expectations': verification_expectations,
        'page_title': f'Task: {task.router_name or task.router_uuid}',
    }
    return render(request, 'fleet_commander/task_details.html', context)


ABORT_CONFIRMATION = 'abort'


def abort_confirmed(request):
    return posted_confirmation(request) == ABORT_CONFIRMATION


def stopped_message(count):
    """The second line of a message about work that was stopped with its schedule.

    Empty when nothing was stopped, so the caller can put it straight behind the
    message it always shows - see template_messages.html for the format.
    """
    if not count:
        return ''
    noun = 'task it had started was stopped' if count == 1 else 'tasks it had started were stopped'
    return f'|{count} {noun}'


@login_required()
def view_abort_command_task(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=20).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    task = get_object_or_404(CommandTask, uuid=request.GET.get('uuid'))
    job = task.job

    if not abort_confirmed(request):
        messages.warning(request, 'Task not stopped|Invalid confirmation')
        return redirect(f'/fleet_commander/task/details/?uuid={task.uuid}')

    if abort_command_task(task, request.user):
        messages.success(request, f'Task for {task.router_name or task.router_uuid} stopped')
    else:
        messages.warning(request, 'Task not stopped|Only a task that has not finished yet can be stopped')

    return redirect(f'/fleet_commander/job/details/?uuid={job.uuid}')


@login_required()
def view_abort_command_job(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=20).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    job = get_object_or_404(CommandJob, uuid=request.GET.get('uuid'))

    if not abort_confirmed(request):
        messages.warning(request, 'Job not stopped|Invalid confirmation')
        return redirect(f'/fleet_commander/job/details/?uuid={job.uuid}')

    stopped = 0
    for task in job.tasks.filter(status='pending'):
        if abort_command_task(task, request.user):
            stopped += 1

    if stopped:
        messages.success(request, f'Job stopped|{stopped} pending tasks of this job were aborted')
    else:
        messages.warning(request, 'Job not stopped|This job has no pending tasks left')

    return redirect(f'/fleet_commander/job/details/?uuid={job.uuid}')


@cron_endpoint
def view_cron_create_command_jobs(request):
    data = create_jobs_from_schedules()
    return JsonResponse(data)


@cron_endpoint
def view_cron_perform_command_tasks(request):
    data = {'tasks_performed': 0}
    max_execution_time = 45
    execution_start_time = timezone.now()

    pending_tasks = CommandTask.objects.filter(
        status='pending',
        router__routerstatus__command_lock__isnull=True, router__routerstatus__backup_lock__isnull=True
    ).filter(
        Q(next_retry__isnull=True) | Q(next_retry__lte=timezone.now())
    ).filter(
        # The work of a schedule that is switched off does not go out, whatever
        # is still sitting in the job it created. A job that nobody scheduled
        # has no schedule here and is performed as it always was.
        Q(job__schedule__isnull=True) | Q(job__schedule__enabled=True)
    ).select_related('job__command', 'router', 'command_variant')

    for task in pending_tasks:
        execute_command_task(task)
        data['tasks_performed'] += 1

        if timezone.now() - execution_start_time > datetime.timedelta(seconds=max_execution_time):
            break

    return JsonResponse(data)


# --- System commands: edited only by an administrator, after a review of the
# change and a confirmation that takes attention: a red button, and a small
# sum to solve in the confirmation popup, checked here on the server

SYSTEM_CHALLENGE_SECONDS = 600


def _payload_digest(variant_uuid, payload):
    import hashlib
    return hashlib.sha256(f'{variant_uuid}|{payload}'.encode()).hexdigest()


@login_required()
def view_edit_system_variant(request):
    import difflib
    import secrets
    import time
    from . import system_commands
    if not UserAcl.objects.filter(user=request.user, user_level__gte=50).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    variant = get_object_or_404(CommandVariant, uuid=request.GET.get('uuid'), command__system_key__isnull=False)
    command = variant.command
    key = command.system_key
    default = system_commands.default_payload(key, variant.router_type)
    details_url = f'/fleet_commander/command/details/?uuid={command.uuid}'
    edit_url = f'/fleet_commander/system/variant/?uuid={variant.uuid}'
    context = {'page_title': f'Edit system command: {command.name}', 'command': command, 'variant': variant,
               'definition': system_commands.DEFAULTS[key], 'default_payload': default,
               'is_default': (variant.payload or '').strip() == default.strip(), 'details_url': details_url}

    action = request.POST.get('action') if request.method == 'POST' else None
    if action in ('review', 'restore'):
        payload = default if action == 'restore' else request.POST.get('payload', '').replace('\r\n', '\n')
        problems = system_commands.validate_payload(key, variant.router_type, payload)
        if problems:
            return render(request, 'fleet_commander/system_variant_edit.html',
                          dict(context, payload=payload, problems=problems))
        if payload.strip() == (variant.payload or '').strip():
            messages.info(request, 'No change|The payload is the same as the stored one.')
            return redirect(edit_url)
        first, second = 3 + secrets.randbelow(7), 11 + secrets.randbelow(29)
        request.session['system_variant_challenge'] = {
            'variant': str(variant.uuid), 'digest': _payload_digest(variant.uuid, payload),
            'answer': first + second, 'expires': time.time() + SYSTEM_CHALLENGE_SECONDS}
        diff = list(difflib.unified_diff((variant.payload or '').splitlines(), payload.splitlines(),
                                         'stored', 'new', lineterm=''))
        return render(request, 'fleet_commander/system_variant_review.html',
                      dict(context, payload=payload, diff=diff[2:], first=first, second=second,
                           restoring=action == 'restore'))

    if action == 'apply':
        payload = request.POST.get('payload', '').replace('\r\n', '\n')
        # One attempt per review: a wrong answer means reviewing again
        challenge = request.session.pop('system_variant_challenge', None) or {}
        answer = request.POST.get('answer', '').strip()
        valid = (challenge.get('variant') == str(variant.uuid)
                 and challenge.get('digest') == _payload_digest(variant.uuid, payload)
                 and time.time() < challenge.get('expires', 0)
                 and answer.isdigit() and int(answer) == challenge.get('answer'))
        if not valid or system_commands.validate_payload(key, variant.router_type, payload):
            messages.error(request, 'Not applied|The confirmation was wrong, expired or did not match the reviewed '
                                    'change. Nothing was changed.')
            return redirect(edit_url)
        variant.payload = payload
        variant.save()
        messages.success(request, f'System command changed|{command.name}, {variant.get_router_type_display()}. '
                                  'Recorded in the change log.')
        return redirect(details_url)

    return render(request, 'fleet_commander/system_variant_edit.html', dict(context, payload=variant.payload))
