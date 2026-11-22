"""System Settings: the hub, notifications and their channels."""
from django.conf import settings as django_settings
from django.contrib import messages
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from message_center import email
from message_center.forms import MessageChannelForm
from message_center.models import MessageChannel, SmtpSettings
from routerlib.actions import posted_action, posted_confirmation
from user_manager.models import UserAcl

CONFIGURATION_MANAGER = 40
ADMINISTRATOR = 50
EVENTS = (('backup_fail', 'Backup failed'), ('backup_success', 'Backup succeeded'),
          ('daily_backup_report', 'Daily backup report'), ('daily_status_report', 'Daily status report'),
          ('status_change_offline', 'Device offline'), ('status_change_online', 'Device back online'),
          ('command_failed', 'Fleet Commander job with failures'))


def level(user):
    acl = UserAcl.objects.filter(user=user).first()
    return acl.user_level if acl else 0


@login_required
def view_system(request):
    user_level = level(request.user)
    if user_level < CONFIGURATION_MANAGER:
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    from .models import HttpsSettings, DisplaySettings
    from router_manager.models import DeviceType
    return render(request, 'routerfleet_tools/system.html', {
        'page_title': 'System settings', 'is_administrator': user_level >= ADMINISTRATOR,
        'https': HttpsSettings.load(), 'smtp': SmtpSettings.load(), 'display': DisplaySettings.load(),
        'current': django_settings.ROUTERFLEET_RELEASE, 'update_available': _available(),
        'can_firmware': user_level >= CONFIGURATION_MANAGER,
        'device_type_count': DeviceType.objects.count(),
        'channels': MessageChannel.objects.filter(user__isnull=True, enabled=True).count()})


@login_required
def view_display(request):
    if level(request.user) < CONFIGURATION_MANAGER:
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    from .models import DisplaySettings
    display = DisplaySettings.load()
    if request.method == 'POST':
        try:
            size = int(request.POST.get('default_page_size', ''))
        except ValueError:
            size = None
        if size in DisplaySettings.PAGE_SIZES:
            display.default_page_size = size
            display.save()
            messages.success(request, f'Tables now open with {size} rows')
            return redirect('/system/display/')
        messages.error(request, 'Choose one of the sizes offered')
    return render(request, 'routerfleet_tools/display.html', {
        'page_title': 'Display', 'display': display, 'sizes': DisplaySettings.PAGE_SIZES})



def _available():
    from routerfleet.versioning import from_number
    from .models import WebadminSettings
    webadmin = WebadminSettings.objects.filter(name='webadmin_settings').first()
    if webadmin and webadmin.update_available and webadmin.latest_version > django_settings.ROUTERFLEET_VERSION:
        return from_number(webadmin.latest_version)
    return None


def summary(channel):
    chosen = [label for field, label in EVENTS if getattr(channel, field)]
    return ', '.join(chosen) if chosen else 'Nothing chosen'


@login_required
def view_notifications(request):
    user_level = level(request.user)
    if user_level < CONFIGURATION_MANAGER:
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    shared = MessageChannel.objects.filter(user__isnull=True).order_by('channel_type', 'name')
    return render(request, 'routerfleet_tools/notifications.html', {
        'page_title': 'Notifications', 'is_administrator': user_level >= ADMINISTRATOR, 'smtp': SmtpSettings.load(),
        'channels': [(c, summary(c)) for c in shared.exclude(channel_type='callmebot')],
        'removed': shared.filter(channel_type='callmebot'),
        'personal': MessageChannel.objects.filter(user__isnull=False, enabled=True).count()})


@login_required
def view_notification_channel(request):
    if level(request.user) < CONFIGURATION_MANAGER:
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    channel = None
    if request.GET.get('uuid'):
        channel = get_object_or_404(MessageChannel, uuid=request.GET.get('uuid'), user__isnull=True)
        if posted_action(request, 'delete'):
            if posted_confirmation(request) == 'delete':
                channel.delete()
                messages.success(request, 'Channel deleted')
            else:
                messages.warning(request, 'Channel not deleted|Invalid confirmation')
            return redirect('/system/notifications/')
    data = None
    if request.method == 'POST':
        data = request.POST.copy()
        # A stored token is never shown again: empty keeps it
        if channel and not data.get('token') and channel.token and data.get('channel_type') == channel.channel_type:
            data['token'] = channel.token
    form = MessageChannelForm(data, instance=channel)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        messages.success(request, f'Channel saved|{saved.name}: ' + ('a test message was sent.' if saved.enabled else 'switched off.'))
        return redirect('/system/notifications/')
    if data is not None:
        states = [(f, label, data.get(f) == 'on') for f, label in EVENTS]
    else:
        source = channel or MessageChannel()   # a new channel: the model's defaults
        states = [(f, label, bool(getattr(source, f))) for f, label in EVENTS]
    return render(request, 'routerfleet_tools/notification_channel.html', {
        'page_title': channel.name if channel else 'New channel', 'channel': channel, 'form': form,
        'event_states': states,
        'values': data or {}, 'has_token': bool(channel and channel.token), 'smtp_ready': SmtpSettings.load().ready,
        'removed': bool(channel and channel.channel_type == 'callmebot'), 'email': email})


@login_required
def view_updates(request):
    """Updates: what runs, what is available, and - with the updater set up -
    approving the update, confirmed with the administrator's password."""
    if level(request.user) < ADMINISTRATOR:
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    from django.conf import settings
    from routerfleet.versioning import from_number
    from . import updates
    from .models import UpdateRun, WebadminSettings
    from .views import check_enabled
    webadmin = WebadminSettings.objects.filter(name='webadmin_settings').first()
    current = settings.ROUTERFLEET_RELEASE
    available = None
    if webadmin and webadmin.update_available and webadmin.latest_version > settings.ROUTERFLEET_VERSION:
        available = from_number(webadmin.latest_version)
    updater = updates.updater()
    progress = updates.status()
    last = UpdateRun.objects.first()
    # What the updater reported about the last request
    if last and progress and progress['id'] == last.request_id and progress['state'] in dict(UpdateRun.STATES) \
            and (progress['state'], progress['message']) != (last.state, last.message):
        last.state, last.message = progress['state'], progress['message']
        last.save(update_fields=['state', 'message', 'updated'])
    if last and last.state == 'requested' and timezone.now() - last.created > timezone.timedelta(minutes=10):
        last.state, last.message = 'failed', 'The updater did not pick the request up within 10 minutes.'
        last.save(update_fields=['state', 'message', 'updated'])
    busy = bool(last and last.active and timezone.now() - last.updated < timezone.timedelta(minutes=30))

    if request.method == 'POST' and request.POST.get('action') == 'check':
        from .views import check_now
        if webadmin is None:
            webadmin = WebadminSettings.objects.create(name='webadmin_settings')
        done, message = check_now(webadmin)
        if not done:
            messages.warning(request, 'Not checked|' + message.replace('|', '/'))
        else:
            from routerfleet.versioning import from_number
            if webadmin.update_available and webadmin.latest_version > settings.ROUTERFLEET_VERSION:
                messages.success(request, f'Update available|{from_number(webadmin.latest_version)} can be installed.')
            else:
                messages.success(request, f'Up to date|{current} is the newest release.')
        return redirect('/system/updates/')

    if request.method == 'POST' and request.POST.get('action') == 'update':
        error = None
        if not available or request.POST.get('version') != available:
            error = 'That release is not the one available any more. Reload the page.'
        elif not updater['ready']:
            error = 'The updater is not ready.'
        elif busy:
            error = 'An update is already under way.'
        elif not request.user.check_password(request.POST.get('password', '')):
            error = 'The password is not correct.'
        if error:
            messages.error(request, 'Update not started|' + error)
            return redirect('/system/updates/')
        try:
            request_id = updates.request(available, request.user.username)
        except (OSError, ValueError):
            messages.error(request, 'Update not started|The request could not be written for the updater.')
            return redirect('/system/updates/')
        UpdateRun.objects.create(request_id=request_id, from_version=current, to_version=available,
                                 approved_by=request.user, approved_by_name=request.user.username)
        messages.success(request, f'Update to {available} approved|The updater starts within a minute.')
        return redirect('/system/updates/')

    return render(request, 'routerfleet_tools/updates.html', {
        'page_title': 'Updates', 'current': current, 'available': available, 'updater': updater,
        'last': last, 'busy': busy, 'runs': UpdateRun.objects.all()[:10],
        'last_checked': webadmin.last_checked if webadmin else None,
        'check_enabled': check_enabled()})
