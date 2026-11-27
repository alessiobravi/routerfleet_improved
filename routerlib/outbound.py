"""HTTPS requests the server makes to addresses an administrator configured
(vendor release lists, update channels): never to RouterFleet's own networks,
and to exactly the address that was checked.

The name is resolved once and checked (console.network.check: no loopback,
link-local, reserved or internal address); the connection then goes to that
IP - not to whatever a second lookup would answer, which is how a name that
changes its answer (DNS rebinding) would otherwise slip past the check. The
certificate is still verified against the name, and the name is what the
server is told (SNI, Host). No redirect is followed, and at most MAX_BYTES of
the answer are read.
"""
import json as jsonlib
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 10


class _PinnedAdapter(HTTPAdapter):
    """Connects to the checked IP while TLS checks and announces the name."""

    def __init__(self, hostname, **kwargs):
        self._hostname = hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs['server_hostname'] = self._hostname
        pool_kwargs['assert_hostname'] = self._hostname
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)


class Answer:
    """What callers need of a response: status, text, JSON."""

    def __init__(self, status_code, content, encoding):
        self.status_code = status_code
        self.content = content
        self.encoding = encoding or 'utf-8'

    @property
    def text(self):
        return self.content.decode(self.encoding, errors='replace')

    def json(self):
        return jsonlib.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}')


def https_get(url, headers=None, limit=MAX_BYTES):
    from console import network
    parts = urlsplit(url or '')
    if parts.scheme != 'https' or not parts.hostname:
        raise ValueError('only https:// addresses are fetched')
    port = parts.port or 443
    try:
        address = network.check(parts.hostname, port)
    except network.Refused as refused:
        raise ValueError(str(refused)) from None
    host_in_url = f'[{address}]' if ':' in address else address
    pinned = urlunsplit(('https', f'{host_in_url}:{port}', parts.path or '/', parts.query, ''))
    host_header = parts.hostname if port == 443 else f'{parts.hostname}:{port}'
    session = requests.Session()
    session.mount('https://', _PinnedAdapter(parts.hostname))
    with session, session.get(pinned, headers={**(headers or {}), 'Host': host_header}, timeout=TIMEOUT,
                              allow_redirects=False, stream=True) as response:
        chunks, total = [], 0
        for chunk in response.iter_content(65536):
            total += len(chunk)
            if total > limit:
                raise ValueError(f'the answer is larger than {limit // 1024} kB')
            chunks.append(chunk)
        return Answer(response.status_code, b''.join(chunks), response.encoding)
