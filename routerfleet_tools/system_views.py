"""System Settings: the hub, notifications and their channels."""
from django.contrib import messages
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
    from .models import HttpsSettings
    return render(request, 'routerfleet_tools/system.html', {
        'page_title': 'System settings', 'is_administrator': user_level >= ADMINISTRATOR,
        'https': HttpsSettings.load(), 'smtp': SmtpSettings.load(),
        'channels': MessageChannel.objects.filter(user__isnull=True, enabled=True).count()})


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
