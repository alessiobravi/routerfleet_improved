import base64
import csv
import hashlib
import io
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

_fernet = None


def get_fernet():
    """Return a cached Fernet instance.

    The key comes from settings.ENCRYPTION_KEY, which the container entrypoint
    generates once and stores in the persistent /app_secrets volume (same
    pattern as MONITORING_KEY). Outside docker it falls back to a key derived
    from SECRET_KEY so development keeps working out of the box.
    """
    global _fernet
    if _fernet is None:
        encryption_key = getattr(settings, 'ENCRYPTION_KEY', None)
        if not encryption_key:
            encryption_key = base64.urlsafe_b64encode(
                hashlib.sha256(settings.SECRET_KEY.encode()).digest()
            ).decode()
        _fernet = Fernet(encryption_key.encode())
    return _fernet


def encrypt_value(value):
    """Encrypt a plaintext value. Empty values pass through untouched.

    Idempotent: values that are already valid Fernet tokens (e.g. during a data
    migration that assigns encrypted values through a model save) are returned
    unchanged instead of being encrypted twice.
    """
    if value in (None, ''):
        return value
    value = str(value)
    try:
        get_fernet().decrypt(value.encode())
        return value
    except InvalidToken:
        if looks_encrypted(value):
            # Encrypted under another key (a lost or replaced app_secrets):
            # kept as it is, never encrypted a second time - it can still be
            # read once the right key is back
            return value
        return get_fernet().encrypt(value.encode()).decode()


def looks_encrypted(value):
    """A Fernet token: version byte 0x80 in base64 ('gAAAAA'), and long."""
    return isinstance(value, str) and value.startswith('gAAAAA') and len(value) >= 100


UNREADABLE_FLAG = 'rf-undecryptable'


def note_unreadable():
    """Remembered for a day, across workers: the status page warns."""
    try:
        from django.core.cache import caches
        caches['limits'].set(UNREADABLE_FLAG, 1, 86400)
    except Exception:   # noqa: BLE001 - a warning must never break a read
        pass


def unreadable_seen():
    try:
        from django.core.cache import caches
        return bool(caches['limits'].get(UNREADABLE_FLAG))
    except Exception:   # noqa: BLE001
        return False


def decrypt_value(value):
    """Decrypt a value produced by encrypt_value. Empty values pass through."""
    if value in (None, ''):
        return value
    try:
        return get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        # Two different things land here and they cannot be told apart from the
        # token alone: a value that is not encrypted yet - which is normal while
        # the migration that encrypts the column is still running, and harmless,
        # because the value is handed back unchanged - and a value encrypted
        # under a different key, which is the case worth worrying about. The
        # message therefore names both instead of asserting the alarming one at
        # every upgrade.
        if looks_encrypted(value):
            # Encrypted, but not with this key: the ciphertext is never handed
            # on - not sent to a device as its password, not used as a TOTP
            # secret. Empty instead, and the status page says why.
            logger.error('A stored secret was encrypted with a different key than the configured ENCRYPTION_KEY '
                         '(app_secrets lost or replaced?). It reads as empty until the right key is back.')
            note_unreadable()
            return ''
        logger.warning(
            'Could not decrypt a stored value. Either it is not encrypted yet '
            '(normal while the upgrade that encrypts it is running), or it was '
            'encrypted with a different key than the configured ENCRYPTION_KEY.')
        return value


def mask_passwords_in_csv(raw_csv_data):
    """Rewrite CSV text with every value in the 'password' column replaced by '********'.

    Used so that imported CSV data never keeps the password in cleartext at rest.
    Unparseable input is returned unchanged so no data is lost.
    """
    try:
        csv_file = io.StringIO(raw_csv_data)
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or 'password' not in reader.fieldnames:
            return raw_csv_data
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=reader.fieldnames, extrasaction='ignore')
        writer.writeheader()
        for row in reader:
            if row.get('password'):
                row['password'] = '********'
            writer.writerow(row)
        return output.getvalue()
    except csv.Error:
        logger.warning('Could not parse CSV data to mask passwords')
        return raw_csv_data


class EncryptedTextField(models.TextField):
    """TextField that stores its value encrypted at rest.

    Reads return the plaintext, so existing code that uses the field value
    (e.g. connecting to a router over SSH) keeps working unchanged. Both
    instance save() and queryset.update() apply get_prep_value, so values are
    encrypted at rest either way.
    """

    def get_prep_value(self, value):
        if value is None:
            return None
        return encrypt_value(str(value))

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        if isinstance(value, (bytes, memoryview)):
            value = value.decode()
        return decrypt_value(value)
