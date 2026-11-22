"""The firmware repository and its rollouts.

A firmware image belongs to a device type and may be limited to models
(a pattern) and an architecture. A rollout installs one image on a set of
devices: a fresh backup of each device first, the file uploaded and verified
on the device, the install, and the device must come back reporting the new
version. The first device goes alone (the canary); the others follow in small
batches; any failure stops the rest. See firmware/rollout.py.
"""
import re
import uuid

from django.conf import settings
from django.db import models

from router_manager.models import DeviceType, Router


class FirmwareImage(models.Model):
    device_type = models.ForeignKey(DeviceType, on_delete=models.PROTECT, related_name='firmware_images')
    version = models.CharField(max_length=64)
    file = models.FileField(upload_to='firmware/')
    filename = models.CharField(max_length=200)
    size = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    models_pattern = models.CharField(max_length=200, blank=True,
                                      help_text='A regular expression matched against the model; empty: every model')
    architecture = models.CharField(max_length=50, blank=True, help_text='Empty: every architecture')
    notes = models.TextField(blank=True)
    enabled = models.BooleanField(default=True, help_text='Offered for updates and counted by the update check')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_by_name = models.CharField(max_length=150, blank=True)

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    class Meta:
        ordering = ['device_type__position', '-created']

    def __str__(self):
        return f'{self.device_type.name} {self.version}'

    def matches(self, model_name='', model_version='', architecture=''):
        """Whether this image is for a device with these inventory values."""
        if self.architecture and self.architecture.lower() != (architecture or '').lower():
            return False
        if self.models_pattern:
            try:
                pattern = re.compile(self.models_pattern, re.IGNORECASE)
            except re.error:
                return False
            if not (pattern.search(model_name or '') or pattern.search(model_version or '')):
                return False
        return True

    def matches_router(self, router):
        info = getattr(router, 'routerinformation', None)
        return self.matches(getattr(info, 'model_name', ''), getattr(info, 'model_version', ''),
                            getattr(info, 'architecture', ''))

    @classmethod
    def newest_for(cls, device_type, found):
        """The newest enabled image for a device with these inventory values."""
        from router_manager.templatetags.router_tags import compare_versions
        best = None
        for image in cls.objects.filter(device_type=device_type, enabled=True):
            if not image.matches(found.get('model_name', ''), found.get('model_version', ''),
                                 found.get('architecture', '')):
                continue
            if best is None or compare_versions(best.version, image.version) == -1:
                best = image
        return best


class FirmwareRollout(models.Model):
    STATES = [('running', 'Running'), ('done', 'Done'), ('failed', 'Stopped by a failure'), ('aborted', 'Aborted')]

    image = models.ForeignKey(FirmwareImage, on_delete=models.PROTECT, related_name='rollouts')
    batch_size = models.PositiveSmallIntegerField(default=3)
    state = models.CharField(max_length=10, choices=STATES, default='running')
    message = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_by_name = models.CharField(max_length=150, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return f'{self.image} on {self.targets.count()} devices'

    @property
    def active(self):
        return self.state == 'running'


class RolloutTarget(models.Model):
    STATES = [('waiting', 'Waiting'), ('backing_up', 'Backing up'), ('uploading', 'Uploading'),
              ('verifying', 'Verifying the upload'), ('installing', 'Installing'),
              ('rebooting', 'Waiting for the new version'), ('done', 'Updated'), ('failed', 'Failed'),
              ('skipped', 'Not started')]
    IN_PROGRESS = ('backing_up', 'uploading', 'verifying', 'installing', 'rebooting')

    rollout = models.ForeignKey(FirmwareRollout, on_delete=models.CASCADE, related_name='targets')
    router = models.ForeignKey(Router, on_delete=models.CASCADE)
    position = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=12, choices=STATES, default='waiting')
    message = models.TextField(blank=True)
    from_version = models.CharField(max_length=100, blank=True)
    backup = models.ForeignKey('backup_data.RouterBackup', on_delete=models.SET_NULL, null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    lock = models.DateTimeField(null=True, blank=True)

    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    class Meta:
        ordering = ['rollout', 'position']
        constraints = [models.UniqueConstraint(fields=['rollout', 'router'], name='one_target_per_rollout_router')]

    def __str__(self):
        return f'{self.router} in {self.rollout}'

    @property
    def canary(self):
        return self.position == 0
