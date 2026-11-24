"""Email notifications through the installation's SMTP server.

Subjects and sender names are single lines (a line break in a header would let
a device name add headers of its own); addresses are validated; the password is
taken out of any error text before it is stored or shown.
"""
from email.utils import formataddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email

TIMEOUT = 15


class Delivered:
    """What channels.send returns for an email: the shape the queue expects."""
    status_code = 200
    text = ''


def one_line(text, limit):
    return ' '.join(str(text or '').split())[:limit]


def connection(smtp):
    return get_connection(
        backend=getattr(settings, 'ROUTERFLEET_EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend'),
        host=smtp.host, port=smtp.port, username=smtp.username or None, password=smtp.password or None,
        use_tls=smtp.security == 'starttls', use_ssl=smtp.security == 'ssl', timeout=TIMEOUT, fail_silently=False)


def send(to, subject, text, smtp=None):
    """Sends one message; raises ValueError for a setting or an address that can
    not be used, and whatever smtplib raises for the server."""
    from .models import SmtpSettings
    smtp = smtp or SmtpSettings.load()
    if not smtp.ready:
        raise ValueError('Email is not set up: an administrator configures it in System Settings, Notifications.')
    try:
        validate_email(to)
        validate_email(smtp.sender_address)
    except ValidationError:
        raise ValueError('The recipient or the sender address is not a valid email address.') from None
    message = EmailMessage(
        subject=one_line(subject, 150) or 'RouterFleet Improved',
        body=text, from_email=formataddr((one_line(smtp.sender_name, 100), smtp.sender_address)), to=[to],
        connection=connection(smtp))
    message.send()
    return Delivered()


def check(smtp):
    """Connects to the server with these settings and logs in (when a login
    is set), then disconnects: raises what the server or the network says."""
    smtp_connection = connection(smtp)
    smtp_connection.open()
    smtp_connection.close()


def scrub(text, smtp):
    text = str(text)
    if smtp and smtp.password:
        text = text.replace(smtp.password, '***')
    return text


# What a user can receive by email, in the order My Settings shows it:
# (channel field, label, explanation)
PERSONAL_EVENTS = (
    ('backup_fail', 'Backup failed', 'Grouped: one message for the failures of a few minutes.'),
    ('backup_success', 'Backup succeeded', 'Grouped the same way, so a night of backups is one message.'),
    ('daily_backup_report', 'Daily backup report', 'The digest of the last 24 hours, at the report time.'),
    ('daily_status_report', 'Daily status report', 'Devices online and offline, at the report time.'),
    ('status_change_offline', 'Device offline', 'When a monitored device stops answering.'),
    ('status_change_online', 'Device back online', 'When it answers again.'),
    ('command_failed', 'Fleet Commander job with failures', 'When a job finishes with failed tasks.'),
    ('firmware_update', 'Firmware update finished', 'When a device is back online on its new version - or the update failed.'),
)


def personal_channel(user):
    from .models import MessageChannel
    return MessageChannel.objects.filter(user=user).first()


def save_personal_channel(user, address, events):
    """The user's own email channel, made to match My Settings: enabled only
    with an address and at least one event."""
    from .models import MessageChannel
    channel = personal_channel(user)
    if channel is None:
        name = f'Email: {user.username}'[:100]
        if MessageChannel.objects.filter(name=name).exists():
            name = f'Email: {user.username} (personal)'[:100]
        channel = MessageChannel(name=name, channel_type='email', user=user)
        for field, _, _ in PERSONAL_EVENTS:
            setattr(channel, field, False)
    for field, _, _ in PERSONAL_EVENTS:
        setattr(channel, field, field in events)
    channel.enabled = bool(address) and bool(events)
    channel.save()
    return channel
