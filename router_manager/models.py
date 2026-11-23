import uuid

from django.db import models

from backup.models import BackupProfile
from routerlib.encryption import EncryptedTextField



class DeviceType(models.Model):
    """A kind of device, as data - see router_manager/device_types.py.

    Devices and Fleet Commander variants refer to it by its slug, which never
    changes once created; everything else can be edited."""
    SESSION_MODES = [('exec', 'One SSH command per channel (RouterOS, OpenWrt, Linux)'),
                     ('shell', 'Interactive shell with a prompt (Cisco, Juniper, Huawei)')]
    FIRMWARE_VERIFY = [('none', 'Not checked'), ('size', 'File size'), ('sha256', 'SHA-256')]

    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100, unique=True)
    vendor = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    preset = models.CharField(max_length=50, blank=True, help_text='The shipped preset it started from')
    position = models.PositiveIntegerField(default=100)
    enabled = models.BooleanField(default=True, help_text='Offered for new devices')
    managed = models.BooleanField(default=True, help_text='Reached over SSH. Off: availability monitoring only')
    # Connection
    session_mode = models.CharField(max_length=8, choices=SESSION_MODES, default='exec')
    prompt_pattern = models.CharField(max_length=300, blank=True)
    session_setup = models.TextField(blank=True, help_text='Sent once after login in a shell: paging off and similar')
    enable_command = models.CharField(max_length=100, blank=True)
    # Links the router list offers on a device's address besides SSH: "Label: scheme://{address}"
    management_links = models.TextField(blank=True)
    # Recognition and inventory
    test_pattern = models.CharField(max_length=300, blank=True)
    inventory_fields = models.JSONField(default=dict, blank=True)
    inventory_repeat = models.CharField(max_length=300, blank=True)
    # Backups
    text_extension = models.CharField(max_length=10, default='txt')
    binary_extension = models.CharField(max_length=10, blank=True)
    text_ignore_pattern = models.CharField(max_length=300, blank=True)
    option_sensitive = models.CharField(max_length=100, blank=True)
    option_terse = models.CharField(max_length=100, blank=True)
    # The word when passwords and keys are left out (a type that exports them
    # unless told otherwise, such as RouterOS 6, needs one)
    option_hide_sensitive = models.CharField(max_length=100, blank=True)
    # Devices whose OS version is below this one use the legacy words instead
    # (RouterOS 6: show-sensitive does not exist, hide-sensitive does)
    legacy_before_version = models.CharField(max_length=20, blank=True)
    legacy_option_sensitive = models.CharField(max_length=100, blank=True)
    legacy_option_hide_sensitive = models.CharField(max_length=100, blank=True)
    # Which version the device could run
    check_device = models.BooleanField(default=False)
    check_vendor = models.BooleanField(default=False)
    check_repository = models.BooleanField(default=False)
    vendor_url = models.CharField(max_length=300, blank=True)
    vendor_keys = models.CharField(max_length=200, blank=True)
    vendor_confirm_url = models.CharField(max_length=300, blank=True)
    vendor_target = models.CharField(max_length=200, blank=True)
    vendor_version_field = models.CharField(max_length=30, default='os_version')
    # Firmware
    firmware_upload_path = models.CharField(max_length=200, blank=True)
    firmware_verify = models.CharField(max_length=8, choices=FIRMWARE_VERIFY, default='none')

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    class Meta:
        ordering = ['position', 'name']

    def __str__(self):
        return self.name

    @property
    def checks(self):
        return [label for on, label in ((self.check_device, 'device'), (self.check_vendor, 'vendor'),
                                        (self.check_repository, 'repository')) if on]


class SSHKey(models.Model):
    name = models.CharField(max_length=100, unique=True)
    public_key = models.TextField()
    # Encrypted at rest like Router.password; reads return the key as written
    private_key = EncryptedTextField()

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    def __str__(self):
        return self.name


class Router(models.Model):
    name = models.CharField(max_length=100, unique=True)
    internal_notes = models.TextField(null=True, blank=True)
    address = models.CharField(max_length=100)
    port = models.IntegerField(default=22)
    username = models.CharField(max_length=100, default='admin')
    password = EncryptedTextField(null=True, blank=True)
    ssh_key = models.ForeignKey(SSHKey, on_delete=models.SET_NULL, null=True, blank=True)
    monitoring = models.BooleanField(default=True)
    backup_profile = models.ForeignKey(BackupProfile, on_delete=models.SET_NULL, null=True, blank=True)
    # The console's Telnet: off unless switched on for the device - Telnet
    # sends everything, a password included, unencrypted
    console_telnet = models.BooleanField(default=False, verbose_name='Allow a Telnet console (unencrypted)')
    telnet_port = models.PositiveIntegerField(default=23, verbose_name='Telnet port')
    # When the device was last enabled or disabled - kept on every save, single
    # or bulk edit alike - so the status page can tell which days it was off
    enabled_changed_at = models.DateTimeField(null=True, blank=True, editable=False)
    # Whether unchanged scheduled backups are stored in full: as the backup
    # profile says (what every device had before), or decided for this device
    KEEP_UNCHANGED_CHOICES = [('profile', 'As the backup profile says'),
                              ('keep', 'Keep every backup in full'),
                              ('changes', 'Store only changes')]
    keep_unchanged = models.CharField(max_length=8, choices=KEEP_UNCHANGED_CHOICES, default='profile',
                                      verbose_name='Unchanged backups')

    def keeps_unchanged(self, profile):
        """Whether this device stores unchanged scheduled backups in full."""
        if self.keep_unchanged == 'keep':
            return True
        if self.keep_unchanged == 'changes':
            return False
        return bool(profile and profile.keep_unchanged)
    # The slug of a DeviceType
    router_type = models.CharField(max_length=100)
    # For devices whose account does not land in privileged mode (Cisco enable)
    enable_password = EncryptedTextField(null=True, blank=True)
    enabled = models.BooleanField(default=True)
    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    def save(self, *args, **kwargs):
        if self.pk:
            before = Router.objects.filter(pk=self.pk).values_list('enabled', flat=True).first()
            if before is not None and before != self.enabled:
                from django.utils import timezone
                self.enabled_changed_at = timezone.now()
                if kwargs.get('update_fields') is not None:
                    kwargs['update_fields'] = set(kwargs['update_fields']) | {'enabled_changed_at'}
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def device_type(self):
        from . import device_types
        return device_types.get(self.router_type)

    def get_router_type_display(self):
        return self.device_type.name if self.device_type else self.router_type

    @property
    def is_managed(self):
        """Reached over SSH: backups, commands, console."""
        return bool(self.device_type and self.device_type.managed)


class RouterStatus(models.Model):
    router = models.OneToOneField(Router, on_delete=models.CASCADE)
    status_online = models.BooleanField(default=True)
    last_status_change = models.DateTimeField(blank=True, null=True)
    last_backup = models.DateTimeField(blank=True, null=True)
    last_backup_failed = models.DateTimeField(blank=True, null=True)
    backup_lock = models.DateTimeField(blank=True, null=True)
    command_lock = models.DateTimeField(blank=True, null=True)

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)


class RouterGroup(models.Model):
    name = models.CharField(max_length=100, unique=True)
    default_group = models.BooleanField(default=False)
    internal_notes = models.TextField(null=True, blank=True)
    routers = models.ManyToManyField(Router, blank=True)

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    def __str__(self):
        return self.name


class BackupSchedule(models.Model):
    router = models.OneToOneField(Router, on_delete=models.CASCADE)
    next_daily_backup = models.DateTimeField(null=True, blank=True)
    next_weekly_backup = models.DateTimeField(null=True, blank=True)
    next_monthly_backup = models.DateTimeField(null=True, blank=True)

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)


class RouterInformation(models.Model):
    router = models.OneToOneField(Router, on_delete=models.CASCADE)

    success = models.BooleanField(default=False)
    error = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, null=True)
    retry_count = models.IntegerField(default=0)
    next_retry = models.DateTimeField(blank=True, null=True)
    last_retrieval = models.DateTimeField(blank=True, null=True)
    # Set when a user asks for an update from the router list, so the next cron
    # run picks this router before the regular refresh queue
    update_requested = models.BooleanField(default=False)

    model_name = models.CharField(max_length=100, null=True, blank=True)
    model_version = models.CharField(max_length=100, null=True, blank=True)
    serial_number = models.CharField(max_length=100, null=True, blank=True)

    os_version = models.CharField(max_length=100, null=True, blank=True)
    firmware_version = models.CharField(max_length=100, null=True, blank=True)
    # Version the router could be upgraded to, and whether it differs from the
    # installed one. Empty when the router type has no automatic update check.
    available_version = models.CharField(max_length=100, null=True, blank=True)
    update_available = models.BooleanField(default=False)
    architecture = models.CharField(max_length=100, null=True, blank=True)
    cpu = models.CharField(max_length=100, null=True, blank=True)

    json_data = models.TextField(null=True, blank=True)

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    def __str__(self):
        return str(self.router)


class SSHHostKey(models.Model):
    """The host key a device presented the first time RouterFleet connected to
    it (trust on first use). Afterwards a connection to the same address and
    port is refused, before anything is sent, unless the device presents this
    key again. Kept by address and port rather than by device, because the
    connection test runs before the device is saved."""
    address = models.CharField(max_length=100)
    port = models.IntegerField()
    key_type = models.CharField(max_length=100)
    key_base64 = models.TextField()
    fingerprint = models.CharField(max_length=100)
    first_seen = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['address', 'port'], name='unique_ssh_host_key_address_port')]

    def __str__(self):
        return f'{self.address}:{self.port} {self.key_type} {self.fingerprint}'
