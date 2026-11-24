"""A limit on failed sign-in attempts.

Counted from the login history the audit log keeps anyway, within
LOGIN_LOCKOUT_MINUTES, and further attempts are turned away without the
password being checked at all:

- LOGIN_LOCKOUT_ATTEMPTS refused attempts for one account from one address
  lock that account from that address - so failing on purpose from elsewhere
  no longer locks the owner out;
- as many from one address, whatever the names, lock that address;
- LOGIN_LOCKOUT_ACCOUNT_ATTEMPTS for one account from all addresses together
  lock the account everywhere - guessing spread over many addresses.

A successful sign-in starts the count again.

Attempts turned away while locked are recorded too, so they show in the login
history, but they do not count: someone who keeps trying while locked out would
otherwise never get back in.
"""
from django.conf import settings
from django.utils import timezone

from audit_log import context
from audit_log.models import LoginRecord

ACTOR_LOCKED_OUT = 'locked-out'


def limits():
    return (getattr(settings, 'LOGIN_LOCKOUT_ATTEMPTS', 5),
            timezone.timedelta(minutes=getattr(settings, 'LOGIN_LOCKOUT_MINUTES', 15)))


def account_limit():
    """Refused attempts for one account from all addresses together."""
    attempts, _ = limits()
    return getattr(settings, 'LOGIN_LOCKOUT_ACCOUNT_ATTEMPTS', attempts * 4)


def _failures(**where):
    _, window = limits()
    since = timezone.now() - window
    records = LoginRecord.objects.filter(created__gte=since, **where)
    last_success = records.filter(successful=True).order_by('-created').values_list('created', flat=True).first()
    failures = records.filter(successful=False).exclude(actor_kind=ACTOR_LOCKED_OUT)
    if last_success:
        failures = failures.filter(created__gt=last_success)
    return failures.count()


def is_locked_out(request, username):
    attempts, _ = limits()
    if attempts <= 0:
        return False
    address, _ = context.client_address(request)
    if address and (_failures(username__iexact=username, ip=address) >= attempts
                    or _failures(ip=address) >= attempts):
        return True
    return _failures(username__iexact=username) >= account_limit()


def address_locked_out(request):
    """The address part alone, for a sign-in that names no account (a passkey)."""
    attempts, _ = limits()
    if attempts <= 0:
        return False
    address, _ = context.client_address(request)
    return bool(address) and _failures(ip=address) >= attempts


def record_turned_away(request, username):
    fields = context.identity(request)
    fields.update(actor=None, actor_kind=ACTOR_LOCKED_OUT, username=context.text(username, 150),
                  user_level=None, session_key='')
    LoginRecord(successful=False, **fields).save()
