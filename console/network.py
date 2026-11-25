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
IPV6_ROUTE_TABLE = '/proc/net/ipv6_route'


class Refused(Exception):
    pass


def attached_networks(route_table=ROUTE_TABLE, ipv6_route_table=None):
    """The networks this container reaches without a gateway - IPv4 and IPv6."""
    networks = _attached_ipv6(ipv6_route_table or IPV6_ROUTE_TABLE)
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


def gateways(route_table=ROUTE_TABLE, ipv6_route_table=IPV6_ROUTE_TABLE):
    """The default gateways of this container, IPv4 and IPv6: on Docker's own
    network, the address every client appears from when Docker hands
    connections over itself (its userland proxy, Docker Desktop, rootless)."""
    found = set()
    try:
        with open(route_table) as handle:
            next(handle, None)
            for line in handle:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == '00000000' and fields[2] != '00000000':
                    found.add(str(ipaddress.IPv4Address(int(fields[2], 16).to_bytes(4, 'little'))))
    except (OSError, ValueError):
        pass
    try:
        with open(ipv6_route_table) as handle:
            for line in handle:
                fields = line.split()
                if len(fields) >= 5 and fields[0] == '0' * 32 and int(fields[1], 16) == 0 and fields[4] != '0' * 32:
                    found.add(str(ipaddress.IPv6Address(int(fields[4], 16))))
    except (OSError, ValueError):
        pass
    return found


def _attached_ipv6(route_table):
    """/proc/net/ipv6_route: destination, prefix length, ..., next hop - a
    route without a next hop (all zeros) is a network on a link."""
    networks = []
    try:
        with open(route_table) as handle:
            for line in handle:
                fields = line.split()
                if len(fields) < 5:
                    continue
                destination, length, next_hop = fields[0], int(fields[1], 16), fields[4]
                if next_hop != '0' * 32 or destination == '0' * 32 or length == 0:
                    continue
                network = ipaddress.IPv6Network((int(destination, 16), length), strict=False)
                if not (network.is_link_local or network.is_multicast or network.is_loopback):
                    networks.append(network)
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
