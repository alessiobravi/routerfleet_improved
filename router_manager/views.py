from django.conf import settings
import json
import time
from urllib.parse import unquote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.http import Http404, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from audit_log.bulk import collect
from backup.models import BackupProfile
from backup_data.models import RouterBackup
from routerfleet_tools.models import WebadminSettings
from routerlib.decorators import cron_endpoint
from routerlib.router_functions import update_router_information
from user_manager.models import UserAcl
from routerfleet.object_pages import router_page
from .forms import RouterForm, RouterBulkEditForm, RouterGroupForm, SSHKeyForm
from .models import Router, RouterGroup, RouterInformation, RouterStatus, SSHKey, BackupSchedule
from routerlib.actions import posted_action, posted_confirmation
from router_manager.templatetags.router_tags import update_state_of_routers
from routerlib import host_keys

# How long the information update cron task may run; it is started once per minute
MAX_UPDATE_INFORMATION_RUNTIME = 50


def _in_update_state(state):
    def narrow(queryset):
        ids = [router_id for router_id, value in update_state_of_routers().items() if value == state]
        return queryset.filter(id__in=ids)
    return narrow


# The states the status page's cards count, each defined exactly as the card
# counts it, so the number on the card and the length of the list agree
ROUTER_STATES = {
    'online': ('Online routers', lambda qs: qs.filter(monitoring=True, routerstatus__status_online=True)),
    'offline': ('Offline routers', lambda qs: qs.filter(monitoring=True, routerstatus__status_online=False)),
    'disabled': ('Disabled routers', lambda qs: qs.filter(enabled=False)),
    'unmonitored': ('Unmonitored routers', lambda qs: qs.filter(enabled=True, monitoring=False)),
    'backup_failed': ('Routers with a failed backup in the last 24 hours', lambda qs: qs.filter(
        id__in=RouterBackup.objects.filter(error=True, created__gte=timezone.now() - timezone.timedelta(days=1))
        .values('router_id'))),
    'up_to_date': ('Routers up to date', _in_update_state('up_to_date')),
    'update_waiting': ('Routers missing updates', _in_update_state('update_waiting')),
    'no_information': ('Routers without version information', _in_update_state('no_information')),
}


@login_required
def view_router_list(request):
    router_list = Router.objects.all().prefetch_related(
        'routerstatus', 
        'routerinformation', 
        'backupschedule', 
        'routergroup_set'
    ).order_by('name')

    # The newest backup that brought a change, for the "Last Configuration
    # Change" column. Annotated instead of asked per router, so the list stays one
    # query however many devices it shows. The time is when the configuration was
    # read from the device, which is what finish_time holds wherever it is known.
    change_backups = RouterBackup.objects.filter(
        router=OuterRef('pk'), config_change_detected=True).order_by('-created', '-id')
    router_list = router_list.annotate(
        last_config_change_time=Subquery(
            change_backups.annotate(change_time=Coalesce('finish_time', 'created'))
            .values('change_time')[:1]),
        last_config_change_uuid=Subquery(change_backups.values('uuid')[:1]),
    )

    last_router_status_change = RouterStatus.objects.filter(last_status_change__isnull=False).order_by('-last_status_change').first()
    if last_router_status_change:
        last_status_change_timestamp = last_router_status_change.last_status_change.isoformat()
    else:
        last_status_change_timestamp = 0

    default_backup_profile, default_backup_profile_created = BackupProfile.objects.get_or_create(name='default')

    # A state from the status page: the list shows exactly the devices the card
    # counted, across every group, and says so
    state = request.GET.get('state')
    state_label = None
    if state:
        if state not in ROUTER_STATES:
            raise Http404
        state_label, narrow = ROUTER_STATES[state]
        router_list = narrow(router_list)

    filter_group = None
    if state:
        pass
    elif request.GET.get('filter_group'):
        if request.GET.get('filter_group') == 'all':
            pass
        else:
            filter_group = get_object_or_404(RouterGroup, uuid=request.GET.get('filter_group'))
            router_list = router_list.filter(routergroup=filter_group)

    if not state and not filter_group and request.GET.get('filter_group') != 'all':
        filter_group = RouterGroup.objects.filter(default_group=True).first()
    # Parse the router_visible_columns cookie
    visible_columns = []
    if 'router_visible_columns' in request.COOKIES:
        try:
            visible_columns = json.loads(unquote(request.COOKIES['router_visible_columns']))
        except json.JSONDecodeError:
            # If the cookie is invalid, use default columns
            visible_columns = ["name", "type", "status", "backup", "groups"]
    else:
        # Default columns if cookie doesn't exist
        visible_columns = ["name", "type", "status", "backup", "groups"]

    context = {
        'router_list': router_list,
        'page_title': 'Router List',
        'filter_group_list': RouterGroup.objects.all().order_by('name'),
        'filter_group': filter_group,
        'state_label': state_label,
        'last_status_change_timestamp': last_status_change_timestamp,
        'visible_columns': visible_columns,
        # Not every user has a UserAcl row, the first user created has none
        'address_link': UserAcl.objects.filter(user=request.user).values_list('address_link', flat=True).first() or 'none',
        'can_execute_commands': UserAcl.objects.filter(user=request.user, user_level__gte=40).exists(),
        'can_open_console': UserAcl.objects.filter(user=request.user, user_level__gte=settings.CONSOLE_LEVEL).exists(),
        'can_backup': UserAcl.objects.filter(user=request.user, user_level__gte=20).exists(),
        'can_edit': UserAcl.objects.filter(user=request.user, user_level__gte=30).exists(),
        'can_update_firmware': UserAcl.objects.filter(user=request.user, user_level__gte=40).exists(),
    }
    return render(request, 'router_manager/router_list.html', context=context)


@login_required()
def view_router_availability(request):
    router = get_object_or_404(Router, uuid=request.GET.get('uuid'))
    data = {
        'router': router,
        'downtime_list': router.routerdowntime_set.all().order_by('-start_time'),
    }
    return render(request, 'router_manager/router_availability.html', context=data)


@login_required()
def view_test_router_connection(request):
    """The connection test, on request: with the values of the router form
    (nothing is saved), or with a saved router's own settings (stored=1)."""
    from routerlib.functions import test_authentication
    if not UserAcl.objects.filter(user=request.user, user_level__gte=30).exists():
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)
    router = get_object_or_404(Router, uuid=request.POST['uuid']) if request.POST.get('uuid') else None
    if router and request.POST.get('stored'):
        settings_ = {'router_type': router.router_type, 'address': router.address, 'port': router.port,
                     'username': router.username, 'password': router.password, 'ssh_key': router.ssh_key,
                     'enable_password': router.enable_password}
    else:
        # Read before the form: validating copies the typed values onto the
        # instance, and the stored password must be compared with what is stored
        stored_address, stored_port, stored_password = (router.address, router.port, router.password) if router else (None, None, '')
        stored_enable = router.enable_password if router else None
        form = RouterForm(request.POST, instance=router)
        if not form.is_valid():
            errors = [e for field_errors in form.errors.values() for e in field_errors]
            return JsonResponse({'success': False, 'message': errors[0] if errors else 'Check the form.'})
        data = form.cleaned_data
        password = request.POST.get('password') or ''
        if not password and not data.get('ssh_key') and router:
            # The stored password is only sent to the address it was stored for
            if (data['address'], data['port']) != (stored_address, stored_port):
                return JsonResponse({'success': False, 'message': 'Type the password to test a new address or port: '
                                                                  'the stored one is only used for the stored address.'})
            password = stored_password
        enable_password = request.POST.get('enable_password') or ''
        if not enable_password and router and stored_enable:
            # Like the password: only to the address it was stored for
            if (data['address'], data['port']) == (stored_address, stored_port):
                enable_password = stored_enable
        settings_ = {'router_type': data['router_type'], 'address': data['address'], 'port': data['port'],
                     'username': data['username'], 'password': password, 'ssh_key': data.get('ssh_key'),
                     'enable_password': enable_password or None}
    from router_manager import device_types
    chosen = device_types.get(settings_['router_type'])
    if chosen is None or not chosen.managed:
        return JsonResponse({'success': False, 'message': 'A monitoring-only device is not connected to over SSH.'})
    success, message = test_authentication(settings_['router_type'], settings_['address'], settings_['port'],
                                           settings_['username'], settings_['password'], settings_['ssh_key'],
                                           settings_['enable_password'])
    if not success:
        message = 'Could not authenticate: ' + (message or 'check the credentials.')
    return JsonResponse({'success': success, 'message': message})


@login_required()
def view_router_details(request):
    router = get_object_or_404(Router, uuid=request.GET.get('uuid'))
    router_status, router_status_created = RouterStatus.objects.get_or_create(router=router)
    router_backup_list = router.routerbackup_set.all().order_by('-created')
    router_information = RouterInformation.objects.filter(router=router).first()
    downtime_last_week = router.routerdowntime_set.filter(start_time__gte=timezone.now() - timezone.timedelta(days=7)).aggregate(total=Sum('total_down_time'))['total']
    if downtime_last_week is None:
        downtime_last_week = 0
    total_last_week = 7 * 24 * 60 * 60  # total seconds in a week
    last_week_availability = round((total_last_week - downtime_last_week) / total_last_week * 100, 3)
    if downtime_last_week > 0 and last_week_availability == 100:
        last_week_availability = 99.999

    if router_status.backup_lock:
        if not router_backup_list.filter(success=False, error=False).exists():
            router_status.backup_lock = None
            router_status.save()
            messages.warning(request, 'Backup lock removed|Backup lock was removed as there are no active backup tasks')

    live_reasons = []
    if router_status.backup_lock:
        live_reasons.append('backup in progress')
    pending_tasks = router.commandtask_set.filter(status='pending').count()
    if router_status.command_lock or pending_tasks:
        live_reasons.append(f'{pending_tasks or 1} command task{"s" if pending_tasks > 1 else ""} running')
    if router_information and not router_information.success and not router_information.error:
        live_reasons.append('reading device information')
    context = {
        'router': router,
        'host_key': host_keys.stored_key(router.address, router.port),
        'router_information': router_information,
        'router_status': router_status,
        'router_backup_list': router_backup_list,
        'page_title': 'Router Details',
        'offline_time_last_week': downtime_last_week,
        'last_week_availability': last_week_availability,
        'command_task_list': router.commandtask_set.all().order_by('-created')[:25],
        # Live while a backup, a command or an information read is under way
        'live_active': bool(live_reasons),
        'live_reason': ', '.join(live_reasons),
        'can_execute_commands': UserAcl.objects.filter(user=request.user, user_level__gte=40).exists(),
        'can_edit_routers': UserAcl.objects.filter(user=request.user, user_level__gte=30).exists(),
        'page': router_page(router, router_status, router_information, last_week_availability,
                            UserAcl.objects.filter(user=request.user, user_level__gte=40).exists(),
                            can_console=UserAcl.objects.filter(user=request.user, user_level__gte=settings.CONSOLE_LEVEL).exists(),
                            can_update_firmware=UserAcl.objects.filter(user=request.user, user_level__gte=40).exists()),
    }


    return render(request, 'router_manager/router_details.html', context=context)


def saved_router_message(router, created, changed):
    """What the save means for the device, in the words of what was changed:
    monitoring starting is only announced when it will start."""
    title = 'Router added' if created else 'Router saved'
    watched = router.enabled and router.monitoring
    if not created and 'enabled' in changed:
        if not router.enabled:
            return 'Router disabled|No backups, information updates or monitoring until it is enabled again.'
        title = 'Router enabled'
    if not created and 'monitoring' in changed and not router.monitoring:
        return f'{title}|Monitoring is off for this router.'
    if watched and (created or 'monitoring' in changed or 'enabled' in changed or 'address' in changed):
        return f'{title}|Monitoring starts within a few minutes.'
    if created and not watched:
        return f'{title}|Monitoring is off for this router.'
    return title


@login_required()
def view_manage_router(request):
    if not UserAcl.objects.filter(user=request.user).filter(user_level__gte=30).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    webadmin_settings, webadmin_settings_created = WebadminSettings.objects.get_or_create(name='webadmin_settings')

    if request.GET.get('uuid'):
        router = get_object_or_404(Router, uuid=request.GET.get('uuid'))
        if posted_action(request, 'delete'):
            if posted_confirmation(request) == 'delete':
                router.delete()
                messages.success(request, 'Router deleted successfully')
                webadmin_settings.router_config_last_updated = timezone.now()
                webadmin_settings.save()
                return redirect('router_list')
            else:
                messages.warning(request, 'Router not deleted|Invalid confirmation')
                return redirect('router_list')
        elif posted_action(request, 'forget_host_key'):
            if host_keys.forget(router.address, router.port):
                messages.success(request, 'Host key forgotten|The key the device presents on the next connection will be trusted')
            return redirect('/router/details/?uuid=' + str(router.uuid))
        elif posted_action(request, 'refresh_information'):
            router_information, created = RouterInformation.objects.get_or_create(router=router)
            router_information.next_retry = None
            router_information.retry_count = 0
            router_information.success = False
            router_information.error = False
            router_information.error_message = ''
            router_information.save()
            # Run the update right away instead of waiting for the cron task
            success, error_message = update_router_information(router_information)
            if success:
                messages.success(request, 'Router information updated')
            else:
                # The message is shown as a "title|body" toast, so the body must not contain a pipe
                messages.warning(request, 'Router information update failed|' + (
                    error_message or 'See the router information panel for details.'
                ).replace('|', '/'))
            return redirect('/router/details/?uuid=' + str(router.uuid))
    else:
        router = None

    form = RouterForm(request.POST or None, instance=router)
    if form.is_valid():
        created = router is None
        changed = set(form.changed_data)
        form.save()
        messages.success(request, saved_router_message(form.instance, created, changed))
        router_status, router_status_created = RouterStatus.objects.get_or_create(router=form.instance)
        BackupSchedule.objects.filter(router=form.instance).delete()
        if not form.instance.is_managed:
            RouterInformation.objects.filter(router=form.instance).delete()
        webadmin_settings.router_config_last_updated = timezone.now()
        webadmin_settings.save()
        return redirect('router_list')

    context = {
        'form': form,
        'page_title': 'Manage Router',
        'instance': router
    }
    return render(request, 'generic_form.html', context=context)


@login_required()
def view_router_group_list(request):
    context = {
        'router_group_list': RouterGroup.objects.all().order_by('name'),
        'page_title': 'Router Group List',
    }
    return render(request, 'router_manager/router_group_list.html', context=context)


@login_required()
def view_manage_router_group(request):
    if not UserAcl.objects.filter(user=request.user).filter(user_level__gte=40).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    if request.GET.get('uuid'):
        router_group = get_object_or_404(RouterGroup, uuid=request.GET.get('uuid'))
        if posted_action(request, 'delete'):
            if posted_confirmation(request) == 'delete':
                router_group.delete()
                messages.success(request, 'Router Group deleted successfully')
                return redirect('router_group_list')
            else:
                messages.warning(request, 'Router Group not deleted|Invalid confirmation')
                return redirect('router_group_list')
    else:
        router_group = None

    form = RouterGroupForm(request.POST or None, instance=router_group)
    if form.is_valid():
        form.save()
        messages.success(request, 'Router Group saved successfully')
        return redirect('router_group_list')

    context = {
        'form': form,
        'page_title': 'Manage Router Group',
        'instance': router_group
    }
    return render(request, 'generic_form.html', context=context)


@login_required()
def view_ssh_key_list(request):
    context = {
        'sshkey_list': SSHKey.objects.all().order_by('name'),
        'page_title': 'SSH Key List',
    }
    return render(request, 'router_manager/sshkey_list.html', context=context)


@login_required()
def view_manage_sshkey(request):
    if not UserAcl.objects.filter(user=request.user).filter(user_level__gte=40).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    if request.GET.get('uuid'):
        sshkey = get_object_or_404(SSHKey, uuid=request.GET.get('uuid'))
        if posted_action(request, 'delete'):
            if posted_confirmation(request) == 'delete':
                sshkey.delete()
                messages.success(request, 'SSH Key deleted successfully')
                return redirect('ssh_keys_list')
            else:
                messages.warning(request, 'SSH Key not deleted|Invalid confirmation')
                return redirect('ssh_keys_list')
    else:
        sshkey = None

    form = SSHKeyForm(request.POST or None, instance=sshkey)
    if form.is_valid():
        form.save()
        messages.success(request, 'SSH Key saved successfully')
        return redirect('ssh_keys_list')

    context = {
        'form': form,
        'page_title': 'Manage SSH Key',
        'instance': sshkey
    }
    return render(request, 'generic_form.html', context=context)


def create_instant_backup(router):
    if RouterBackup.objects.filter(router=router, success=False, error=False).exists():
        return 'Active router backup task already exists'

    if router.routerstatus.backup_lock:
        return 'Router backup is currently locked'

    if not router.backup_profile:
        return 'Router has no backup profile'

    router_backup = RouterBackup.objects.create(
        router=router,
        schedule_time=timezone.now(),
        schedule_type='instant'
    )

    router.routerstatus.backup_lock = router_backup.schedule_time
    router.routerstatus.save()

    return None


@login_required()
def view_create_instant_backup_task(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=20).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})

    router = get_object_or_404(Router, uuid=request.GET.get('uuid'))
    router_details_url = f'/router/details/?uuid={router.uuid}'
    if request.method != 'POST':
        # A link a foreign page could load must not start a backup
        messages.warning(request, 'Backup task not created | Use the Backup now button')
        return redirect(router_details_url)

    error = create_instant_backup(router)
    if error:
        messages.warning(request, f'Backup task not created | {error}')
    else:
        messages.success(request, f'Backup task created | {router.name}')

    # Back where the button was - the router list's quick action - but only to
    # a page of this site
    back = request.POST.get('next', '')
    if back.startswith('/router/list/') and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        return redirect(back)
    return redirect(router_details_url)


@login_required
def view_create_instant_backup_multiple_routers(request):
    if request.method == 'POST':
        if not UserAcl.objects.filter(user=request.user, user_level__gte=20).exists():
            return JsonResponse({'error': 'Permission denied.'}, status=403)

        uuids = request.POST.getlist('routers[]')
        if not uuids:
            return JsonResponse({'error': 'No routers selected.'}, status=400)

        results = []
        for uuid in uuids:
            router = get_object_or_404(Router, uuid=uuid)
            error = create_instant_backup(router)
            results.append({'router': router.name, 'status': error})

        return JsonResponse({'results': results})

    return JsonResponse({'error': 'Invalid request method.'}, status=405)


@login_required
def view_enable_routers_multiple(request):
    """Enable or disable the selected routers. A disabled router is neither
    backed up nor monitored; Router.save closes its open outage."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method.'}, status=405)
    if not UserAcl.objects.filter(user=request.user, user_level__gte=30).exists():
        return JsonResponse({'error': 'Permission denied.'}, status=403)
    action = request.POST.get('action')
    if action not in ('enable', 'disable'):
        return JsonResponse({'error': 'Unknown action.'}, status=400)
    uuids = request.POST.getlist('routers[]')
    if not uuids:
        return JsonResponse({'error': 'No routers selected.'}, status=400)
    enabled = action == 'enable'
    changed = 0
    for router in Router.objects.filter(uuid__in=uuids).exclude(enabled=enabled):
        router.enabled = enabled
        router.save(update_fields=['enabled', 'updated'])   # records the change and its time
        changed += 1
    return JsonResponse({'changed': changed, 'enabled': enabled})


@login_required
def view_update_routers_information_multiple(request):
    """
    Queue an information update for the selected routers.

    The update itself is done by the update_router_information cron task, which
    picks requested routers up before its regular refresh queue.
    """
    if request.method == 'POST':
        if not UserAcl.objects.filter(user=request.user, user_level__gte=30).exists():
            return JsonResponse({'error': 'Permission denied.'}, status=403)

        uuids = request.POST.getlist('routers[]')
        if not uuids:
            return JsonResponse({'error': 'No routers selected.'}, status=400)

        requested = 0
        for router in Router.objects.filter(uuid__in=uuids):
            router_information, created = RouterInformation.objects.get_or_create(router=router)
            router_information.update_requested = True
            router_information.retry_count = 0
            router_information.error = False
            router_information.error_message = ''
            router_information.save()
            requested += 1

        return JsonResponse({'requested': requested})

    return JsonResponse({'error': 'Invalid request method.'}, status=405)


@login_required
def view_edit_routers_multiple(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=30).exists():
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
            return redirect('edit_router_multiple')

        form = RouterBulkEditForm(request.POST)
        router_uuids = request.POST.getlist('router_uuids')
        if form.is_valid():
            routers = Router.objects.filter(uuid__in=router_uuids)
            ssh_key_uuid = form.cleaned_data.get('ssh_key')
            backup_profile_uuid = form.cleaned_data.get('backup_profile')
            ssh_key = None
            backup_profile = None
            if ssh_key_uuid and ssh_key_uuid != 'clear':
                ssh_key = get_object_or_404(SSHKey, uuid=ssh_key_uuid)
            if backup_profile_uuid and backup_profile_uuid != 'clear':
                backup_profile = get_object_or_404(BackupProfile, uuid=backup_profile_uuid)

            updated_count = 0
            # One entry for the whole edit instead of one per router: the form says
            # what to set, and the number of devices it reached is the interesting
            # part of it
            with collect('Bulk edit of routers'):
                for router in routers:
                    if form.cleaned_data.get('port'):
                        router.port = form.cleaned_data['port']
                    if form.cleaned_data.get('username'):
                        router.username = form.cleaned_data['username']
                    if form.cleaned_data.get('monitoring'):
                        router.monitoring = form.cleaned_data['monitoring'] == 'true'
                    if form.cleaned_data.get('enabled'):
                        router.enabled = form.cleaned_data['enabled'] == 'true'
                    if backup_profile_uuid:
                        router.backup_profile = backup_profile
                    if form.cleaned_data.get('internal_notes'):
                        router.internal_notes = form.cleaned_data['internal_notes']
                    if router.is_managed:
                        if form.cleaned_data.get('password'):
                            router.password = form.cleaned_data['password']
                            router.ssh_key = None
                        if ssh_key_uuid:
                            if ssh_key_uuid == 'clear':
                                router.ssh_key = None
                            else:
                                router.ssh_key = ssh_key
                            if not form.cleaned_data.get('password'):
                                router.password = ''
                    router.save()
                    updated_count += 1

            request.session.pop('router_selection', None)
            webadmin_settings, webadmin_settings_created = WebadminSettings.objects.get_or_create(name='webadmin_settings')
            webadmin_settings.router_config_last_updated = timezone.now()
            webadmin_settings.save()
            messages.success(request, f'{updated_count} routers updated successfully')
            return redirect('router_list')
    else:
        form = RouterBulkEditForm()

    router_uuids = request.session.get('router_selection') or request.GET.getlist('routers[]')
    if not router_uuids:
        messages.warning(request, 'No routers selected')
        return redirect('router_list')

    routers = Router.objects.filter(uuid__in=router_uuids)
    context = {
        'routers': routers,
        'form': form,
        'page_title': 'Edit Multiple Routers',
    }
    return render(request, 'router_manager/edit_routers_multiple.html', context=context)


@login_required
def view_manage_router_groups_multiple(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=20).exists():
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
            return redirect('manage_router_groups_multiple')

        router_uuids = request.POST.getlist('router_uuids')
        add_group_uuid = request.POST.get('add_group')
        remove_group_uuid = request.POST.get('remove_group')

        if not router_uuids:
            messages.warning(request, 'No routers selected')
            return redirect('router_list')

        # Validate that the same group is not selected for both add and remove
        if add_group_uuid and remove_group_uuid and add_group_uuid == remove_group_uuid:
            messages.warning(request, 'Cannot add and remove from the same group')
            return redirect('router_list')

        routers = Router.objects.filter(uuid__in=router_uuids)

        # Process add to group
        if add_group_uuid:
            try:
                group = RouterGroup.objects.get(uuid=add_group_uuid)
                # One entry naming the group and the number of routers, rather than
                # one membership line each
                with collect(f'Added routers to group {group.name}'):
                    for router in routers:
                        group.routers.add(router)
                messages.success(request, f'Added {routers.count()} router(s) to group {group.name}')
            except RouterGroup.DoesNotExist:
                messages.error(request, 'Group not found')

        # Process remove from group
        if remove_group_uuid:
            try:
                group = RouterGroup.objects.get(uuid=remove_group_uuid)
                with collect(f'Removed routers from group {group.name}'):
                    for router in routers:
                        group.routers.remove(router)
                messages.success(request, f'Removed {routers.count()} router(s) from group {group.name}')
            except RouterGroup.DoesNotExist:
                messages.error(request, 'Group not found')

        request.session.pop('router_selection', None)
        return redirect('router_list')

    # GET request - display form
    router_uuids = request.session.get('router_selection') or request.GET.getlist('routers[]')
    if not router_uuids:
        messages.warning(request, 'No routers selected')
        return redirect('router_list')

    routers = Router.objects.filter(uuid__in=router_uuids)
    groups = RouterGroup.objects.all().order_by('name')

    context = {
        'routers': routers,
        'groups': groups,
        'page_title': 'Manage Router Groups',
    }

    return render(request, 'router_manager/manage_router_groups.html', context)


UNCHECKED_RETRY = timezone.timedelta(hours=1)


def _router_due_for_information_update(router_list, refresh_interval):
    """Next router that needs an information update, requested ones first."""
    router = router_list.filter(routerinformation__update_requested=True).first()
    if not router:
        router = router_list.filter(routerinformation__isnull=True).first()
    if not router:
        router = router_list.filter(routerinformation__next_retry__lt=timezone.now()).first()
    if not router:
        router = router_list.filter(routerinformation__last_retrieval__isnull=True).first()
    if not router:
        router = router_list.filter(routerinformation__last_retrieval__lt=timezone.now() - timezone.timedelta(hours=refresh_interval)).first()
    if not router:
        # An update check that gave no version (the device could not reach its
        # vendor, or answered too slowly) is tried again after an hour, not a day
        from router_manager.models import DeviceType
        checked = [t.slug for t in DeviceType.objects.all() if t.checks]
        router = router_list.filter(router_type__in=checked, routerinformation__last_retrieval__lt=timezone.now() - UNCHECKED_RETRY) \
            .filter(Q(routerinformation__available_version__isnull=True) | Q(routerinformation__available_version='')).first()
    return router


@cron_endpoint
def view_cron_update_router_information(request):
    data = {'status': 'success', 'updated': 0, 'failed': 0}
    refresh_interval = 24 #hours
    # Work through the queue until the next cron run is due, so that a batch of
    # requested routers is not spread over one minute per router
    deadline = time.time() + MAX_UPDATE_INFORMATION_RUNTIME

    from router_manager.models import DeviceType
    unmanaged = DeviceType.objects.filter(managed=False).values_list('slug', flat=True)
    router_list = Router.objects.filter(enabled=True).exclude(router_type__in=list(unmanaged)).exclude(routerstatus__status_online=False)

    while time.time() < deadline:
        router = _router_due_for_information_update(router_list, refresh_interval)
        if not router:
            break
        router_information, created = RouterInformation.objects.get_or_create(router=router)
        if router_information.update_requested:
            # Clear the request before running, so a crash does not leave it pending forever
            router_information.update_requested = False
            router_information.save(update_fields=['update_requested'])
        success, error_message = update_router_information(router_information)
        if success:
            data['updated'] += 1
        else:
            data['failed'] += 1

    if not data['updated'] and not data['failed']:
        data['message'] = 'No routers need update'

    return JsonResponse(data)
