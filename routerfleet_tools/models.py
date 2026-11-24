from django.conf import settings
from django.db import models
import uuid


class WebadminSettings(models.Model):
    name = models.CharField(default='webadmin_settings', max_length=20, unique=True)
    update_available = models.BooleanField(default=False)
    current_version = models.PositiveIntegerField(default=0)
    latest_version = models.PositiveIntegerField(default=0)
    last_checked = models.DateTimeField(blank=True, null=True)
    router_config_last_updated = models.DateTimeField(blank=True, null=True)
    cron_last_run = models.DateTimeField(blank=True, null=True)
    monitoring_last_run = models.DateTimeField(blank=True, null=True)

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    def __str__(self):
        return self.name


class HttpsSettings(models.Model):
    """How the installation serves HTTPS, chosen in System settings.

    'env' - the default, and what every installation has after upgrading -
    keeps the behaviour of HTTPS_REDIRECT_POLICY in .env exactly as before. Any
    other mode is applied by the nginx container from the shared certificate
    volume (see routerfleet_tools/https.py). The private key of an uploaded
    certificate is never stored here: it is written to that volume only.
    """
    MODES = (
        ('env', 'As configured in .env (HTTPS_REDIRECT_POLICY)'),
        ('letsencrypt', "Let's Encrypt certificate, renewed automatically"),
        ('custom', 'Certificate provided by an administrator'),
        ('selfsigned', 'Self-signed certificate, generated'),
        ('proxy', 'HTTPS off: behind a reverse proxy that serves HTTPS'),
        ('plain', 'HTTPS off: plain HTTP'),
    )
    name = models.CharField(default='https_settings', max_length=20, unique=True)
    mode = models.CharField(max_length=16, choices=MODES, default='env')
    domain = models.CharField(max_length=253, blank=True)
    email = models.CharField(max_length=254, blank=True)
    trusted_proxies = models.TextField(blank=True)
    # What an uploaded certificate says about itself - shown, never the key
    custom_subject = models.CharField(max_length=255, blank=True)
    custom_issuer = models.CharField(max_length=255, blank=True)
    custom_not_after = models.DateTimeField(blank=True, null=True)
    custom_names = models.TextField(blank=True)
    custom_trusted = models.BooleanField(default=False)
    generation = models.PositiveIntegerField(default=0)
    # HTTP access: what an address no entry of the access list matches gets
    ACCESS_POLICIES = (('allow', 'Allow'), ('deny', 'Deny'))
    access_default = models.CharField(max_length=5, choices=ACCESS_POLICIES, default='allow')

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    def __str__(self):
        return 'HTTPS settings'

    @classmethod
    def load(cls):
        settings_row, _ = cls.objects.get_or_create(name='https_settings')
        return settings_row


class DisplaySettings(models.Model):
    """How pages look for everyone, chosen in System settings, Display.

    The default page size is what every table opens with; each table still
    offers the others. 10 - what tables always showed - is what an upgraded
    installation keeps."""
    PAGE_SIZES = (10, 25, 50, 100)

    name = models.CharField(default='display_settings', max_length=20, unique=True)
    default_page_size = models.PositiveSmallIntegerField(
        default=10, choices=[(n, f'{n} rows') for n in PAGE_SIZES], verbose_name='Default rows per table')

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    def __str__(self):
        return self.name

    @classmethod
    def load(cls):
        settings_row, _ = cls.objects.get_or_create(name='display_settings')
        return settings_row


class UpdateRun(models.Model):
    """An update approved in System settings, and how it went.

    Kept for the history of updates approved with the in-app updater of 0.21
    to 0.23.0, which was removed in 0.23.1 - no container holds Docker access
    any more. Nothing creates rows now."""
    STATES = [('requested', 'Requested'), ('verifying', 'Checking signatures'), ('backing_up', 'Backing up'),
              ('restarting', 'Restarting'), ('checking', 'Waiting until ready'), ('done', 'Done'),
              ('failed', 'Failed'), ('refused', 'Refused'), ('rolling_back', 'Rolling back'),
              ('rolled_back', 'Rolled back')]
    ACTIVE = ('requested', 'verifying', 'backing_up', 'restarting', 'checking', 'rolling_back')

    request_id = models.CharField(max_length=32, unique=True)
    from_version = models.CharField(max_length=16)
    to_version = models.CharField(max_length=16)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    approved_by_name = models.CharField(max_length=150)
    state = models.CharField(max_length=16, choices=STATES, default='requested')
    message = models.CharField(max_length=500, blank=True)

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return f'{self.from_version} to {self.to_version}'

    @property
    def active(self):
        return self.state in self.ACTIVE


class AccessRule(models.Model):
    """One entry of the HTTP access list (System settings, HTTP access):
    allow or deny an address or a network, IPv4 or IPv6. nginx checks the
    entries in order and the first that matches decides; an address none
    matches gets the final policy (HttpsSettings.access_default)."""
    ACTIONS = (('allow', 'Allow'), ('deny', 'Deny'))
    position = models.PositiveIntegerField(default=0)
    action = models.CharField(max_length=5, choices=ACTIONS)
    network = models.CharField(max_length=50)
    note = models.CharField(max_length=100, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f'{self.action} {self.network}'
