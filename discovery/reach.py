"""Whether RouterFleet's host reaches a discovered device: its SSH port (22)
answering a connection - what adding it needs. Checked in parallel, each
attempt bounded, never more than a few at a time per page."""
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor

from django.utils import timezone

SSH_PORT = 22
TIMEOUT = 1.5
STALE = timezone.timedelta(minutes=10)
MOST = 40


def is_ip(value):
    """An IPv4 or IPv6 address a device can be reached at (not link-local)."""
    try:
        address = ipaddress.ip_address((value or '').strip())
    except ValueError:
        return False
    # Loopback would probe RouterFleet's own container, not a device
    if address.is_link_local or address.is_multicast or address.is_unspecified or address.is_loopback:
        return False
    # ... nor RouterFleet's own Docker networks: an address announced on the LAN
    # never sends the probe into the stack itself
    from console.network import attached_networks, gateways
    if str(address) in gateways():
        return False
    return not any(address in network for network in attached_networks() if network.version == address.version)


def _probe(address, port=SSH_PORT):
    try:
        with socket.create_connection((address, port), timeout=TIMEOUT):
            return True, f'SSH (port {port}) answers'
    except socket.timeout:
        return False, f'no answer on SSH (port {port})'
    except ConnectionRefusedError:
        return False, f'SSH (port {port}) refused: reachable, but no SSH there'
    except OSError as error:
        return False, f'not reachable ({error.strerror or error.__class__.__name__})'


def check(devices, port=SSH_PORT):
    """Checks these devices (with an IP address) and stores the result."""
    devices = [d for d in devices if is_ip(d.address)][:MOST]
    if not devices:
        return 0
    with ThreadPoolExecutor(max_workers=min(16, len(devices))) as pool:
        results = list(pool.map(lambda d: _probe(d.address, port), devices))
    now = timezone.now()
    for device, (reachable, detail) in zip(devices, results):
        device.reachable, device.reachable_detail, device.checked_at = reachable, detail[:120], now
        device.save(update_fields=['reachable', 'reachable_detail', 'checked_at'])
    return len(devices)


def check_stale():
    """The devices not checked in the last minutes."""
    from django.db.models import Q
    from .models import DiscoveredDevice
    since = timezone.now() - STALE
    return check(DiscoveredDevice.objects.filter(dismissed=False).exclude(address='')
                 .filter(Q(checked_at__isnull=True) | Q(checked_at__lt=since)).order_by('checked_at')[:MOST])
