import datetime
import re

from router_manager.models import Router
from .models import MessageChannel, Notification, MessageSettings, Message
from backup_data.models import RouterBackup
from . import channels
from .email import scrub as email_scrub
from django.utils import timezone


# An address in a stored error: a mail server refusing a recipient names it, and
# Message Center is read by every operator, not only the person it belongs to
EMAIL_ADDRESS = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def without_addresses(text):
    return EMAIL_ADDRESS.sub('[address]', str(text))


def concatenate_notifications(notification_type: str):
    concatenate_count = 0
    message_settings, _ = MessageSettings.objects.get_or_create(name='message_settings')
    message_channel_list = MessageChannel.objects.filter(enabled=True)
    if notification_type == 'status_online':
        notification_text = 'Device status change: Online\n'
        notification_list = Notification.objects.filter(notification_type='status_online')
        message_channel_list = message_channel_list.filter(status_change_online=True)
    elif notification_type == 'status_offline':
        notification_text = 'Device status change: Offline\n'
        notification_list = Notification.objects.filter(notification_type='status_offline')
        message_channel_list = message_channel_list.filter(status_change_offline=True)
    elif notification_type == 'backup_fail':
        notification_text = 'Backup failed report\n'
        notification_list = Notification.objects.filter(notification_type='backup_fail')
        message_channel_list = message_channel_list.filter(backup_fail=True)
    elif notification_type == 'backup_success':
        notification_text = 'Successful backups\n'
        notification_list = Notification.objects.filter(notification_type='backup_success')
        message_channel_list = message_channel_list.filter(backup_success=True)
    else:
        return 0

    notification_list = notification_list.order_by('created')
    for notification in notification_list:
        notification_text_temp = notification_text
        if notification.router_backup:
            notification_text_temp += f'\n- Backup: {notification.router_backup.id} for device {notification.router_backup.router.name} '
        elif notification.router:
            notification_text_temp += f'\n- Device {notification.router.name} ({notification.router.address})'
        if len(notification_text_temp) < message_settings.max_length:
            notification_text = notification_text_temp
            notification.delete()
            concatenate_count += 1
        else:
            break

    for message_channel in message_channel_list:
        Message.objects.create(
            channel=message_channel,
            subject=notification_text.split('\n')[0],
            message=notification_text
        )
    return concatenate_count


def generate_backup_report(data):
    message_settings, _ = MessageSettings.objects.get_or_create(name='message_settings')
    yesterday = timezone.now() - datetime.timedelta(days=1)
    failed_backup_list = RouterBackup.objects.filter(error=True, updated__gt=yesterday)
    success_backup_list = RouterBackup.objects.filter(success=True, updated__gt=yesterday)
    pending_backup_list = RouterBackup.objects.filter(success=False, error=False, updated__gt=yesterday)
    if data['report_time_exception']:
        message_text = 'Warning: Error calculating report time, please check your timezone settings.\n\n'
    else:
        message_text = ''
    message_text += 'Routerfleet Daily backup report:\n'
    message_text += f'Backups completed: {success_backup_list.count()}\n'
    message_text += f'Backups failed: {failed_backup_list.count()}\n'
    message_text += f'Backups pending: {pending_backup_list.count()}\n'

    if failed_backup_list.count() > 0:
        message_text += '\n=========\n Failed backups:\n'
        message_text_temp = message_text
        truncate_text = 'There are more failed backups, please check the web interface for the full list\n\n'
        for backup in failed_backup_list:
            message_text_temp += f'- Backup {backup.id} for device {backup.router.name} ({backup.router.address})\n'
            if len(message_text_temp + truncate_text) < message_settings.max_length:
                message_text = message_text_temp
            else:
                message_text += truncate_text
                break
    for message_channel in MessageChannel.objects.filter(enabled=True, daily_backup_report=True):
        Message.objects.create(
            channel=message_channel,
            subject='Daily backup report',
            message=message_text
        )
    message_settings.last_daily_backup_report = timezone.now()
    message_settings.save()
    return


def generate_status_report(data):
    message_settings, _ = MessageSettings.objects.get_or_create(name='message_settings')
    router_list = Router.objects.filter(enabled=True, monitoring=True)
    offline_count = router_list.filter(routerstatus__status_online=False).count()
    if data['report_time_exception']:
        message_text = 'Warning: Error calculating report time, please check your timezone settings.\n\n'
    else:
        message_text = ''
    message_text += 'Routerfleet Daily status report:\n'
    message_text += f'Monitored devices: {router_list.count()}\n'
    message_text += f'Online: {router_list.filter(routerstatus__status_online=True).count()}\n'
    message_text += f'Offline: {offline_count}\n'

    if offline_count > 0:
        message_text += '\n=========\n Offline devices:\n'
        message_text_temp = message_text
        truncate_text = 'There are more offline devices, please check the web interface for the full list\n\n'
        for router in router_list.filter(routerstatus__status_online=False):
            message_text_temp += f'- {router.name} ({router.address})\n'
            if len(message_text_temp + truncate_text) < message_settings.max_length:
                message_text = message_text_temp
            else:
                message_text += truncate_text
                break

    message_channel_list = MessageChannel.objects.filter(enabled=True, daily_status_report=True)
    for message_channel in message_channel_list:
        Message.objects.create(
            channel=message_channel,
            subject='Daily status report',
            message=message_text
        )
    message_settings.last_daily_status_report = timezone.now()
    message_settings.save()
    return


def send_notification_message(message: Message):
    message_settings, _ = MessageSettings.objects.get_or_create(name='message_settings')
    if message.status != 'pending':
        return
    if message.retry_count > message_settings.max_retry:
        message.status = 'failed'
        message.completed = timezone.now()
        message.save()
        return

    message_response = {'status': 'pending', 'error_message': '', 'error_status_code': 0}

    channel = message.channel
    smtp = None
    try:
        destination = channel.email_address if channel.channel_type == 'email' else channel.destination
        if channel.channel_type == 'email':
            from .models import SmtpSettings
            smtp = SmtpSettings.load()
        response = channels.send(channel.channel_type, channel.token, destination,
                                 message.message, message.subject, server_url=channel.server_url,
                                 priority=channel.priority)
        if response.status_code == 200:
            message_response['status'] = 'sent'
        else:
            message_response['status'] = 'failed'
            message_response['error_message'] = channels.scrub(response.text, channel.token)
            message_response['error_status_code'] = response.status_code
    except ValueError as e:
        message_response['status'] = 'failed'
        message_response['error_message'] = (f'Failed to send message: {without_addresses(e)}' if channel.channel_type in ('email', 'callmebot', 'ntfy')
                                             else 'Failed to send message: the channel type, token or destination is not valid')
        message_response['error_status_code'] = 0
    except Exception as e:
        # The exception names the URL, which carries the token, or the SMTP
        # error, which may echo the login: never stored as is
        message_response['status'] = 'failed'
        message_response['error_message'] = 'Failed to send message: Request exception ' + \
            without_addresses(email_scrub(channels.scrub(e, channel.token), smtp))
        message_response['error_status_code'] = 0

    if message_response['status'] == 'sent':
        message.status = 'sent'
        message.completed = timezone.now()
        message.save()
    else:
        message.retry_count += 1
        message.error_message = message_response['error_message']
        message.error_status_code = message_response['error_status_code']
        message.next_retry = timezone.now() + datetime.timedelta(seconds=message_settings.retry_interval)
        message.save()
    return


def notify_router_status_update(router: Router):
    message_channel_list = MessageChannel.objects.filter(enabled=True)
    message_settings, _ = MessageSettings.objects.get_or_create(name='message_settings')

    if message_settings.concatenate_status_change:
        if router.routerstatus.status_online:
            if message_channel_list.filter(status_change_online=True):
                Notification.objects.create(notification_type='status_online', router=router)
        else:
            if message_channel_list.filter(status_change_offline=True):
                Notification.objects.create(notification_type='status_offline', router=router)
    else:
        if router.routerstatus.status_online:
            for message_channel in message_channel_list.filter(status_change_online=True):
                Message.objects.create(
                    channel=message_channel,
                    subject='Device status change: Online',
                    message=f'Device {router.name} ({router.address}) is now online'
                )
        else:
            for message_channel in message_channel_list.filter(status_change_offline=True):
                Message.objects.create(
                    channel=message_channel,
                    subject='Device status change: Offline',
                    message=f'Device {router.name} ({router.address}) is now offline'
                )
    return


def notify_backup_fail(router_backup: RouterBackup):
    message_settings, _ = MessageSettings.objects.get_or_create(name='message_settings')
    message_channel_list = MessageChannel.objects.filter(enabled=True, backup_fail=True)

    if not message_channel_list:
        return

    if message_settings.concatenate_backup_fails:
        Notification.objects.create(notification_type='backup_fail', router=router_backup.router, router_backup=router_backup)
    else:
        error_message = f'Backup {router_backup.id} failed for device {router_backup.router.name} ({router_backup.router.address})'
        if router_backup.error_message:
            error_message += f'\n\nError message: {router_backup.error_message}'
        for message_channel in message_channel_list:
            Message.objects.create(
                channel=message_channel,
                subject=f'Backup failed: {router_backup.id}',
                message=error_message
            )
    return

def notify_backup_task_lock_expired(router_backup: RouterBackup):
    message_settings, _ = MessageSettings.objects.get_or_create(name='message_settings')
    message_channel_list = MessageChannel.objects.filter(enabled=True)

    if not message_channel_list:
        return

    error_message = f'TASK LOCK EXPIRED: Backup task lock expired for backup {router_backup.id} of device {router_backup.router.name} ({router_backup.router.address}). This likely means that the backup task has been running for too long and has been marked as failed. Please check the device and the backup task for more details.'
    if router_backup.error_message:
        error_message += f'\n\nError message: {router_backup.error_message}'
    for message_channel in message_channel_list:
        Message.objects.create(
            channel=message_channel,
            subject=f'Backup lock expired: {router_backup.id}',
            message=error_message
        )
    return


def notify_backup_success(router_backup: RouterBackup):
    """A backup that succeeded, for the channels that asked: grouped like
    failures, so a night of backups is one message, not one per device."""
    message_channel_list = MessageChannel.objects.filter(enabled=True, backup_success=True)
    if not message_channel_list.exists():
        return
    message_settings, _ = MessageSettings.objects.get_or_create(name='message_settings')
    if message_settings.concatenate_backup_fails:
        Notification.objects.create(notification_type='backup_success', router=router_backup.router,
                                    router_backup=router_backup)
        return
    state = 'unchanged' if router_backup.same_as_id else ('configuration changed' if router_backup.config_change_detected
                                                          else 'stored')
    for message_channel in message_channel_list:
        Message.objects.create(channel=message_channel, subject=f'Backup succeeded: {router_backup.router.name}',
                               message=f'Backup {router_backup.id} of {router_backup.router.name} '
                                       f'({router_backup.router.address}) succeeded: {state}.')


def notify_firmware_update(target):
    """A device of a firmware rollout finished: back online on its new version,
    or the update failed - for the channels that asked."""
    channels = MessageChannel.objects.filter(enabled=True, firmware_update=True)
    if not channels.exists():
        return
    router = target.router
    if target.state == 'done':
        subject = f'Firmware updated: {router.name}'
        text = (f'{router.name} ({router.address}) is back online: {target.message}'
                f'{" Before: " + target.from_version + "." if target.from_version else ""}')
    else:
        subject = f'Firmware update failed: {router.name}'
        text = f'The update of {router.name} ({router.address}) failed: {target.message}'
    text += f'\n\nRollout: {target.rollout.label}.'
    for channel in channels:
        Message.objects.create(channel=channel, subject=subject, message=text)


def notify_command_job_failed(job):
    """A Fleet Commander job that finished with failed tasks."""
    message_channel_list = MessageChannel.objects.filter(enabled=True, command_failed=True)
    if not message_channel_list.exists():
        return
    failed = job.tasks.filter(status='error').select_related('router')[:20]
    lines = [f'Fleet Commander job #{job.id} ({job.command.name}) finished: {job.success_count} succeeded, '
             f'{job.error_count} failed, {job.aborted_count} aborted.', '']
    for task in failed:
        reason = (task.error_message or '').splitlines()[0][:120] if task.error_message else ''
        lines.append(f'- {task.router.name if task.router else task.router_name}{": " + reason if reason else ""}')
    for message_channel in message_channel_list:
        Message.objects.create(channel=message_channel, subject=f'Fleet Commander job #{job.id} had failures',
                               message='\n'.join(lines))
