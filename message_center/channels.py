"""Talking to the notification services, in one place.

The request to each service is built from the channel's token and destination
and from message text that contains device names - all of them typed by
someone. So none of it is pasted into a URL: query values go through
`params` (encoded by requests), path segments are quoted, and the token and
destination must look like what the service issues before a channel is saved.
A `&` in a device name can therefore no longer cut a message short or add a
parameter to the request.

Error text from a failed request names the URL, and the URL carries the
Telegram bot token or the CallMeBot key; `scrub` takes them out before the text
is stored or shown.
"""
import re
from urllib.parse import quote

import requests

TIMEOUT = 15

FORMATS = {
    'telegram': {
        'token': (re.compile(r'^\d+:[A-Za-z0-9_-]+$'), 'A Telegram bot token looks like 123456789:AAE…'),
        'destination': (re.compile(r'^(-?\d+|@[A-Za-z0-9_]{5,32})$'),
                        'A Telegram chat id is a number (negative for groups) or @channelname'),
    },
    'ntfy': {
        'destination': (re.compile(r'^[A-Za-z0-9_-]{1,64}$'),
                        'An ntfy topic is up to 64 letters, digits, - and _'),
        'token': (re.compile(r'^[A-Za-z0-9_.-]{1,128}$'), 'An ntfy access token looks like tk_...'),
    },
}


def format_errors(channel_type, token, destination):
    """{field: message} for each value that does not look like what the service issues."""
    errors = {}
    values = {'token': token, 'destination': destination}
    for field, (pattern, message) in FORMATS.get(channel_type, {}).items():
        value = values[field]
        if value and not pattern.match(value):
            errors[field] = message
    return errors


REMOVED = 'CallMeBot is no longer supported: recreate this channel as email, ntfy or Telegram.'
PUBLIC_NTFY = 'https://ntfy.sh'
NTFY_SERVER = re.compile(r'^(https?)://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?$')


def ntfy_server_errors(server_url, token):
    """Why an ntfy server can not be used, or ''. A token is only ever sent
    over HTTPS, and a server on RouterFleet's own networks is refused."""
    url = (server_url or PUBLIC_NTFY).strip().rstrip('/')
    match = NTFY_SERVER.match(url)
    if not match:
        return 'Enter the server as https://ntfy.example.com (or http:// on a trusted network).'
    if token and match.group(1) != 'https':
        return 'An access token is only sent over HTTPS: use an https:// server.'
    if url != PUBLIC_NTFY:
        from console.network import Refused, check
        try:
            check(match.group(2).strip('[]'), int((match.group(3) or ':0')[1:]) or (443 if match.group(1) == 'https' else 80))
        except Refused as reason:
            return f'That server can not be used: {reason}.'
    return ''


def send(channel_type, token, destination, text, subject=None, server_url=None, priority=3):
    """Send `text`; returns the response. Raises ValueError for an unknown type
    or for a token or destination that does not look like what the service
    issues, and whatever requests raises for a network failure.

    The formats are checked here as well as when a channel is saved: a channel
    saved by an earlier version was never checked, and the token and the topic
    end up in the request's path."""
    if channel_type == 'callmebot':
        raise ValueError(REMOVED)
    if channel_type in FORMATS and format_errors(channel_type, token, destination):
        raise ValueError('The channel token or destination is not valid; edit the channel.')
    if channel_type == 'telegram':
        return requests.get(f'https://api.telegram.org/bot{quote(token or "", safe=":")}/sendMessage',
                            params={'chat_id': destination, 'text': text}, timeout=TIMEOUT)
    if channel_type == 'email':
        from . import email
        return email.send(destination, subject or text.split('\n')[0], text)
    if channel_type == 'ntfy':
        problem = ntfy_server_errors(server_url, token)
        if problem:
            raise ValueError(problem)
        headers = {'Priority': str(max(1, min(int(priority or 3), 5)))}
        if subject:
            # A header is one line: a device name can not add another
            headers['Title'] = ' '.join(str(subject).split())[:200]
        if token:
            headers['Authorization'] = f'Bearer {token}'
        base = (server_url or PUBLIC_NTFY).strip().rstrip('/')
        return requests.post(f'{base}/{quote(destination or "", safe="")}', data=text.encode('utf-8'),
                             headers=headers, timeout=TIMEOUT, allow_redirects=False)
    raise ValueError('Invalid channel type')


def scrub(text, *secrets):
    """`text` without the secrets in it, in either their plain or their URL-encoded form."""
    text = str(text)
    for secret in secrets:
        if secret:
            for form in {secret, quote(secret, safe=''), quote(secret, safe=':')}:
                text = text.replace(form, '***')
    return text
