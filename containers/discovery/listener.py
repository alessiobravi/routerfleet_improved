"""RouterFleet discovery listener - optional (compose profile "discovery").

Hears the neighbour protocols on the LAN segment of the host it runs on:
MikroTik MNDP (UDP broadcast, port 5678), LLDP and CDP (layer-2 frames, read
with a raw socket - the container has host networking and CAP_NET_RAW, and
nothing else). Nothing is sent. What it hears goes to a JSON file on a volume
the application reads (DISCOVERY_FILE); a device not heard for a day is left
out. No network path to the application, no credentials.
"""
import ipaddress
import json
import os
import socket
import struct
import sys
import threading
import time

OUT = os.environ.get('DISCOVERY_FILE', '/discovery/seen.json')
FORGET_SECONDS = 86400
WRITE_SECONDS = 30
LLDP_MAC = bytes.fromhex('0180c200000e')
CDP_MAC = bytes.fromhex('01000ccccccc')
ETH_P_ALL = 0x0003
seen = {}
lock = threading.Lock()


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
    record = {'address': source}
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
        elif kind == 8 and len(value) >= 6 and value[1] == 1:   # management address, IPv4
            record['address'] = str(ipaddress.IPv4Address(value[2:6]))
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
            entry = value[4:]
            if len(entry) >= 9 and entry[0] == 1 and entry[1] == 1 and entry[2] == 0xCC:
                size = struct.unpack('>H', entry[3:5])[0]
                if size == 4:
                    record['address'] = str(ipaddress.IPv4Address(entry[5:9]))
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


def listen_mndp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', 5678))
    while True:
        data, (address, _) = sock.recvfrom(2048)
        try:
            remember(parse_mndp(data, address), 'mndp')
        except (struct.error, ValueError):
            continue


def listen_layer2():
    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(ETH_P_ALL))
    except (PermissionError, AttributeError, OSError) as error:
        print(f'LLDP and CDP not heard (raw socket refused: {error}); MNDP only.', flush=True)
        return
    while True:
        frame, _ = sock.recvfrom(9000)
        try:
            found = parse_frame(frame)
        except (struct.error, ValueError):
            continue
        if found:
            remember(found[1], found[0])


def write_out():
    while True:
        time.sleep(WRITE_SECONDS)
        cutoff = time.time() - FORGET_SECONDS
        with lock:
            for key in [k for k, v in seen.items() if v.get('last_seen', 0) < cutoff]:
                del seen[key]
            data = {'written': int(time.time()), 'devices': dict(seen)}
        temporary = OUT + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
        os.replace(temporary, OUT)


def main():
    print('RouterFleet discovery listener: MNDP, LLDP and CDP on this host\'s LAN.', flush=True)
    for target in (listen_mndp, listen_layer2):
        threading.Thread(target=target, daemon=True).start()
    write_out()


if __name__ == '__main__':
    sys.exit(main())
