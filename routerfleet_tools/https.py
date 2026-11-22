"""HTTPS settings: what System settings decides, and how nginx is told.

The application and the nginx container share the certificate volume
(/certificate). The application writes there:

    routerfleet-https.json   the desired mode - data only, never configuration
    custom/fullchain.pem     an uploaded certificate and its chain
    custom/privkey.pem       its private key (0600)

nginx watches the volume, checks the values again, renders its configuration
from fixed templates, tests it, reloads, and writes back
routerfleet-https-status.json: what it applied, or why it kept the working
configuration. Let's Encrypt (HTTP-01) runs in the nginx container, which owns
port 80.
"""
import datetime
import ipaddress
import json
import os
import re
import tempfile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http.request import split_domain_port

STATE_FILE = 'routerfleet-https.json'
STATUS_FILE = 'routerfleet-https-status.json'
CUSTOM_DIR = 'custom'

TLS_MODES = ('letsencrypt', 'custom', 'selfsigned')
HTTPS_OFF_MODES = ('proxy', 'plain')

# A host name (RFC 1123) with at least one dot: Let's Encrypt and browsers'
# passkeys both need a real domain name
DOMAIN = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$')


def cert_dir():
    return getattr(settings, 'HTTPS_CERT_DIR', '/certificate')


def volume_shared():
    """Whether this container sees the certificate volume nginx uses. A
    compose file from before this feature does not mount it here."""
    directory = cert_dir()
    return os.path.isdir(directory) and os.access(directory, os.W_OK)


def _write_atomically(path, data, mode=0o600):
    """Owner-only by default: both containers that read these files (the
    application and nginx) run as root, so nobody else needs them."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=directory, prefix='.tmp-')
    try:
        with os.fdopen(handle, 'wb') as file:
            file.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


# --- Validation ---------------------------------------------------------------

def clean_domain(value):
    domain = (value or '').strip().lower().rstrip('.')
    if not DOMAIN.match(domain):
        raise ValidationError('Enter a domain name such as routerfleet.example.com - not an IP address.')
    return domain


def clean_email(value):
    email = (value or '').strip()
    validate_email(email)
    if any(c in email for c in ' ;\'"{}\\'):
        raise ValidationError('Enter a valid email address.')
    return email


def clean_proxies(value):
    networks = []
    for line in re.split(r'[\s,]+', value or ''):
        if not line:
            continue
        try:
            networks.append(str(ipaddress.ip_network(line, strict=False)))
        except ValueError:
            raise ValidationError(f'"{line}" is not an IP address or network.') from None
    if not networks:
        raise ValidationError('List the address of the reverse proxy, or its network (one per line).')
    if any(network in ('0.0.0.0/0', '::/0') for network in networks):
        raise ValidationError('Trusting every address would let any client claim to be the proxy.')
    return networks


def _names_of(certificate):
    from cryptography import x509
    try:
        extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        return [name.lower() for name in extension.value.get_values_for_type(x509.DNSName)]
    except x509.ExtensionNotFound:
        return []


def _covers(names, domain):
    for name in names:
        if name == domain:
            return True
        if name.startswith('*.') and domain.count('.') == name.count('.') and domain.endswith(name[1:]):
            return True
    return False


def check_certificate(certificate_pem, key_pem, domain):
    """An uploaded certificate, checked before it is used: what it is, or a
    ValidationError saying why it can not be used. The chain may follow the
    certificate in the same text."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    try:
        chain = x509.load_pem_x509_certificates(certificate_pem.encode())
    except ValueError:
        raise ValidationError('The certificate is not a PEM certificate (-----BEGIN CERTIFICATE-----).') from None
    try:
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except (ValueError, TypeError):
        raise ValidationError('The private key is not an unencrypted PEM key (-----BEGIN PRIVATE KEY-----). '
                              'A key protected by a passphrase can not be used by nginx.') from None
    leaf = chain[0]
    public = serialization.PublicFormat.SubjectPublicKeyInfo
    if leaf.public_key().public_bytes(serialization.Encoding.DER, public) != \
            key.public_key().public_bytes(serialization.Encoding.DER, public):
        raise ValidationError('The private key does not belong to the certificate.')
    now = datetime.datetime.now(datetime.timezone.utc)
    if leaf.not_valid_after_utc <= now:
        raise ValidationError(f'The certificate expired on {leaf.not_valid_after_utc:%Y-%m-%d}.')
    if leaf.not_valid_before_utc > now:
        raise ValidationError(f'The certificate is valid only from {leaf.not_valid_before_utc:%Y-%m-%d}.')
    names = _names_of(leaf)
    if domain and not _covers(names, domain):
        raise ValidationError(f'The certificate is not issued for {domain}: it names '
                              f'{", ".join(names) or "no domain"}.')
    return {
        'subject': leaf.subject.rfc4514_string()[:255],
        'issuer': leaf.issuer.rfc4514_string()[:255],
        'not_after': leaf.not_valid_after_utc,
        'names': names,
        'trusted': trusted_by_browsers(chain, domain or (names[0] if names else '')),
        'pem': b''.join(c.public_bytes(serialization.Encoding.PEM) for c in chain),
        'key': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption()),
    }


def trusted_by_browsers(chain, domain):
    """Whether the chain leads to an authority browsers trust (the Mozilla
    store, as certifi carries it) for that domain."""
    if not domain or domain.startswith('*'):
        return False
    try:
        import certifi
        from cryptography import x509
        from cryptography.x509.verification import PolicyBuilder, Store
        with open(certifi.where(), 'rb') as handle:
            store = Store(x509.load_pem_x509_certificates(handle.read()))
        verifier = PolicyBuilder().store(store).build_server_verifier(x509.DNSName(domain))
        verifier.verify(chain[0], chain[1:])
        return True
    except Exception:
        return False


# --- Applying -------------------------------------------------------------------

def write_state(https_settings):
    """Tells nginx what to serve. Raises OSError when the volume is not
    shared with this container."""
    https_settings.generation += 1
    https_settings.save(update_fields=['generation', 'updated'])
    state = {'mode': https_settings.mode, 'generation': https_settings.generation}
    if https_settings.mode == 'letsencrypt':
        state.update(domain=https_settings.domain, email=https_settings.email)
    if https_settings.mode in ('custom', 'selfsigned') and https_settings.domain:
        state['domain'] = https_settings.domain
    if https_settings.mode == 'proxy':
        state['trusted_proxies'] = clean_proxies(https_settings.trusted_proxies)
    _write_atomically(os.path.join(cert_dir(), STATE_FILE), json.dumps(state, indent=2).encode())
    return state


def store_custom_certificate(checked):
    directory = os.path.join(cert_dir(), CUSTOM_DIR)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    _write_atomically(os.path.join(directory, 'privkey.pem'), checked['key'])
    _write_atomically(os.path.join(directory, 'fullchain.pem'), checked['pem'])


PENDING_DIR = 'pending'


def _pending_path(token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', token or ''):
        raise ValueError('bad token')
    return os.path.join(cert_dir(), CUSTOM_DIR, PENDING_DIR, token)


def store_pending_certificate(token, checked):
    """A certificate under review waits here, its key 0600, until it is
    confirmed or the review expires - never in the session table."""
    import shutil
    base = os.path.join(cert_dir(), CUSTOM_DIR, PENDING_DIR)
    os.makedirs(base, mode=0o700, exist_ok=True)
    # Reviews that were never confirmed are not kept beyond an hour
    for name in os.listdir(base):
        path = os.path.join(base, name)
        if os.path.isdir(path) and os.path.getmtime(path) < __import__('time').time() - 3600:
            shutil.rmtree(path, ignore_errors=True)
    path = _pending_path(token)
    os.makedirs(path, mode=0o700, exist_ok=True)
    _write_atomically(os.path.join(path, 'privkey.pem'), checked['key'])
    _write_atomically(os.path.join(path, 'fullchain.pem'), checked['pem'])
    return True


def activate_pending_certificate(token):
    path = _pending_path(token)
    try:
        with open(os.path.join(path, 'privkey.pem'), 'rb') as handle:
            key = handle.read()
        with open(os.path.join(path, 'fullchain.pem'), 'rb') as handle:
            pem = handle.read()
    except OSError:
        return False
    store_custom_certificate({'key': key, 'pem': pem})
    discard_pending_certificate(token)
    return True


def discard_pending_certificate(token):
    import shutil
    try:
        shutil.rmtree(_pending_path(token), ignore_errors=True)
    except ValueError:
        pass


def read_status():
    """What nginx last applied, as it wrote it - or None."""
    try:
        with open(os.path.join(cert_dir(), STATUS_FILE)) as handle:
            status = json.load(handle)
        return status if isinstance(status, dict) else None
    except (OSError, ValueError):
        return None


def served_certificate():
    """The certificate nginx serves in .env mode - the one already deployed in
    the volume, kept as it is on upgrade: self-signed, or one an administrator
    put there, possibly trusted by browsers."""
    from cryptography import x509
    try:
        with open(os.path.join(cert_dir(), 'nginx.pem'), 'rb') as handle:
            chain = x509.load_pem_x509_certificates(handle.read())
    except (OSError, ValueError):
        return None
    certificate = chain[0]
    names = _names_of(certificate)
    self_signed = certificate.issuer == certificate.subject
    return {'self_signed': self_signed,
            'not_after': certificate.not_valid_after_utc,
            'subject': certificate.subject.rfc4514_string(),
            'names': names,
            'trusted': not self_signed and any(trusted_by_browsers(chain, name) for name in names[:5])}


# --- Passkeys -------------------------------------------------------------------

def _is_ip(host):
    try:
        ipaddress.ip_address(host.strip('[]'))
        return True
    except ValueError:
        return False


def passkey_requirements(https_settings, request=None, mode=None, status=None):
    """The three things browsers need for passkeys, each True, False or None
    (unknown: decided by a reverse proxy RouterFleet can not see)."""
    mode = mode or https_settings.mode
    status = status if status is not None else (read_status() or {})
    host = split_domain_port(request.get_host())[0] if request else ''
    domain = https_settings.domain or host
    if mode == 'letsencrypt':
        trusted = bool(status.get('mode') == 'letsencrypt' and status.get('certificate_issued'))
    elif mode == 'custom':
        trusted = https_settings.custom_trusted
    elif mode == 'proxy':
        trusted = None
    elif mode == 'env':
        served = served_certificate()
        trusted = bool(served and served['trusted'])
    else:
        trusted = False
    if mode in TLS_MODES:
        https = True
    elif mode == 'plain':
        https = False
    elif mode == 'proxy':
        https = bool(request and request.is_secure()) or None
    else:
        https = getattr(settings, 'SESSION_COOKIE_SECURE', False) or bool(request and request.is_secure())
    named = bool(domain) and not _is_ip(domain)
    return [
        ('Served over HTTPS', https),
        ('Reached by a domain name, not an IP address', named),
        ('A certificate browsers trust', trusted),
    ]


def passkeys_work(requirements):
    return all(value is True for _, value in requirements)


def passkey_users():
    from user_manager.models import UserPasskey
    return UserPasskey.objects.values('user').distinct().count()
