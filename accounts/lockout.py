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


def counted_failures(records):
    """The failed attempts among these records that still count: a successful
    sign-in clears the failures of that same account (from the same address,
    when records span addresses) before it - never anybody else's. Otherwise
    whoever holds any account could sign in between guesses at other
    accounts and never be locked out or banned."""
    rows = list(records.values_list('created', 'username', 'ip', 'successful'))
    last_success = {}
    for created, username, ip, successful in rows:
        if successful:
            key = ((username or '').lower(), ip)
            if created > last_success.get(key, created.min.replace(tzinfo=created.tzinfo)):
                last_success[key] = created
    count = 0
    for created, username, ip, successful in rows:
        if successful:
            continue
        cleared = last_success.get(((username or '').lower(), ip))
        if cleared is None or created > cleared:
            count += 1
    return count


def _failures(**where):
    _, window = limits()
    since = timezone.now() - window
    records = LoginRecord.objects.filter(created__gte=since, **where).exclude(actor_kind=ACTOR_LOCKED_OUT, successful=False)
    return counted_failures(records)


def _shared(address):
    """Every client's address at once (the container's gateway): no address-
    wide lock there - one person would lock out everybody."""
    from routerfleet_tools.bans import _address, shared
    parsed = _address(address)
    return parsed is not None and shared(parsed)


def _banned(request):
    from routerfleet_tools.bans import is_banned
    return is_banned(context.client_address(request)[0])


def is_locked_out(request, username):
    if _banned(request):
        return True
    attempts, _ = limits()
    if attempts <= 0:
        return False
    address, _ = context.client_address(request)
    if address and (_failures(username__iexact=username, ip=address) >= attempts
                    or (not _shared(address) and _failures(ip=address) >= attempts)):
        return True
    return _failures(username__iexact=username) >= account_limit()


def address_locked_out(request):
    """The address part alone, for a sign-in that names no account (a passkey)."""
    if _banned(request):
        return True
    attempts, _ = limits()
    if attempts <= 0:
        return False
    address, _ = context.client_address(request)
    return bool(address) and not _shared(address) and _failures(ip=address) >= attempts


def record_turned_away(request, username):
    fields = context.identity(request)
    fields.update(actor=None, actor_kind=ACTOR_LOCKED_OUT, username=context.text(username, 150),
                  user_level=None, session_key='')
    LoginRecord(successful=False, **fields).save()
