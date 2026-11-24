"""SSH keys: checking a key with its passphrase, deriving its public key, and
protecting an unprotected key with the passphrase it was given."""
from cryptography.hazmat.primitives import serialization

from routerlib.functions import KeyPassphraseError, load_private_key_from_string


class KeyProblem(Exception):
    pass


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
