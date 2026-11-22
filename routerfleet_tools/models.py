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

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    def __str__(self):
        return 'HTTPS settings'

    @classmethod
    def load(cls):
        settings_row, _ = cls.objects.get_or_create(name='https_settings')
        return settings_row
