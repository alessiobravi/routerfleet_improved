"""Updates approved in System settings.

The application never touches Docker. It writes one request into the volume it
shares with the scheduler (settings.UPDATE_DIR):

    request.json   {"id", "version", "approved_by", "approved_at"} - written here

and reads what the scheduler's updater (containers/cron/rf-update) writes back:

    updater.json   every minute: that it is there, and whether it can work
    status.json    the progress of the update under way, or of the last one

The updater trusts nothing in the request beyond its format: it installs only
a release newer than the running one, and only when every image is signed by
this project's release workflow.
"""
import datetime
import json
import os
import re
import secrets

from django.conf import settings
from django.utils import timezone

from .https import _write_atomically

FRESH_SECONDS = 180
USERNAME = re.compile(r'[A-Za-z0-9_.@+-]{1,150}')
# The same form the updater accepts: ASCII digits only, no trailing newline
RELEASE = re.compile(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}')


def directory():
    return getattr(settings, 'UPDATE_DIR', '/update')


def shared():
    """Whether the compose file gives this container the update volume."""
    path = directory()
    return os.path.isdir(path) and os.access(path, os.W_OK)


def _read(name):
    try:
        path = os.path.join(directory(), name)
        if os.path.islink(path) or os.path.getsize(path) > 4096:
            return None
        with open(path) as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _when(value):
    try:
        moment = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return moment if moment.tzinfo else None
    except ValueError:
        return None


def updater():
    """{'shared', 'ready', 'reason', 'seen'}: ready only when the updater
    reported, recently, that it can work."""
    beat = _read('updater.json') or {}
    seen = _when(beat.get('seen'))
    fresh = bool(seen and timezone.now() - seen < datetime.timedelta(seconds=FRESH_SECONDS))
    return {'shared': shared(), 'seen': seen, 'fresh': fresh,
            'ready': bool(fresh and beat.get('ready') is True),
            'reason': str(beat.get('reason') or '')[:200]}


def status():
    value = _read('status.json')
    if not value:
        return None
    return {'id': str(value.get('id', ''))[:32], 'state': str(value.get('state', ''))[:16],
            'message': str(value.get('message', ''))[:500], 'at': _when(value.get('at'))}


def request(version, username):
    """Writes the request; returns its id. Raises ValueError on a value the
    updater would refuse anyway, OSError when the volume is not shared."""
    if not RELEASE.fullmatch(version or '') or not USERNAME.fullmatch(username or ''):
        raise ValueError('not a valid request')
    request_id = secrets.token_hex(16)
    body = {'id': request_id, 'version': version, 'approved_by': username,
            'approved_at': timezone.now().strftime('%Y-%m-%dT%H:%M:%SZ')}
    # Owner-only: both containers that touch it run as root
    _write_atomically(os.path.join(directory(), 'request.json'), json.dumps(body).encode())
    return request_id
