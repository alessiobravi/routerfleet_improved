"""Second factors: authenticator-app codes (TOTP) and passkeys (WebAuthn).

TOTP is RFC 6238 with the parameters every authenticator app uses by default:
HMAC-SHA1, 6 digits, 30-second steps. A code is accepted in the step it
belongs to and the one either side of it, to allow for clock drift, and never
twice: the last step used is stored, and a code from that step or an earlier
one is refused. That makes a code seen over someone's shoulder useless.

Passkeys use the browser's WebAuthn API. The relying party is the name the
installation is reached by, so a passkey made for one name does not work for
another, and browsers refuse to use an IP address - passkeys need a DNS name
and a certificate the browser trusts.
"""
import base64
import hashlib
import hmac
import ipaddress
import os
import struct
import time
from urllib.parse import quote, urlsplit

from django.http.request import split_domain_port

RP_NAME = 'RouterFleet Improved'
TOTP_DIGITS = 6
TOTP_PERIOD = 30
TOTP_WINDOW = 1          # steps accepted either side of the current one
PENDING_SECONDS = 300    # how long a password stays good for its second step


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------

def new_totp_secret():
    """160 random bits, base32 without padding - what authenticator apps expect."""
    return base64.b32encode(os.urandom(20)).decode('ascii').rstrip('=')


def _key(secret):
    secret = secret.strip().replace(' ', '').upper()
    return base64.b32decode(secret + '=' * (-len(secret) % 8))


def totp_at(secret, step, digits=TOTP_DIGITS, digest=hashlib.sha1):
    """The code for one 30-second step (RFC 4226 truncation)."""
    mac = hmac.new(_key(secret), struct.pack('>Q', step), digest).digest()
    offset = mac[-1] & 0x0F
    number = struct.unpack('>I', mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** digits)).zfill(digits)


def current_step(now=None):
    return int((time.time() if now is None else now) // TOTP_PERIOD)


def matching_step(secret, code, last_used_step=None, now=None):
    """The step a code belongs to, or None when it is wrong, outside the window,
    or from a step already used."""
    code = (code or '').strip().replace(' ', '')
    if not (code.isdigit() and len(code) == TOTP_DIGITS):
        return None
    now_step = current_step(now)
    found = None
    for step in range(now_step - TOTP_WINDOW, now_step + TOTP_WINDOW + 1):
        # Compare every candidate, found or not, so the time taken says nothing
        if hmac.compare_digest(totp_at(secret, step), code):
            found = step
    if found is None or (last_used_step is not None and found <= last_used_step):
        return None
    return found


def provisioning_uri(secret, username):
    label = quote(f'{RP_NAME}:{username}')
    return (f'otpauth://totp/{label}?secret={secret}&issuer={quote(RP_NAME)}'
            f'&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD}')


def qr_svg(uri):
    """The provisioning URI as an inline SVG QR code, drawn on the server: no
    script, no image from another host."""
    import segno
    return segno.make(uri, error='m').svg_inline(scale=5, border=2, dark='#212529', light='#ffffff')


# ---------------------------------------------------------------------------
# Passkeys
# ---------------------------------------------------------------------------

class PasskeyUnavailable(Exception):
    """The installation is reached in a way browsers do not allow passkeys for."""


def relying_party(request):
    """(rp_id, origin) for this request.

    The relying party is the host name the browser used. The origin is taken
    from the browser's Origin header when it is https and names that same host
    - nginx passes the host on without its port, and an installation published
    on a port other than 443 is still the same relying party."""
    host, _ = split_domain_port(request.get_host())
    try:
        ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        pass
    else:
        raise PasskeyUnavailable('Passkeys need the installation to be reached by a name, not by an IP address.')
    origin = request.headers.get('Origin', '')
    parts = urlsplit(origin)
    if parts.scheme == 'https' and parts.hostname == host:
        return host, origin
    return host, f'https://{host}'


def b64url(data):
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def from_b64url(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
