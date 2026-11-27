"""Neighbour tables, as devices print them: MikroTik's /ip neighbor (MNDP, CDP
and LLDP together), Cisco CDP and LLDP details, Juniper and Huawei LLDP, and
lldpd's key=value output (OpenWrt with lldpd). The format is recognised by
the output itself, so a device type only names the command.

Each neighbour comes back as a dict with any of: identity, address, mac,
platform, model, version, interface (the port it was seen on), remote_interface
(its own port), protocols (a set).

Written from the vendors' documented output; confirm on your devices.
"""
import re

MAC = re.compile(r'^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$|^([0-9a-f]{4}\.){2}[0-9a-f]{4}$|^[0-9a-f]{12}$|^([0-9a-f]{4}-){2}[0-9a-f]{4}$', re.I)
IPV4 = re.compile(r'\b(\d{1,3}(?:\.\d{1,3}){3})\b')


def usable_address(*candidates):
    """The first address a device can be reached at: IPv4 or IPv6, never a
    link-local one (fe80::, which needs its interface to mean anything)."""
    import ipaddress
    for candidate in candidates:
        for word in re.split(r'[\s,]+', candidate or ''):
            try:
                address = ipaddress.ip_address(word.strip().split('%')[0])
            except ValueError:
                continue
            if not (address.is_link_local or address.is_multicast or address.is_unspecified or address.is_loopback):
                return str(address)
    return ''


def normal_mac(value):
    """aa:bb:cc:dd:ee:ff for a MAC address in any common spelling, else ''."""
    text = (value or '').strip()
    if re.match(r'^([0-9a-f]{2} ){5}[0-9a-f]{2}$', text, re.I):
        text = text.replace(' ', ':')
    if not MAC.match(text):
        return ''
    digits = re.sub(r'[^0-9a-f]', '', text.lower())
    return ':'.join(digits[i:i + 2] for i in range(0, 12, 2)) if len(digits) == 12 else ''


def _clean(record):
    out = {key: (value.strip()[:200] if isinstance(value, str) else value) for key, value in record.items() if value}
    if out.get('mac'):
        out['mac'] = normal_mac(out['mac'])
    return out if (out.get('identity') or out.get('address') or out.get('mac')) else None


def _routeros(text):
    """`/ip neighbor print terse` (one line per neighbour) or `print detail`
    (a record starts with its number): key=value pairs, values maybe quoted."""
    records, current = [], []
    for line in text.splitlines():
        if re.match(r'^\s*\d+\s+[A-Z]?\s*[\w-]+=', line) and current:
            records.append(' '.join(current))
            current = []
        if line.strip():
            current.append(line.strip())
    if current:
        records.append(' '.join(current))
    found = []
    for record in records:
        # A value runs to the next key=: terse output does not quote "7.16.2 (stable)"
        pairs = {k: v.strip().strip('"') for k, v in
                     re.findall(r'([\w-]+)=("[^"]*"|.*?)(?=\s+[\w-]+=|\s*$)', record)}
        if not pairs:
            continue
        found.append(_clean({
            'identity': pairs.get('identity'),
            'address': usable_address(pairs.get('address4'), pairs.get('address'), pairs.get('address6')),
            'mac': pairs.get('mac-address'), 'platform': pairs.get('platform'), 'model': pairs.get('board'),
            'version': pairs.get('version'), 'interface': pairs.get('interface'),
            'remote_interface': pairs.get('interface-name'),
            'protocols': {p.strip() for p in (pairs.get('discovered-by') or 'mndp').split(',') if p.strip()}}))
    return found


def _blocks(text, separator):
    return [block for block in re.split(separator, text) if block.strip()]


def _field(block, label):
    match = re.search(rf'(?im)^\s*{label}\s*:\s*(.*?)\s*$', block)
    return match.group(1) if match else ''


def _cisco_cdp(text):
    found = []
    for block in _blocks(text, r'(?m)^-{5,}\s*$'):
        if 'Device ID' not in block:
            continue
        platform = _field(block, 'Platform').split(',')[0]
        version_match = re.search(r'(?im)^\s*Version\s*:\s*\n\s*(.+)$', block)
        interface = re.search(r'(?im)^\s*Interface:\s*([^,]+),\s*Port ID \(outgoing port\):\s*(\S+)', block)
        entry = block.split('Platform:')[0]
        address = IPV4.search(_field(block, 'IP address') or entry)
        address = address.group(1) if address else usable_address(_field(block, 'IPv6 address').split('(')[0])
        found.append(_clean({
            'identity': _field(block, 'Device ID').split('(')[0], 'address': address,
            'platform': platform, 'model': platform.replace('cisco ', '').replace('Cisco ', ''),
            'version': version_match.group(1) if version_match else '',
            'interface': interface.group(1) if interface else '', 'remote_interface': interface.group(2) if interface else '',
            'protocols': {'cdp'}}))
    return found


def _cisco_lldp(text):
    found = []
    for block in _blocks(text, r'(?m)^-{5,}\s*$'):
        if 'Local Intf' not in block and 'System Name' not in block:
            continue
        description = re.search(r'(?im)^\s*System Description:\s*\n\s*(.+)$', block)
        address = re.search(r'(?im)^\s*IP:\s*(\d{1,3}(?:\.\d{1,3}){3})', block)
        address = address.group(1) if address else usable_address(_field(block, 'IPV6'))
        found.append(_clean({
            'identity': _field(block, 'System Name'), 'address': address,
            'mac': _field(block, 'Chassis id'), 'platform': description.group(1) if description else '',
            'interface': _field(block, 'Local Intf'), 'remote_interface': _field(block, 'Port id'),
            'protocols': {'lldp'}}))
    return found


def _huawei(text):
    """`display lldp neighbor` and `display cdp neighbor` (CDP received with
    `lldp compliance cdp receive`): per port "<port> has N neighbor(s):", then
    blocks from "Neighbor index :"; CDP blocks name the device "Device ID"."""
    found, port = [], ''
    parts = re.split(r'(?im)^(\S+ has \d+ neighbor\(s\):|\s*Neighbor index\s*:.*)$', text)
    for part in parts:
        header = re.match(r'(\S+) has \d+ neighbor', part or '')
        if header:
            port = header.group(1)
            continue
        if not part or ('System name' not in part and 'Device ID' not in part):
            continue
        cdp = bool(re.search(r'(?im)^\s*Device ID\s*:', part))
        version = re.search(r'(?ims)^\s*Version\s*:(.*?)^\s*Platform\s*:', part)
        found.append(_clean({
            'identity': _field(part, 'Device ID') if cdp else _field(part, 'System name'),
            'address': usable_address(*re.findall(r'(?im)^\s*Management address\s*:\s*(\S+)', part)),
            'mac': _field(part, 'MacAddress') if cdp else _field(part, 'Chassis ID'),
            'platform': _field(part, 'Platform') if cdp else _field(part, 'System description'),
            'version': ' '.join(version.group(1).split())[:200] if cdp and version else '',
            'interface': port, 'remote_interface': _field(part, 'Port ID'),
            'protocols': {'cdp' if cdp else 'lldp'}}))
    return found


def _lldpcli(text):
    """`lldpcli -f keyvalue show neighbors details`: lldp.<port>.<field>=value."""
    by_port = {}
    for match in re.finditer(r'(?m)^lldp\.([^.=\s]+)\.(\S+?)=(.*)$', text):
        by_port.setdefault(match.group(1), {})[match.group(2)] = match.group(3)
    found = []
    for port, fields in by_port.items():
        via = (fields.get('via') or 'LLDP').upper()
        found.append(_clean({
            'identity': fields.get('chassis.name') or fields.get('chassis.local'),
            'address': usable_address(fields.get('chassis.mgmt-ip'), fields.get('chassis.mgmt-ip6')),
            'mac': fields.get('chassis.mac'), 'platform': fields.get('chassis.descr'),
            'interface': port, 'remote_interface': fields.get('port.ifname') or fields.get('port.descr'),
            'protocols': {'cdp' if via.startswith('CDP') else 'lldp'}}))
    return found


def _junos(text):
    """`show lldp neighbors`: a table - Local Interface, Parent Interface,
    Chassis Id, Port info, System Name (columns two or more spaces apart)."""
    found = []
    lines = text.splitlines()
    header = next((i for i, line in enumerate(lines) if 'Local Interface' in line and 'Chassis Id' in line), None)
    if header is None:
        return found
    for line in lines[header + 1:]:
        columns = re.split(r'\s{2,}', line.strip())
        if len(columns) < 4:
            continue
        mac = next((c for c in columns if normal_mac(c)), '')
        found.append(_clean({'interface': columns[0], 'mac': mac, 'remote_interface': columns[-2],
                             'identity': columns[-1], 'protocols': {'lldp'}}))
    return found


def _junos_detail(text):
    """`show lldp neighbors detail` (Junos 19.1R2 and later) or per interface:
    blocks from "LLDP Neighbor Information:"."""
    found = []
    for block in re.split(r'(?m)^\s*LLDP Neighbor Information:\s*$', text)[1:]:
        management = block.split('Management address', 1)[1] if 'Management address' in block else ''
        address = usable_address(*re.findall(r'(?im)^\s*Address\s*:\s*(\S+)', management))
        found.append(_clean({
            'identity': _field(block, 'System name'), 'address': address,
            'mac': _field(block, 'Chassis ID'), 'platform': _field(block, 'System Description'),
            'interface': _field(block, 'Local Interface'),
            'remote_interface': _field(block, 'Port description') or _field(block, 'Port ID'),
            'protocols': {'lldp'}}))
    return found


def parse(text):
    """Every neighbour in the output of one or more neighbour commands."""
    text = (text or '').replace('\r', '')
    found = []
    if re.search(r'(?m)^lldp\.\S+=', text):
        found += _lldpcli(text)
    if re.search(r'\b(identity|mac-address|discovered-by)=', text):
        found += _routeros(text)
    if re.search(r'(?m)^Device ID: ', text):
        found += _cisco_cdp(text)
    if 'Local Intf' in text:
        found += _cisco_lldp(text)
    if re.search(r'(?im)^\s*Neighbor index\s*:', text):
        found += _huawei(text)
    if re.search(r'(?m)^\s*LLDP Neighbor Information:', text):
        found += _junos_detail(text)
    if 'Local Interface' in text and 'Chassis Id' in text:
        found += _junos(text)
    return [record for record in found if record]
