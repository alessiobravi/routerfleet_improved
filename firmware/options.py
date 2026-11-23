"""The update options of a device, in the order they are offered:

1. a file of the firmware repository that is for the device - its device
   type, model and architecture - newest first;
2. the device's own online update, one option per release channel its device
   type lists for the device's major version, each with the newest version the
   vendor publishes on that channel.

Only versions newer than the one the device runs are offered, and never one of
another major version: such a jump (RouterOS 6 to 7) takes a procedure of its
own. Nothing here knows a vendor: the channels, the addresses that publish
their newest version and the procedures are configuration of the device type
and its system commands.
"""
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

CHANNEL_LINE = re.compile(r'^\s*(\d{1,3})\s+([A-Za-z0-9][\w.\-]{0,39})\s*:\s*(https://\S{1,300})\s*$')
DEFAULT_PATTERN = r'^\s*([0-9][0-9A-Za-z.\-]*)'
CACHE_SECONDS = 3600
_cache = {}   # url -> (time, version)


def parse_channels(text):
    """[(major, channel, url)] of the update_channels lines; others ignored."""
    channels = []
    for line in (text or '').splitlines():
        match = CHANNEL_LINE.match(line)
        if match:
            channels.append((int(match.group(1)), match.group(2), match.group(3)))
    return channels


def channel_problems(text):
    """What is wrong with update_channels, for the device type form."""
    problems = []
    for number, line in enumerate((text or '').splitlines(), 1):
        if line.strip() and not CHANNEL_LINE.match(line):
            problems.append(f'Update channels, line {number}: "<major version> <channel>: https://..." '
                            f'(for example "7 stable: https://...").')
    return problems


def major_of(version):
    match = re.match(r'\s*(\d+)', version or '')
    return int(match.group(1)) if match else None


def plain(version):
    """The version without what follows it in brackets: '7.24.5 (stable)' -> '7.24.5'."""
    return (version or '').split('(')[0].strip()


def same_version(running, expected):
    """Whether the device runs exactly that version - a beta is not its final
    release, so the text decides, not only the numbers."""
    return plain(running).lower() == plain(expected).lower() and bool(plain(expected))


def fetch_channel_version(device_type, url):
    """The newest version the vendor publishes at url, '' when it can not be
    read. Fetched by the server, https only, never on RouterFleet's own
    networks, kept for an hour."""
    from routerlib.router_functions import _safe_url
    cached = _cache.get(url)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        response = requests.get(_safe_url(url), timeout=10, allow_redirects=False,
                                headers={'User-Agent': 'RouterFleet-Improved'})
        response.raise_for_status()
        text = response.text[:2000]
    except Exception as error:
        logger.info(f'Update channel {url} could not be read: {type(error).__name__}')
        return ''   # not cached: asked again next time
    try:
        match = re.search(device_type.update_channel_pattern or DEFAULT_PATTERN, text, re.MULTILINE)
    except re.error:
        match = None
    version = match.group(1).strip()[:64] if match and match.groups() else ''
    _cache[url] = (time.time(), version)
    return version


def current_channel(router):
    """The channel the device reported it is on ("channel: stable"), or ''."""
    import json
    info = getattr(router, 'routerinformation', None)
    try:
        outputs = json.loads(getattr(info, 'json_data', '') or '{}')
    except ValueError:
        return ''
    if not isinstance(outputs, dict):
        return ''
    for output in outputs.values():
        match = re.search(r'^\s*channel\s*[:=]\s*([\w.\-]+)', str(output), re.MULTILINE | re.IGNORECASE)
        if match:
            return match.group(1)
    return ''


def options_for(router, fetch=fetch_channel_version):
    """The update options of one device, in order (see the module)."""
    from fleet_commander import system_commands
    from router_manager.templatetags.router_tags import compare_versions
    from .models import FirmwareImage
    device_type = router.device_type
    info = getattr(router, 'routerinformation', None)
    running = plain(getattr(info, 'os_version', '') or '')
    if device_type is None or not device_type.managed or not running:
        return []
    major = major_of(running)

    def newer(version):
        return compare_versions(running, version) == -1 and major_of(version) == major

    options = []
    if system_commands.payload_of('firmware_install', router.router_type).strip():
        images = [image for image in FirmwareImage.objects.filter(device_type=device_type, enabled=True)
                  if image.matches_router(router) and newer(image.version)]
        images.sort(key=lambda image: [int(p) if p.isdigit() else 0 for p in re.split(r'[.\-]', plain(image.version))],
                    reverse=True)
        for image in images:
            options.append({'key': f'image:{image.uuid}', 'kind': 'image', 'version': image.version,
                            'image': image, 'channel': '',
                            'label': f'{image.version} from the firmware repository ({image.filename})'})
    if system_commands.payload_of('firmware_online_update', router.router_type).strip():
        on = current_channel(router)
        for line_major, channel, url in parse_channels(device_type.update_channels):
            if line_major != major:
                continue
            version = fetch(device_type, url)
            if version and newer(version):
                options.append({'key': f'online:{channel}', 'kind': 'online', 'version': version, 'image': None,
                                'channel': channel, 'current': channel == on,
                                'label': f'{version} - online update, {channel} channel'
                                         + (' (the channel it is on)' if channel == on else '')})
    if options:
        # The first file of the repository; without one, the channel the device is on
        recommended = next((o for o in options if o['kind'] == 'image'), None) \
            or next((o for o in options if o.get('current')), None) or options[0]
        recommended['recommended'] = True
    return options
