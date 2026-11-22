from django.db import models
from django.contrib.auth.models import User
import uuid

from routerlib.encryption import EncryptedTextField


class UserAcl(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    user_level = models.PositiveIntegerField(default=0, choices=(
        (10, 'Viewer'),
        (20, 'Backup Operator'),
        (30, 'Host Manager'),
        (40, 'configuration Manager'),
        (50, 'Administrator'),
    ))
    address_link = models.CharField(max_length=10, default='none', choices=(
        ('none', 'No links'),
        ('ssh', 'Open with SSH'),
        ('winbox', 'Open with Winbox'),
        ('both', 'Show a menu (SSH / Winbox)'),
    ), help_text='What a click on the address of a router in the router list does.')

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    uuid = models.UUIDField(primary_key=True, editable=False, default=uuid.uuid4)

    def __str__(self):
        return self.user.username


class UserTotp(models.Model):
    """A user's authenticator app. Only a confirmed one is asked for at sign-in:
    setting one up shows the secret once, and it takes a correct code to turn
    it on, so a half-finished setup cannot lock anybody out."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='totp')
    # Encrypted at rest, like every other credential
    secret = EncryptedTextField()
    confirmed = models.BooleanField(default=False)
    # The last 30-second step a code was accepted for: a code is never accepted twice
    last_used_step = models.BigIntegerField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'Authenticator app of {self.user.username}'


class UserPasskey(models.Model):
    """A passkey registered by a user (a WebAuthn credential)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='passkeys')
    name = models.CharField(max_length=100)
    credential_id = models.CharField(max_length=512, unique=True)  # base64url
    public_key = models.TextField()                                 # base64url, COSE
    sign_count = models.BigIntegerField(default=0)
    rp_id = models.CharField(max_length=253)
    created = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    def __str__(self):
        return f'{self.name} ({self.user.username})'
