"""Where a console may connect: devices, never RouterFleet's own inside.

The console's Telnet is a raw TCP connection, and a device's address and port
are set by Host Managers. Pointed at the database container, the application
itself or a loopback address, a "device" would give an interactive connection
from inside the stack. So the console resolves the address first and refuses:

- loopback, link-local, unspecified, multicast and reserved addresses;
- the networks this container is directly attached to (the stack's internal
  Docker networks), read from its routing table;
- the stack's service names.

The checked address is the one connected to, so a DNS answer can not change in
between. Devices on the LAN (10.x, 192.168.x, ...) are reached through the
gateway, not these networks, and stay allowed.
"""
import ipaddress
import socket

STACK_NAMES = {'routerfleet', 'routerfleet-postgres', 'routerfleet-nginx', 'routerfleet-cron',
               'routerfleet-monitoring', 'localhost'}
ROUTE_TABLE = '/proc/net/route'


class Refused(Exception):
    pass


def attached_networks(route_table=ROUTE_TABLE):
    """The IPv4 networks this container reaches without a gateway."""
    networks = []
    try:
        with open(route_table) as handle:
            next(handle, None)
            for line in handle:
                fields = line.split()
                if len(fields) < 8:
                    continue
                destination, gateway, mask = fields[1], fields[2], fields[7]
                if gateway != '00000000' or destination == '00000000':
                    continue
                address = ipaddress.IPv4Address(int(destination, 16).to_bytes(4, 'little'))
                netmask = ipaddress.IPv4Address(int(mask, 16).to_bytes(4, 'little'))
                networks.append(ipaddress.IPv4Network(f'{address}/{netmask}', strict=False))
    except (OSError, ValueError):
        pass
    return networks


def check(host, port, route_table=ROUTE_TABLE):
    """The IP address to connect to, or Refused with the reason."""
    name = (host or '').strip().lower().rstrip('.')
    if not name or name in STACK_NAMES or name.endswith('.localhost'):
        raise Refused('that address belongs to RouterFleet itself')
    try:
        answers = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise Refused('the address does not resolve') from None
    addresses = [ipaddress.ip_address(answer[4][0].split('%')[0]) for answer in answers]
    inside = attached_networks(route_table)
    for address in addresses:
        mapped = getattr(address, 'ipv4_mapped', None) or address
        if (mapped.is_loopback or mapped.is_link_local or mapped.is_unspecified or mapped.is_multicast
                or mapped.is_reserved):
            raise Refused('that address is not a device')
        if any(mapped in network for network in inside if mapped.version == network.version):
            raise Refused("that address is on RouterFleet's own internal network")
    return str(addresses[0])
