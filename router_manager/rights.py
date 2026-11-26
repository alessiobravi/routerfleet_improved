"""The device account's own rights: read with every inventory (the device
type's rights command), and compared with the rights an update needs - so an
update is shown as available but not installable when the account may not
install it, instead of failing on the device."""
import re

# The username goes into a command sent to the device: only these characters
SAFE_USERNAME = re.compile(r'^[A-Za-z0-9._@-]{1,64}$')
TOKEN = re.compile(r'[^\s,;]+')
# What a rights command prints: one line of comma-separated words (RouterOS:
# local,telnet,ssh,...,!romon). Anything else - an error message - is not a
# list of rights, and leaves them unknown
RIGHTS_LIST = re.compile(r'^[!\w-]+(,[!\w-]+)+$')


def command_for(router):
    """The rights command of this device, its account filled in - or '' when
    the type has none or the username could not be sent safely."""
    device_type = router.device_type
    command = (getattr(device_type, 'rights_command', '') or '').strip()
    if not command:
        return ''
    if '{{ username }}' in command:
        if not SAFE_USERNAME.match(router.username or ''):
            return ''
        command = command.replace('{{ username }}', router.username)
    return command


def granted(output):
    """The rights a rights command printed - a comma-separated list; one
    written !right is not granted (RouterOS lists both). [] when the output is
    not such a list (an error): the rights stay unknown, nothing is blocked."""
    line = (output or '').strip()
    if not RIGHTS_LIST.match(line):
        return []
    return [token for token in line.split(',') if not token.startswith('!')]


def missing_for_update(router, information=None):
    """The rights an update needs that the device account lacks - [] when
    nothing is missing, or when the rights are not known (not checked, not
    read yet): only a known lack blocks."""
    device_type = router.device_type
    needed = TOKEN.findall(getattr(device_type, 'update_rights', '') or '')
    if information is None:
        information = getattr(router, 'routerinformation', None)
    known = (getattr(information, 'account_rights', '') or '').split(',')
    if not needed or not known or known == ['']:
        return []
    return [right for right in needed if right not in known]


def blocked_reason(router, information=None):
    """Why an update can not be started for this device - '' when it can."""
    missing = missing_for_update(router, information)
    if not missing:
        return ''
    return (f'An update is available, but this device\'s account may not install it: it lacks '
            f'{", ".join(missing)} (its user rights on the device). Give the account these rights to update '
            f'from RouterFleet.')
