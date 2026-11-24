"""Banning after failed sign-ins - fail2ban's idea, built in.

Every failed sign-in is in the login history already. When one address has
HttpsSettings.ban_failures of them within ban_window_minutes (since its last
successful sign-in), it is banned for ban_minutes: nginx serves it nothing at
all (deny, before the access list), and the application refuses its sign-ins
too, in case nginx has not received the ban. An address an Allow entry of the
HTTP access list matches is never banned, nor a loopback address or a trusted
proxy - banning those would shut out everybody behind them.

A ban ends by itself (nginx drops it when its time is up) or with Unban in
System settings; `manage.py http_access --reset` lifts every ban.
"""
import ipaddress
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)


def _address(text):
    try:
        address = ipaddress.ip_address((text or '').strip().strip('[]'))
    except ValueError:
        return None
    return getattr(address, 'ipv4_mapped', None) or address


def never_banned(text, https_settings):
    """Whitelisted: an Allow entry matches it, or banning it would shut out others."""
    from .https import clean_proxies
    from .models import AccessRule
    address = _address(text)
    if address is None or address.is_loopback or address.is_unspecified:
        return True
    nets = [rule.network for rule in AccessRule.objects.filter(action='allow')]
    nets += clean_proxies(https_settings.trusted_proxies) if https_settings.mode == 'proxy' else []
    for net in nets:
        try:
            if address in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue
    return False


def active():
    from .models import AddressBan
    return AddressBan.objects.filter(until__gt=timezone.now())


def is_banned(text):
    address = _address(text)
    return address is not None and active().filter(address=str(address)).exists()


def failures(address, minutes):
    from audit_log.models import LoginRecord
    records = LoginRecord.objects.filter(ip=address, created__gte=timezone.now() - timezone.timedelta(minutes=minutes))
    last_success = records.filter(successful=True).order_by('-created').values_list('created', flat=True).first()
    failed = records.filter(successful=False)
    return (failed.filter(created__gt=last_success) if last_success else failed).count()


def consider(text):
    """After a failed sign-in from an address: ban it when it reached the limit."""
    from . import https
    from .models import AddressBan, HttpsSettings
    https_settings = HttpsSettings.load()
    address = _address(text)
    if not https_settings.ban_enabled or address is None or https_settings.ban_failures < 1:
        return None
    address = str(address)
    if is_banned(address) or never_banned(address, https_settings):
        return None
    count = failures(address, max(https_settings.ban_window_minutes, 1))
    if count < https_settings.ban_failures:
        return None
    AddressBan.objects.filter(until__lte=timezone.now() - timezone.timedelta(days=30)).delete()
    ban = AddressBan.objects.create(address=address, failures=count,
                                    until=timezone.now() + timezone.timedelta(minutes=max(https_settings.ban_minutes, 1)))
    logger.warning(f'Banned {address} for {https_settings.ban_minutes} minutes after {count} failed sign-ins')
    try:
        https.write_state(https_settings)
    except OSError:
        pass   # the application still refuses its sign-ins
    return ban


@receiver(post_save, sender='audit_log.LoginRecord')
def _after_sign_in(sender, instance, created, **kwargs):
    if created and not instance.successful and instance.ip:
        try:
            consider(instance.ip)
        except Exception:   # a ban must never break a sign-in page
            logger.exception('Ban check failed')
