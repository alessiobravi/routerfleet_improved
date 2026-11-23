from django.conf import settings
from django.db import models

from routerlib.encryption import EncryptedTextField
from router_manager.models import Router
from backup_data.models import RouterBackup
import uuid


class Notification(models.Model):
    notification_type = models.CharField(choices=(('status_online', 'Status change: Online'), ('status_offline', 'Status change: Offline'), ('backup_fail', 'Backup failed'), ('backup_success', 'Backup succeeded'), ('task_lock_expired', 'Task Lock Expired')), max_length=32)
    router = models.ForeignKey(Router, on_delete=models.CASCADE, blank=True, null=True)
    router_backup = models.ForeignKey(RouterBackup, on_delete=models.CASCADE, blank=True, null=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    uuid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)

    class Meta:
        indexes = [
            models.Index(fields=['notification_type', 'created'], name='notification_type_created'),
        ]


class MessageChannel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    enabled = models.BooleanField(default=True)
    channel_type = models.CharField(
        max_length=100, choices=(
            ('email', 'Email'), ('ntfy', 'ntfy'), ('telegram', 'Telegram'),
            # Removed: existing rows are kept, switched off, for their history
            ('callmebot', 'CallMeBot (removed)')
        )
    )
    # A user's own email channel, kept by My Settings: delivered to the user's
    # address. Shared channels (a team mailbox) have no user.
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='email_channel')
    destination = models.CharField(max_length=100, blank=True, null=True)
    # Encrypted at rest; reads return the token as written. Telegram: the bot
    # token; ntfy: an optional access token for a protected topic
    token = EncryptedTextField(blank=True, null=True)
    # ntfy: the server - the public one unless the installation runs its own
    server_url = models.CharField(max_length=255, default='https://ntfy.sh', blank=True)
    priority = models.PositiveSmallIntegerField(default=3)

    status_change_offline = models.BooleanField(default=True)
    status_change_online = models.BooleanField(default=True)
    backup_fail = models.BooleanField(default=True)
    daily_status_report = models.BooleanField(default=True)
    daily_backup_report = models.BooleanField(default=True)
    # Added later: off for every channel that existed, so an upgrade sends nothing new
    backup_success = models.BooleanField(default=False, verbose_name='Backup succeeded')
    command_failed = models.BooleanField(default=False, verbose_name='Fleet Commander job with failures')

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)

    def __str__(self):
        return self.name + ' (' + self.channel_type + ')'

    @property
    def email_address(self):
        if self.user_id:
            return self.user.email
        return self.destination


class Message(models.Model):
    channel = models.ForeignKey(MessageChannel, on_delete=models.CASCADE)
    subject = models.CharField(max_length=100, blank=True, null=True)
    message = models.TextField()
    status = models.CharField(max_length=100, choices=(
        ('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed'),
    ), default='pending')
    retry_count = models.IntegerField(default=0)
    next_retry = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    error_status_code = models.IntegerField(blank=True, null=True)
    completed = models.DateTimeField(blank=True, null=True)

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'next_retry'], name='message_queue'),
            models.Index(fields=['-created'], name='message_history'),
        ]


class MessageSettings(models.Model):
    name = models.CharField(max_length=16, default='message_settings', unique=True)
    max_length = models.IntegerField(default=2000)
    max_retry = models.IntegerField(default=3)
    retry_interval = models.IntegerField(default=60)  # in seconds
    concatenate_status_change = models.BooleanField(default=True)
    status_change_delay = models.IntegerField(default=120)  # in seconds
    concatenate_backup_fails = models.BooleanField(default=True)
    backup_fails_delay = models.IntegerField(default=600)  # in seconds
    last_daily_status_report = models.DateTimeField(blank=True, null=True)
    last_daily_backup_report = models.DateTimeField(blank=True, null=True)
    daily_report_time = models.CharField(max_length=5, default='07:00')

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)



class SmtpSettings(models.Model):
    """The mail server notifications are sent through - one for the
    installation, set by an administrator. The password is encrypted at rest
    and never shown again."""
    SECURITY = (('starttls', 'STARTTLS (port 587)'), ('ssl', 'SSL/TLS (port 465)'), ('none', 'None (unencrypted)'))
    name = models.CharField(max_length=16, default='smtp_settings', unique=True)
    enabled = models.BooleanField(default=False)
    host = models.CharField(max_length=253, blank=True)
    port = models.PositiveIntegerField(default=587)
    security = models.CharField(max_length=10, choices=SECURITY, default='starttls')
    username = models.CharField(max_length=254, blank=True)
    password = EncryptedTextField(blank=True, null=True)
    sender_address = models.CharField(max_length=254, blank=True)
    sender_name = models.CharField(max_length=100, default='RouterFleet Improved')

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)

    def __str__(self):
        return 'SMTP settings'

    @classmethod
    def load(cls):
        row, _ = cls.objects.get_or_create(name='smtp_settings')
        return row

    @property
    def ready(self):
        return bool(self.enabled and self.host and self.sender_address)
