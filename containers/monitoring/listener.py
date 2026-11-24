"""RouterFleet discovery listener - runs in the monitoring container.

Dormant until asked: it opens no socket and hears nothing until RouterFleet
asks for a window ("Discover adjacent devices" writes REQUEST with an end
time). During the window it hears the neighbour protocols on the LAN segment
of the host it runs on - MikroTik MNDP (UDP broadcast, port 5678), LLDP and
CDP (layer-2 frames, read with a raw socket: the monitoring container has
host networking and CAP_NET_RAW, nothing else) - then closes its sockets again.
Nothing is sent. What it hears goes to a JSON file on a shared volume
(DISCOVERY_FILE); its state to STATE, so the page shows whether it runs. A
device not heard for a day is left out. No network path to the application.
"""
import ipaddress
import json
import os
import select
import socket
import struct
import sys
import threading
import time

OUT = os.environ.get('DISCOVERY_FILE', '/discovery/seen.json')
FOLDER = os.path.dirname(OUT)
REQUEST = os.path.join(FOLDER, 'request.json')
STATE = os.path.join(FOLDER, 'listener.json')
LONGEST_WINDOW = 900
FORGET_SECONDS = 86400
WRITE_SECONDS = 30
LLDP_MAC = bytes.fromhex('0180c200000e')
CDP_MAC = bytes.fromhex('01000ccccccc')
ETH_P_ALL = 0x0003
seen = {}
lock = threading.Lock()


def usable(address):
    """An address a device can be reached at - IPv4 or IPv6, not link-local
    (fe80:: needs its interface), else ''."""
    try:
        parsed = ipaddress.ip_address((address or '').split('%')[0])
    except ValueError:
        return ''
    return '' if (parsed.is_link_local or parsed.is_multicast or parsed.is_unspecified) else str(parsed)


def mac_text(raw):
    return ':'.join(f'{b:02x}' for b in raw)


def remember(record, protocol):
    key = record.get('mac') or f"{record.get('address', '')}|{record.get('identity', '')}"
    if key in ('|', ''):
        return
    with lock:
        known = seen.setdefault(key, {'protocols': []})
        known.update({k: v for k, v in record.items() if v})
        if protocol not in known['protocols']:
            known['protocols'].append(protocol)
        known['last_seen'] = int(time.time())


# --- MikroTik Neighbor Discovery: TLVs after a 4-byte header -------------------
MNDP_FIELDS = {1: 'mac', 5: 'identity', 7: 'version', 8: 'platform', 12: 'model', 16: 'remote_interface', 17: 'address'}


def parse_mndp(data, source=''):
    record = {'address': usable(source)}
    position = 4
    while position + 4 <= len(data):
        kind, length = struct.unpack('>HH', data[position:position + 4])
        value = data[position + 4:position + 4 + length]
        position += 4 + length
        name = MNDP_FIELDS.get(kind)
        if name == 'mac' and len(value) == 6:
            record['mac'] = mac_text(value)
        elif name == 'address' and len(value) == 4:
            record['address'] = str(ipaddress.IPv4Address(value))
        elif name:
            record[name] = value.decode('utf-8', errors='replace')
    return record


# --- Ubiquiti discovery (UBNT): version, command, 2-byte length, then TLVs of
# 1-byte type and 2-byte length (types as Wireshark's packet-ubdp.c names them).
# Heard only when devices announce themselves (airOS broadcasts to port 10002,
# others to 10001): nothing is asked. --------------------------------------
UBNT_FIELDS = {0x0B: 'identity', 0x0C: 'model', 0x14: 'model', 0x15: 'model', 0x03: 'version', 0x16: 'version'}


def parse_ubnt(data, source=''):
    if len(data) < 4 or data[0] not in (1, 2):
        return None
    record = {'address': source, 'platform': 'Ubiquiti'}
    position = 4
    while position + 3 <= len(data):
        kind = data[position]
        length = struct.unpack('>H', data[position + 1:position + 3])[0]
        value = data[position + 3:position + 3 + length]
        position += 3 + length
        if kind == 0x02 and len(value) == 10:
            record['mac'] = mac_text(value[:6])
            record['address'] = str(ipaddress.IPv4Address(value[6:10]))
        elif kind in (0x01, 0x05, 0x13) and len(value) == 6:
            record.setdefault('mac', mac_text(value))
        elif kind in UBNT_FIELDS and value:
            record.setdefault(UBNT_FIELDS[kind], value.decode('utf-8', errors='replace'))
    return record if record.get('mac') or record.get('identity') else None


# --- LLDP (IEEE 802.1AB): 7-bit type, 9-bit length ----------------------------
def parse_lldp(payload):
    record = {}
    position = 0
    while position + 2 <= len(payload):
        header = struct.unpack('>H', payload[position:position + 2])[0]
        kind, length = header >> 9, header & 0x1FF
        value = payload[position + 2:position + 2 + length]
        position += 2 + length
        if kind == 0:
            break
        if kind == 1 and value[:1] == b'\x04' and len(value) == 7:
            record['mac'] = mac_text(value[1:])
        elif kind == 2 and value:
            record['remote_interface'] = value[1:].decode('utf-8', errors='replace') if value[0] in (5, 7, 1) \
                else (mac_text(value[1:]) if value[0] == 3 and len(value) == 7 else '')
        elif kind == 5:
            record['identity'] = value.decode('utf-8', errors='replace')
        elif kind == 6:
            record['platform'] = value.decode('utf-8', errors='replace')
        elif kind == 8 and len(value) >= 6 and value[1] == 1:   # management address, IPv4 - preferred
            record['address'] = usable(str(ipaddress.IPv4Address(value[2:6]))) or record.get('address', '')
        elif kind == 8 and len(value) >= 18 and value[1] == 2:  # management address, IPv6
            if not record.get('address'):
                record['address'] = usable(str(ipaddress.IPv6Address(value[2:18])))
    return record


# --- CDP: after the 802.3 header and LLC/SNAP, a 4-byte header, TLVs ----------
def parse_cdp(payload):
    record = {}
    position = 4
    while position + 4 <= len(payload):
        kind, length = struct.unpack('>HH', payload[position:position + 4])
        if length < 4:
            break
        value = payload[position + 4:position + length]
        position += length
        if kind == 1:
            record['identity'] = value.decode('utf-8', errors='replace')
        elif kind == 3:
            record['remote_interface'] = value.decode('utf-8', errors='replace')
        elif kind == 5:
            record['version'] = value.decode('utf-8', errors='replace').splitlines()[0][:200] if value else ''
        elif kind == 6:
            record['platform'] = value.decode('utf-8', errors='replace')
        elif kind == 2 and len(value) >= 4:   # addresses: count, then entries
            count, entry = struct.unpack('>I', value[:4])[0], value[4:]
            found_v4, found_v6 = '', ''
            for _ in range(min(count, 8)):
                if len(entry) < 2:
                    break
                protocol_length = entry[1]
                protocol = entry[2:2 + protocol_length]
                size_at = 2 + protocol_length
                size = struct.unpack('>H', entry[size_at:size_at + 2])[0]
                address = entry[size_at + 2:size_at + 2 + size]
                entry = entry[size_at + 2 + size:]
                if protocol == b'\xcc' and size == 4:
                    found_v4 = found_v4 or usable(str(ipaddress.IPv4Address(address)))
                elif protocol == bytes.fromhex('aaaa0300000086dd') and size == 16:
                    found_v6 = found_v6 or usable(str(ipaddress.IPv6Address(address)))
            if found_v4 or found_v6:
                record['address'] = found_v4 or found_v6
    return record


def parse_frame(frame):
    """(protocol, record) for an LLDP or CDP frame, else None."""
    if len(frame) < 22:
        return None
    destination, source = frame[0:6], frame[6:12]
    ethertype = struct.unpack('>H', frame[12:14])[0]
    if destination == LLDP_MAC and ethertype == 0x88CC:
        record = parse_lldp(frame[14:])
        record.setdefault('mac', mac_text(source))
        return 'lldp', record
    if destination == CDP_MAC and frame[14:17] == b'\xaa\xaa\x03' and frame[20:22] == b'\x20\x00':
        record = parse_cdp(frame[22:])
        record.setdefault('mac', mac_text(source))
        return 'cdp', record
    return None


def udp_sockets(port):
    """UDP sockets on this port: IPv4, and IPv6 (IPv6 only, beside it) where
    the host has IPv6."""
    socks = []
    families = [(socket.AF_INET, '')]
    if os.path.isfile('/proc/net/if_inet6') and os.path.getsize('/proc/net/if_inet6') > 0:
        families.append((socket.AF_INET6, '::'))
    for family, host in families:
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.setblocking(False)
        try:
            sock.bind((host, port))
            socks.append(sock)
        except OSError as error:
            print(f'Port {port} ({"IPv6" if family == socket.AF_INET6 else "IPv4"}) not heard: {error}.', flush=True)
            sock.close()
    return socks


def listen_mndp(until):
    socks = udp_sockets(5678)
    try:
        while socks and time.time() < until():
            ready, _, _ = select.select(socks, [], [], 1)
            for sock in ready:
                data, source = sock.recvfrom(2048)
                try:
                    remember(parse_mndp(data, source[0]), 'mndp')
                except (struct.error, ValueError):
                    continue
    finally:
        for sock in socks:
            sock.close()


def listen_ubnt(until):
    socks = udp_sockets(10001) + udp_sockets(10002)
    try:
        while socks and time.time() < until():
            ready, _, _ = select.select(socks, [], [], 1)
            for sock in ready:
                data, source = sock.recvfrom(2048)
                try:
                    record = parse_ubnt(data, usable(source[0]))
                except (struct.error, ValueError):
                    continue
                if record:
                    remember(record, 'ubnt')
    finally:
        for sock in socks:
            sock.close()


def listen_layer2(until):
    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(ETH_P_ALL))
    except (PermissionError, AttributeError, OSError) as error:
        print(f'LLDP and CDP not heard (raw socket refused: {error}); MNDP only.', flush=True)
        return
    sock.settimeout(1)
    try:
        while time.time() < until():
            try:
                frame, _ = sock.recvfrom(9000)
            except socket.timeout:
                continue
            try:
                found = parse_frame(frame)
            except (struct.error, ValueError):
                continue
            if found:
                remember(found[1], found[0])
    finally:
        sock.close()


def write_json(path, data):
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(data, handle)
    os.replace(temporary, path)


def write_seen():
    cutoff = time.time() - FORGET_SECONDS
    with lock:
        for key in [k for k, v in seen.items() if v.get('last_seen', 0) < cutoff]:
            del seen[key]
        data = {'written': int(time.time()), 'devices': dict(seen)}
    write_json(OUT, data)


def requested_until():
    """The end of the window RouterFleet asked for (never more than
    LONGEST_WINDOW from now), or 0."""
    try:
        with open(REQUEST, encoding='utf-8') as handle:
            until = float(json.load(handle).get('until', 0))
    except (OSError, ValueError, AttributeError):
        return 0
    return min(until, time.time() + LONGEST_WINDOW)


def main():
    if not os.path.isdir(FOLDER):
        # A compose file from before 0.36.0: no shared folder - monitoring goes on, discovery stays off
        print(f'Discovery listener off: no shared folder {FOLDER} (take the current compose file).', flush=True)
        return
    print("RouterFleet discovery listener: dormant until RouterFleet asks to discover adjacent devices.", flush=True)
    window = {'until': 0}
    threads = []
    last_write = 0
    while True:
        asked = requested_until()
        now = time.time()
        if asked > now and not any(thread.is_alive() for thread in threads):
            window['until'] = asked
            print(f'Listening for {int(asked - now)} s: MNDP, LLDP, CDP and Ubiquiti discovery on this host\'s LAN.', flush=True)
            threads = [threading.Thread(target=target, args=(lambda: window['until'],), daemon=True)
                       for target in (listen_mndp, listen_ubnt, listen_layer2)]
            for thread in threads:
                thread.start()
        elif asked > window['until']:
            window['until'] = asked   # asked again while listening: longer
        listening = any(thread.is_alive() for thread in threads)
        if (listening and now - last_write > WRITE_SECONDS) or (not listening and threads):
            write_seen()
            last_write = now
            if not listening:
                threads = []
                print('Window over: dormant again.', flush=True)
        write_json(STATE, {'alive': int(now), 'listening_until': int(window['until']) if listening else 0,
                           'devices': len(seen)})
        time.sleep(2)


if __name__ == '__main__':
    sys.exit(main())
