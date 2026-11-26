import re

from django import template
from django.core.exceptions import ObjectDoesNotExist

from router_manager.models import Router, RouterInformation

register = template.Library()

# The version itself: the first word that starts with a number (or a V and a
# number). '7.16.2 (stable)' of RouterOS, the kernel '4.14.151' of an airOS
# device, 'OpenWrt 24.10.3 r28714-00d1fe2b72' (24.10.3: the build after the
# space is not comparable), Cisco IOS '15.5(2)T1', Huawei 'V200R010C00SPC600'
VERSION_PATTERN = re.compile(r'(?<![\w.])[Vv]?\d[\w.()\-]*')
# Its numbers in order; letters separate them. A pre-release word ranks below
# the release it leads to: 7.21beta3 < 7.21rc1 < 7.21
VERSION_PART = re.compile(r'(\d+)|(alpha|beta|pre|rc)', re.IGNORECASE)
PRE_RELEASE = {'alpha': -4, 'beta': -3, 'pre': -2, 'rc': -1}

# Version strings that are only known after the router information was read
NEUTRAL_STATE = {'css_class': '', 'title': ''}



def version_key(value):
    """The comparable numbers of a version string, None without any."""
    match = VERSION_PATTERN.search(value or '')
    if not match:
        return None
    parts = tuple(int(number) if number else PRE_RELEASE[word.lower()]
                  for number, word in VERSION_PART.findall(match.group(0)))
    return parts or None


def compare_versions(left, right):
    """-1 when left is the older one, 0 when both are the same, 1 when newer.

    None when one of them carries no version at all. A version without the
    patch level counts as that patch level: 7.16 and 7.16.0 are the same.
    """
    left_key = version_key(left)
    right_key = version_key(right)
    if left_key is None or right_key is None:
        return None

    length = max(len(left_key), len(right_key))
    left_key += (0,) * (length - len(left_key))
    right_key += (0,) * (length - len(right_key))
    return (left_key > right_key) - (left_key < right_key)


@register.filter
def firmware_state(router_information):
    """Colour and explanation for the firmware of a router.

    The RouterBOOT firmware has to be on the version of the OS, a firmware that
    stayed behind is what keeps a device from booting into a new OS.
    """
    firmware = getattr(router_information, 'firmware_version', '') or ''
    os_version = getattr(router_information, 'os_version', '') or ''

    comparison = compare_versions(firmware, os_version)
    if comparison is None:
        # Nothing to compare, one of the two was never read out
        return NEUTRAL_STATE
    if comparison == 0:
        return {'css_class': 'text-success',
                'title': f'Firmware matches the OS version {os_version}'}
    if comparison < 0:
        return {'css_class': 'text-danger',
                'title': f'Firmware {firmware} is behind the OS version {os_version}'}
    # Newer than the OS, there is nothing to update
    return NEUTRAL_STATE


@register.filter
def os_version_state(router_information):
    """Colour and explanation for the OS version of a router.

    Green while the version the router offers is the one it runs, red while an
    update is waiting, yellow when RouterFleet has no version to compare with -
    that is only known after the information of the device was read out.
    """
    os_version = getattr(router_information, 'os_version', '') or ''
    available_version = getattr(router_information, 'available_version', '') or ''

    if not os_version:
        return NEUTRAL_STATE
    device_type = getattr(getattr(router_information, 'router', None), 'device_type', None)
    if device_type is not None and not device_type.checks:
        # Not missing information: the device type has no way to tell
        return {'css_class': '', 'title': f'{device_type.name} has no update check configured'}
    if not available_version:
        status = (getattr(router_information, 'update_status', '') or '').strip()
        said = f' The device said: "{status}".' if status else ''
        return {'css_class': 'text-warning',
                'title': f'No version known the device could update to.{said} Checked again within the hour, '
                         f'or now with Update device information.'}

    comparison = compare_versions(os_version, available_version)
    if comparison is None:
        # An offered version without a number in it can not be compared
        return NEUTRAL_STATE
    if comparison == 0:
        return {'css_class': 'text-success', 'title': f'Up to date with {available_version}'}
    if comparison < 0:
        return {'css_class': 'text-danger',
                'title': f'{available_version} is available, the device runs {os_version}'}
    # Ahead of what the channel offers, there is nothing to update
    return NEUTRAL_STATE


def _untracked_types():
    """Slugs of the types with no update check, and of unmanaged ones: their
    devices are never counted as missing version information."""
    from router_manager.models import DeviceType
    return [d.slug for d in DeviceType.objects.all() if not d.managed or not d.checks]


def update_state_counts():
    """How many devices are up to date, are missing an update and have no
    information at all.

    The very rule os_version_state colours the router list by, so the overview
    and the list can not tell two different stories. Devices that are only
    monitored stay out: they carry no version at all and the list shows none
    for them either.
    """
    information_of_router = {
        information.router_id: information
        for information in RouterInformation.objects.select_related('router').exclude(router__router_type__in=_untracked_types())
    }
    counts = {'up_to_date': 0, 'update_waiting': 0, 'no_information': 0}
    for state in update_state_of_routers(information_of_router).values():
        counts[state] += 1
    return counts


def update_state_of_routers(information_of_router=None):
    """{router id: 'up_to_date' | 'update_waiting' | 'no_information'}, by the
    rule the router list colours versions with - which the status page counts,
    and the list filters, with."""
    if information_of_router is None:
        information_of_router = {
            information.router_id: information
            for information in RouterInformation.objects.select_related('router').exclude(router__router_type__in=_untracked_types())
        }
    states = {}
    for router_id in Router.objects.exclude(router_type__in=_untracked_types()).values_list('id', flat=True):
        css_class = os_version_state(information_of_router.get(router_id))['css_class']
        if css_class == 'text-success':
            states[router_id] = 'up_to_date'
        elif css_class == 'text-danger':
            states[router_id] = 'update_waiting'
        else:
            # Nothing was read out of the device, or there is no version to
            # compare the one it runs with
            states[router_id] = 'no_information'
    return states


@register.simple_tag
def device_links(router):
    """The management links of a device's type: [(label, url)]."""
    from router_manager.device_types import links_for
    return links_for(router)


@register.filter
def source_sections(json_data):
    """The raw output the inventory read, as [(command, output)] with real
    line breaks: stored as JSON {command: output}, which shows \\r\\n literally.
    Text that is not that JSON comes back as one section without a command."""
    import json
    if not json_data:
        return []
    try:
        data = json.loads(json_data)
    except (TypeError, ValueError):
        return [('', str(json_data))]
    if not isinstance(data, dict):
        return [('', json.dumps(data, indent=2))]
    sections = []
    for command, output in data.items():
        text = output if isinstance(output, str) else json.dumps(output, indent=2)
        text = text.replace('\r\n', '\n').replace('\r', '\n').strip('\n')
        sections.append((str(command), text))
    return sections


@register.filter
def bootloader_behind(router_information):
    """Whether the device could install a newer bootloader firmware than it
    runs (RouterOS: upgrade-firmware newer than current-firmware)."""
    running = (getattr(router_information, 'firmware_version', '') or '').strip()
    available = (getattr(router_information, 'firmware_available', '') or '').strip()
    return bool(running and available) and compare_versions(running, available) == -1



@register.filter
def url_host(address):
    """An address as a URL needs it: an IPv6 address in brackets."""
    address = str(address or '')
    return f'[{address}]' if ':' in address and not address.startswith('[') else address


@register.filter
def device_state(router):
    """A device's state, worded and coloured the same on every page:
    {'key': online|offline|disabled|unmonitored, 'css': ok|bad|off, 'label'}."""
    if not router.enabled:
        return {'key': 'disabled', 'css': 'off', 'label': 'Disabled'}
    if not router.monitoring:
        return {'key': 'unmonitored', 'css': 'off', 'label': 'Not monitored'}
    try:
        online = router.routerstatus.status_online
    except ObjectDoesNotExist:
        online = False
    return {'key': 'online', 'css': 'ok', 'label': 'Online'} if online else {'key': 'offline', 'css': 'bad', 'label': 'Offline'}


@register.filter
def update_blocked(router):
    """Why this device's account may not install an update - '' when it may
    (router_manager/rights.py)."""
    from router_manager.rights import blocked_reason
    return blocked_reason(router)
