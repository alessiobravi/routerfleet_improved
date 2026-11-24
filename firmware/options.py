"""The update options of a device, in the order they are offered:

1. a file of the firmware repository that is for the device - its device
   type, model and architecture - newest first;
2. the device's own online update, one option per release channel its device
   type lists for the device's major version, each with the newest version the
   vendor publishes on that channel;
3. the bootloader firmware (RouterOS: RouterBOOT), when the device can install
   a newer one than it runs. After an OS update it can follow as one more step.

Only versions newer than the one the device runs are offered - and only of its
own major version, unless the device type allows another (OpenWrt: 24.10 to
25.12 is the usual upgrade; RouterOS 6 to 7 takes a procedure of its own).
Nothing here knows a vendor: the channels, the addresses that publish
their newest version and the procedures are configuration of the device type
and its system commands.
"""
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

# "<major or *> <channel>: https://... [| pattern]" - the pattern reads this line's version
CHANNEL_LINE = re.compile(r'^\s*(\d{1,3}|\*)\s+([A-Za-z0-9][\w.\-]{0,39})\s*:\s*(https://[^\s|]{1,300})\s*(?:\|\s*(.{1,200}?))?\s*$')
DEFAULT_PATTERN = r'^\s*([0-9][0-9A-Za-z.\-]*)'
CACHE_SECONDS = 3600
_cache = {}   # url -> (time, version)


def parse_channels(text):
    """[(major or None for every major, channel, url, pattern)] of the
    update_channels lines; others ignored."""
    channels = []
    for line in (text or '').splitlines():
        match = CHANNEL_LINE.match(line)
        if match:
            major = None if match.group(1) == '*' else int(match.group(1))
            channels.append((major, match.group(2), match.group(3), (match.group(4) or '').strip()))
    return channels


def channel_problems(text):
    """What is wrong with update_channels, for the device type form."""
    problems = []
    for number, line in enumerate((text or '').splitlines(), 1):
        match = CHANNEL_LINE.match(line)
        if line.strip() and not match:
            problems.append(f'Update channels, line {number}: "<major version or *> <channel>: https://... '
                            f'[| pattern]" (for example "7 stable: https://...").')
        elif match and match.group(4):
            try:
                if re.compile(match.group(4).strip()).groups < 1:
                    problems.append(f'Update channels, line {number}: the pattern needs a group around the version.')
            except re.error as error:
                problems.append(f'Update channels, line {number}: the pattern is not valid ({error}).')
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


def fetch_channel_version(device_type, url, pattern=''):
    """The newest version the vendor publishes at url, '' when it can not be
    read - read with the line's pattern, else the device type's, else the
    first word. Fetched by the server, https only, never on RouterFleet's own
    networks, kept for an hour (one fetch for lines sharing an address)."""
    from routerlib.router_functions import _safe_url
    cached = _cache.get(url)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        text = cached[1]
    else:
        try:
            response = requests.get(_safe_url(url), timeout=10, allow_redirects=False,
                                    headers={'User-Agent': 'RouterFleet-Improved'})
            response.raise_for_status()
            text = response.text[:20000]
        except Exception as error:
            logger.info(f'Update channel {url} could not be read: {type(error).__name__}')
            return ''   # not cached: asked again next time
        _cache[url] = (time.time(), text)
    try:
        match = re.search(pattern or device_type.update_channel_pattern or DEFAULT_PATTERN, text, re.MULTILINE)
    except re.error:
        match = None
    return match.group(1).strip()[:64] if match and match.groups() else ''


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
        return compare_versions(running, version) == -1 and \
            (device_type.update_any_major or major_of(version) == major)

    options = []
    if system_commands.payload_of('firmware_install', router.router_type).strip():
        images = [image for image in FirmwareImage.objects.filter(device_type=device_type, enabled=True)
                  if image.matches_router(router) and newer(image.version)]
        from router_manager.templatetags.router_tags import version_key
        images.sort(key=lambda image: version_key(plain(image.version)) or (), reverse=True)
        for image in images:
            options.append({'key': f'image:{image.uuid}', 'kind': 'image', 'version': image.version,
                            'image': image, 'channel': '',
                            'label': f'{image.version} from the firmware repository ({image.filename})'})
    if system_commands.payload_of('firmware_online_update', router.router_type).strip():
        on = current_channel(router)
        for line_major, channel, url, pattern in parse_channels(device_type.update_channels):
            if line_major is not None and line_major != major:
                continue
            version = fetch(device_type, url, pattern)
            if version and newer(version):
                options.append({'key': f'online:{channel}', 'kind': 'online', 'version': version, 'image': None,
                                'channel': channel, 'current': channel == on,
                                'label': f'{version} - online update, {channel} channel'
                                         + (' (the channel it is on)' if channel == on else '')})
    bootloader = bool(system_commands.payload_of('firmware_bootloader_upgrade', router.router_type).strip())
    for option in options:
        option['bootloader_after'] = bootloader
    firmware = (getattr(info, 'firmware_version', '') or '').strip()
    can_install = (getattr(info, 'firmware_available', '') or '').strip()
    if bootloader and firmware and can_install and compare_versions(firmware, can_install) == -1:
        options.append({'key': 'bootloader', 'kind': 'bootloader', 'version': can_install, 'image': None,
                        'channel': '', 'bootloader_after': False,
                        'label': f'Bootloader firmware {firmware} to {can_install}'})
    if options:
        # The first file of the repository; without one, the channel the device is on
        recommended = next((o for o in options if o['kind'] == 'image'), None) \
            or next((o for o in options if o.get('current')), None) or options[0]
        recommended['recommended'] = True
    return options
