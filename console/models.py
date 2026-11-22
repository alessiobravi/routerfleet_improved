"""Console sessions: a terminal on a device, from the browser.

A row is created when a user confirms the console form (the request is
recorded in the change log with who made it) and carries a one-time ticket:
only its SHA-256 is stored, it is valid for TICKET_SECONDS, and it can open
one connection. The row then records how the session went - never what was
typed or shown.
"""
import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

TICKET_SECONDS = 60


def hash_ticket(ticket):
    return hashlib.sha256(ticket.encode()).hexdigest()


class ConsoleSession(models.Model):
    TRANSPORTS = (('ssh', 'SSH'), ('telnet', 'Telnet'))
    ACCOUNTS = (('backup', "The device's backup account"), ('other', 'Another account'))

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    user_name = models.CharField(max_length=150)
    router = models.ForeignKey('router_manager.Router', on_delete=models.SET_NULL, null=True,
                               related_name='console_sessions')
    router_name = models.CharField(max_length=100)
    transport = models.CharField(max_length=10, choices=TRANSPORTS)
    account = models.CharField(max_length=10, choices=ACCOUNTS)
    # The account the session used - the name only, never a password
    username = models.CharField(max_length=100, blank=True)
    ticket_hash = models.CharField(max_length=64, unique=True)
    ticket_expires = models.DateTimeField()
    connected_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=120, blank=True)
    client_address = models.CharField(max_length=45, blank=True)
    bytes_to_device = models.BigIntegerField(default=0)
    bytes_from_device = models.BigIntegerField(default=0)

    created = models.DateTimeField(auto_now_add=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    def __str__(self):
        return f'Console on {self.router_name} as {self.username or "-"} ({self.get_transport_display()})'

    @classmethod
    def open(cls, user, router, transport, account, username):
        """A new session and its ticket - returned once, stored only as a hash."""
        ticket = secrets.token_urlsafe(32)
        session = cls.objects.create(
            user=user, user_name=user.username, router=router, router_name=router.name, transport=transport,
            account=account, username=username, ticket_hash=hash_ticket(ticket),
            ticket_expires=timezone.now() + timezone.timedelta(seconds=TICKET_SECONDS))
        return session, ticket

    @property
    def duration(self):
        if not self.connected_at:
            return None
        return (self.ended_at or timezone.now()) - self.connected_at


def tidy_sessions():
    """Housekeeping: a session left open by a server restart is closed with
    that reason; a ticket never used is removed after a week. Returns the
    counts."""
    now = timezone.now()
    from django.conf import settings
    stale = now - timezone.timedelta(seconds=settings.CONSOLE_MAX_SECONDS + 600)
    closed = ConsoleSession.objects.filter(connected_at__lt=stale, ended_at__isnull=True).update(
        ended_at=now, end_reason='the console server stopped while it was open')
    removed, _ = ConsoleSession.objects.filter(connected_at__isnull=True,
                                               ticket_expires__lt=now - timezone.timedelta(days=7)).delete()
    return closed, removed
