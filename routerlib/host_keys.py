"""Trust on first use for the SSH host keys of devices.

The first successful connection to an address and port stores the key the
device presented. Every later connection must see that same key: paramiko
compares it before authenticating, so an impostor never receives the password
or a signature made with the SSH key. A device that was reset or replaced
legitimately has its stored key removed with "Forget host key" on its page.
"""
import base64
import hashlib

import paramiko


class HostKeyChanged(paramiko.SSHException):
    pass


def fingerprint(key):
    """SHA256:... as OpenSSH prints it."""
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


def known_hosts_name(address, port):
    return address if int(port) == 22 else f'[{address}]:{port}'


def _stored(address, port):
    from router_manager.models import SSHHostKey
    return SSHHostKey.objects.filter(address=address, port=int(port)).first()


class TrustOnFirstUse(paramiko.MissingHostKeyPolicy):
    """Called by paramiko when it finds no known key under the name it looks the
    host up by. With nothing stored, that is a first connection and it goes
    ahead (the key is stored once the connection has succeeded). A stored key
    is loaded under that name, so paramiko itself compares it - also against a
    key of another type - and raises BadHostKeyException; refusing here as
    well means a stored key can never be bypassed by a lookup that misses."""

    def __init__(self, stored):
        self.stored = stored

    def missing_host_key(self, client, hostname, key):
        if self.stored is not None:
            raise HostKeyChanged(changed_message(self.stored, key))


def changed_message(stored, key):
    return (f'The SSH host key of this device has changed: expected {stored.key_type} {stored.fingerprint}, '
            f'got {key.get_name()} {fingerprint(key)}. Nothing was sent to it. If the device was reset or '
            f'replaced, use "Forget host key" on its page; otherwise someone may be impersonating it.')


def prepare(ssh_client, address, port):
    """Load the stored key into the client and install the policy."""
    stored = _stored(address, port)
    if stored is not None:
        key = paramiko.PKey.from_type_string(stored.key_type, base64.b64decode(stored.key_base64))
        ssh_client.get_host_keys().add(known_hosts_name(address, port), stored.key_type, key)
    ssh_client.set_missing_host_key_policy(TrustOnFirstUse(stored))
    return stored


def remember(ssh_client, address, port, stored):
    """After a successful connection: store the key on first use."""
    if stored is not None:
        return
    key = ssh_client.get_transport().get_remote_server_key()
    from router_manager.models import SSHHostKey
    SSHHostKey.objects.get_or_create(address=address, port=int(port), defaults={
        'key_type': key.get_name(), 'key_base64': key.get_base64(), 'fingerprint': fingerprint(key)})


def forget(address, port):
    from router_manager.models import SSHHostKey
    return SSHHostKey.objects.filter(address=address, port=int(port)).delete()[0]


def stored_key(address, port):
    return _stored(address, port)
