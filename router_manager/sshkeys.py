"""SSH keys: checking a key with its passphrase, deriving its public key,
protecting an unprotected key with the passphrase it was given, and generating
a new key of the type chosen."""
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from routerlib.functions import KeyPassphraseError, load_private_key_from_string


class KeyProblem(Exception):
    pass


# The types a new key can be generated as - by algorithm and size, the user's
# choice: (value, label, description). Ed25519 first: the default.
KEY_TYPES = [
    ('ed25519', 'Ed25519', 'Elliptic curve, 256 bits: a short key, fast, the modern default'),
    ('ecdsa-256', 'ECDSA P-256', 'Elliptic curve NIST P-256'),
    ('ecdsa-384', 'ECDSA P-384', 'Elliptic curve NIST P-384'),
    ('ecdsa-521', 'ECDSA P-521', 'Elliptic curve NIST P-521'),
    ('rsa-2048', 'RSA 2048', 'RSA, 2048 bits'),
    ('rsa-3072', 'RSA 3072', 'RSA, 3072 bits'),
    ('rsa-4096', 'RSA 4096', 'RSA, 4096 bits: the largest, slowest to use'),
]
CURVES = {'256': ec.SECP256R1, '384': ec.SECP384R1, '521': ec.SECP521R1}
RSA_SIZES = {'2048', '3072', '4096'}


def generate(kind):
    """A new private key of that type, unprotected, in OpenSSH format (the
    caller protects it with a passphrase through checked())."""
    family, _, size = kind.partition('-')
    if kind == 'ed25519':
        key = ed25519.Ed25519PrivateKey.generate()
    elif family == 'ecdsa' and size in CURVES:
        key = ec.generate_private_key(CURVES[size]())
    elif family == 'rsa' and size in RSA_SIZES:
        key = rsa.generate_private_key(public_exponent=65537, key_size=int(size))
    else:
        raise KeyProblem('Choose one of the key types offered.')
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                             serialization.NoEncryption()).decode()


def describe(public_key):
    """(kind for the icon, label, SHA256 fingerprint) of a public key line -
    the fingerprint as ssh-keygen -l and devices show it. ('', '', '') when
    the line is not one."""
    import base64
    import hashlib
    parts = (public_key or '').split()
    if len(parts) < 2:
        return '', '', ''
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError:
        return '', '', ''
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    algorithm = parts[0]
    if algorithm == 'ssh-ed25519':
        return 'ed25519', 'Ed25519', fingerprint
    if algorithm.startswith('ecdsa-sha2-nistp'):
        return 'ecdsa', f"ECDSA P-{algorithm.removeprefix('ecdsa-sha2-nistp')}", fingerprint
    if algorithm == 'ssh-rsa':
        try:
            import paramiko
            return 'rsa', f'RSA {paramiko.RSAKey(data=blob).get_bits()}', fingerprint
        except Exception:
            return 'rsa', 'RSA', fingerprint
    return 'other', algorithm, fingerprint


def comment_for(name):
    """The comment ending a generated key's public line - the key's name,
    without spaces, so a device lists the key under a recognisable name."""
    return re.sub(r'[^A-Za-z0-9._@+-]+', '-', (name or '').strip()).strip('-')[:60] or 'routerfleet'


def checked(private_key, passphrase):
    """(private key as it is stored, public key line) for a key and its
    passphrase - raises KeyProblem with the reason when they do not open.
    A key given without protection and with a passphrase is stored protected
    by it (OpenSSH format)."""
    private_key = (private_key or '').strip() + '\n'
    try:
        key = load_private_key_from_string(private_key, passphrase or None)
    except KeyPassphraseError as error:
        raise KeyProblem(f'The private key could not be opened: {error}.') from None
    if key is None:
        raise KeyProblem('This is not a private key RouterFleet can use (RSA, ECDSA or Ed25519, PEM or OpenSSH format).')
    public = f'{key.get_name()} {key.get_base64()}'
    if passphrase:
        try:
            loaded = serialization.load_ssh_private_key(private_key.encode(), password=None)
        except (TypeError, ValueError):
            loaded = None   # protected already, or PEM: kept as given
        if loaded is not None:
            private_key = loaded.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                serialization.BestAvailableEncryption(passphrase.encode())).decode()
    return private_key, public


def reprotected(private_key, old_passphrase, new_passphrase):
    """The stored key, opened with its old passphrase and written again
    protected by the new one - or unprotected when there is none (OpenSSH
    format). Raises KeyProblem when the old passphrase does not open it."""
    data = (private_key or '').strip().encode() + b'\n'
    password = old_passphrase.encode() if old_passphrase else None
    try:
        try:
            key = serialization.load_ssh_private_key(data, password=password)
        except ValueError:
            key = serialization.load_pem_private_key(data, password=password)
    except (TypeError, ValueError):
        raise KeyProblem('The stored key could not be opened with its stored passphrase.') from None
    encryption = (serialization.BestAvailableEncryption(new_passphrase.encode()) if new_passphrase
                  else serialization.NoEncryption())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, encryption).decode()
