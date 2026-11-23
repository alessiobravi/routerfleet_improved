"""Device types in System settings: the list, the editor, the wizard for a
new type, and "try on a device" (Administrator only - a device type decides
what RouterFleet sends to devices)."""
import json
import logging
import re
from urllib.parse import urlsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from routerlib.actions import posted_confirmation
from user_manager.models import UserAcl

from . import device_types
from .models import DeviceType, Router

logger = logging.getLogger(__name__)
LEVEL = 50
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{1,48}$')
EXTENSION = re.compile(r'^[a-z0-9][a-z0-9.]{0,9}$')
UPLOAD_PATH = re.compile(r'^[A-Za-z0-9_./:{}\- ]{1,200}$')
# Export words go into a command sent to the device: words, = and dashes only
EXPORT_WORDS = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_=.\- ]{0,99}$')
VERSION = re.compile(r'^\d{1,4}(\.\d{1,4}){0,3}$')
TEXT_FIELDS = ('name', 'vendor', 'description', 'prompt_pattern', 'session_setup', 'enable_command', 'management_links',
               'test_pattern', 'inventory_repeat', 'text_extension', 'binary_extension', 'text_ignore_pattern',
               'option_sensitive', 'option_terse', 'option_hide_sensitive', 'legacy_before_version',
               'legacy_option_sensitive', 'legacy_option_hide_sensitive', 'vendor_url', 'vendor_keys', 'vendor_confirm_url', 'vendor_target',
               'vendor_version_field', 'firmware_upload_path')
BOOL_FIELDS = ('enabled', 'check_device', 'check_vendor', 'check_repository')
PATTERN_FIELDS = (('prompt_pattern', 'Prompt'), ('test_pattern', 'Recognition pattern'),
                  ('inventory_repeat', 'Line to repeat'), ('text_ignore_pattern', 'Lines to ignore'))


def describe_failure(error):
    """What a failed try says: a fixed sentence per kind of failure, never the
    exception text itself."""
    import socket
    import paramiko
    from routerlib import device_session, host_keys
    if isinstance(error, host_keys.HostKeyChanged):
        return ('The device presented a different host key than the one stored for this address: nothing was sent. '
                'Use Forget host key on the device if it was replaced.')
    if isinstance(error, paramiko.AuthenticationException):
        return 'The device refused the username or password.'
    if isinstance(error, device_session.SessionError):
        return ('The shell did not behave as the device type says: no prompt matching the pattern, or the enable '
                'password was refused.')
    if isinstance(error, (socket.timeout, TimeoutError)):
        return 'The device did not answer in time.'
    if isinstance(error, (ConnectionError, OSError, paramiko.SSHException)):
        return 'Could not connect over SSH: the address, the port or the network.'
    return 'The try failed. The logs of the routerfleet container have the details.'


def allowed(request):
    return UserAcl.objects.filter(user=request.user, user_level__gte=LEVEL).exists()


def system_keys():
    from fleet_commander.system_commands import DEFAULTS
    return list(DEFAULTS)


def command_definitions():
    from fleet_commander.system_commands import DEFAULTS
    return [(key, DEFAULTS[key]) for key in DEFAULTS]


def stored_commands(device_type):
    from fleet_commander.models import CommandVariant
    rows = {v.command.system_key: v for v in CommandVariant.objects.filter(
        router_type=device_type.slug, command__system_key__isnull=False).select_related('command')}
    return [(key, definition, rows.get(key)) for key, definition in command_definitions()]


def _url_problem(label, url, placeholders=()):
    if not url:
        return ''
    parts = urlsplit(url.replace('{version}', '1').replace('{target}', 't'))
    if parts.scheme != 'https' or not parts.hostname:
        return f'{label}: only https:// addresses.'
    for name in re.findall(r'\{([a-z_]+)\}', url):
        if name not in placeholders:
            return f'{label}: unknown placeholder {{{name}}}.'
    return ''


def read_form(post, device_type, new=False):
    """Fills device_type from the posted form; returns (problems, commands).
    commands is {key: payload} for a new type (the wizard), else None."""
    problems = []
    for field in TEXT_FIELDS:
        value = (post.get(field) or '').strip() if field not in ('session_setup', 'management_links', 'description') \
            else (post.get(field) or '').strip('\n')
        setattr(device_type, field, value)
    for field in BOOL_FIELDS:
        setattr(device_type, field, post.get(field) == 'on')
    device_type.session_mode = post.get('session_mode') if post.get('session_mode') in ('exec', 'shell') else 'exec'
    device_type.firmware_verify = post.get('firmware_verify') if post.get('firmware_verify') in ('none', 'size', 'sha256') \
        else 'none'
    if new:
        device_type.slug = (post.get('slug') or '').strip().lower()
        if not SLUG.match(device_type.slug):
            problems.append('Short name: 2 to 49 lowercase letters, digits and dashes, e.g. "fortinet-fortios".')
        elif DeviceType.objects.filter(slug=device_type.slug).exists():
            problems.append('A device type with this short name exists.')
        device_type.managed = True
    if not device_type.name:
        problems.append('Enter a name.')
    elif DeviceType.objects.filter(name=device_type.name).exclude(pk=device_type.pk).exists():
        problems.append('A device type with this name exists.')
    for field, label in PATTERN_FIELDS:
        try:
            device_types.compile_pattern(getattr(device_type, field))
        except ValueError as error:
            problems.append(f'{label}: {error}')
    if device_type.session_mode == 'shell' and not device_type.prompt_pattern:
        problems.append('An interactive shell needs the prompt pattern, to know when a command has finished.')
    for field, label in (('text_extension', 'Text extension'), ('binary_extension', 'Binary extension')):
        value = getattr(device_type, field)
        if value and not EXTENSION.match(value):
            problems.append(f'{label}: letters, digits and dots, e.g. "rsc" or "tar.gz".')
    if not device_type.text_extension:
        device_type.text_extension = 'txt'
    for field in ('option_sensitive', 'option_terse', 'option_hide_sensitive', 'legacy_option_sensitive',
                  'legacy_option_hide_sensitive'):
        value = getattr(device_type, field)
        if value and not EXPORT_WORDS.match(value):
            problems.append('Export options: words, "=" and dashes only, e.g. "show-sensitive".')
            break
    if device_type.legacy_before_version and not VERSION.match(device_type.legacy_before_version):
        problems.append('Older versions: a version number such as 7 or 7.1.')
    inventory = {}
    for field, _label in device_types.INVENTORY_FIELDS:
        sources = [line.strip() for line in (post.get(f'inventory_{field}') or '').splitlines() if line.strip()]
        if sources:
            inventory[field] = sources
    problems += device_types.validate_inventory_fields(inventory)
    device_type.inventory_fields = inventory
    problems += device_types.validate_links(device_type.management_links)
    problems += [p for p in (_url_problem('Release list', device_type.vendor_url),
                             _url_problem('Confirmation address', device_type.vendor_confirm_url,
                                          ('version', 'target'))) if p]
    if device_type.check_vendor and not (device_type.vendor_url and device_type.vendor_keys):
        problems.append('The vendor check needs the release list address and the keys holding the versions.')
    if device_type.vendor_target:
        kind = device_type.vendor_target.split(':', 1)[0]
        if kind not in ('key', 're'):
            problems.append('The target is a source like the inventory: key:NAME or re:PATTERN.')
    if device_type.vendor_version_field not in dict(device_types.INVENTORY_FIELDS):
        device_type.vendor_version_field = 'os_version'
    if device_type.firmware_upload_path and not UPLOAD_PATH.match(device_type.firmware_upload_path):
        problems.append('Upload path: a path such as /{{ firmware_name }} or /tmp/{{ firmware_name }} - no quotes '
                        'or shell characters.')
    for name in re.findall(r'\{\{\s*([a-z_]+)\s*\}\}', device_type.firmware_upload_path or ''):
        if name != 'firmware_name':
            problems.append('Upload path: only {{ firmware_name }} can be used.')
    commands = None
    if new:
        from fleet_commander import system_commands
        commands = {}
        for key in system_keys():
            payload = (post.get(f'command_{key}') or '').replace('\r\n', '\n').strip('\n')
            commands[key] = payload
            for problem in system_commands.validate_payload(key, device_type.slug or 'new', payload):
                problems.append(f'{system_commands.DEFAULTS[key]["name"]}: {problem}')
    return problems, commands


def form_context(device_type, commands=None, new=False):
    inventory = device_type.inventory_fields or {}
    presets = {slug: {**device_types.preset_values(slug),
                      'commands': {key: device_types.PRESET_COMMANDS.get(slug, {}).get(key, '') for key in system_keys()}}
               for slug, preset in device_types.PRESETS.items() if preset.get('managed', True)}
    return {
        'device_type': device_type, 'new': new,
        'inventory_rows': [(field, label, '\n'.join(inventory.get(field, []))) for field, label in device_types.INVENTORY_FIELDS],
        'commands': [(key, definition, (commands or {}).get(key, '')) for key, definition in command_definitions()] if new else None,
        'stored_commands': None if new else stored_commands(device_type),
        'presets_json': json.dumps(presets),
        'devices': Router.objects.filter(router_type=device_type.slug).count() if device_type.pk else 0,
    }


@login_required
def view_device_types(request):
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    rows = []
    for device_type in DeviceType.objects.all():
        rows.append({'type': device_type, 'devices': Router.objects.filter(router_type=device_type.slug).count()})
    return render(request, 'router_manager/device_types.html', {'page_title': 'Device types', 'rows': rows})


@login_required
def view_device_type(request):
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    device_type = get_object_or_404(DeviceType, uuid=request.GET.get('uuid'))
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            if posted_confirmation(request) != 'delete':
                return redirect(f'/system/device-types/edit/?uuid={device_type.uuid}')
            if Router.objects.filter(router_type=device_type.slug).exists() or device_type.slug == 'monitoring':
                messages.warning(request, 'Not deleted|Devices use this type: give them another type first.')
            elif device_type.firmware_images.exists():
                messages.warning(request, 'Not deleted|The firmware repository holds images for this type.')
            else:
                from fleet_commander.models import CommandVariant
                CommandVariant.objects.filter(router_type=device_type.slug).delete()
                device_type.delete()
                messages.success(request, f'Device type deleted|{device_type.name}')
                return redirect('/system/device-types/')
            return redirect(f'/system/device-types/edit/?uuid={device_type.uuid}')
        if action == 'restore' and device_type.preset in device_types.PRESETS:
            for field, value in device_types.preset_values(device_type.preset).items():
                if field not in ('name',):
                    setattr(device_type, field, value)
            device_type.save()
            messages.success(request, 'Preset restored|The commands are restored one by one in Fleet Commander.')
            return redirect(f'/system/device-types/edit/?uuid={device_type.uuid}')
        problems, _ = read_form(request.POST, device_type)
        if device_type.managed is False:
            device_type.enabled = True if device_type.slug == 'monitoring' else device_type.enabled
        if problems:
            for problem in problems:
                messages.warning(request, 'Not saved|' + problem.replace('|', '/'))
            return render(request, 'router_manager/device_type_form.html',
                          {'page_title': device_type.name, **form_context(device_type)})
        device_type.save()
        messages.success(request, f'Device type saved|{device_type.name}')
        return redirect(f'/system/device-types/edit/?uuid={device_type.uuid}')
    return render(request, 'router_manager/device_type_form.html',
                  {'page_title': device_type.name, **form_context(device_type)})


@login_required
def view_device_type_new(request):
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    device_type = DeviceType(position=200)
    commands = {}
    if request.method == 'POST':
        problems, commands = read_form(request.POST, device_type, new=True)
        if not problems:
            from fleet_commander.models import Command, CommandVariant
            device_type.preset = ''
            device_type.save()
            for key, payload in commands.items():
                command = Command.objects.filter(system_key=key).first()
                if command is not None:
                    CommandVariant.objects.create(command=command, router_type=device_type.slug, payload=payload,
                                                  enabled=True)
            messages.success(request, f'Device type created|{device_type.name} can be chosen for devices now.')
            return redirect(f'/system/device-types/edit/?uuid={device_type.uuid}')
        for problem in problems:
            messages.warning(request, 'Not created|' + problem.replace('|', '/'))
    return render(request, 'router_manager/device_type_form.html',
                  {'page_title': 'New device type', **form_context(device_type, commands, new=True)})


@login_required
def view_device_type_try(request):
    """Runs the connection test, the inventory and the text backups of the
    posted definition against a device, and shows what each found. Nothing is
    stored except the device's host key, as for every connection."""
    if not allowed(request):
        return JsonResponse({'ok': False, 'message': 'Permission denied.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'message': 'Invalid request method.'}, status=405)
    from console import network
    from fleet_commander import system_commands
    from routerlib import device_session
    stored = DeviceType.objects.filter(uuid=request.POST.get('uuid')).first() \
        if re.match(r'^[0-9a-f-]{36}$', request.POST.get('uuid', '')) else None
    device_type = DeviceType(slug=stored.slug, pk=stored.pk) if stored else DeviceType(slug='try')
    problems, commands = read_form(request.POST, device_type, new=stored is None)
    problems = [p for p in problems if 'short name' not in p.lower() and 'name exists' not in p and 'Enter a name' not in p]
    if problems:
        return JsonResponse({'ok': False, 'message': problems[0]})
    address = (request.POST.get('try_address') or '').strip()
    try:
        port = int(request.POST.get('try_port') or 22)
    except ValueError:
        port = 0
    if not address or not 1 <= port <= 65535:
        return JsonResponse({'ok': False, 'message': 'Enter the address and port of a device to try on.'})
    try:
        network.check(address, port)
    except network.Refused:
        return JsonResponse({'ok': False, 'message': 'Not connected: that address is RouterFleet itself, its internal '
                                                     'network, or does not resolve.'})

    def lines_of(key, **values):
        if commands is not None:
            payload = commands.get(key, '')
            return [re.sub(r'\{\{\s*([a-z_]+)\s*\}\}', lambda m: str(values.get(m.group(1), '')), line)
                    for line in payload.splitlines() if line.strip()]
        return system_commands.lines(key, stored.slug, **values)

    result = {'ok': False, 'steps': []}
    session = None
    try:
        session = device_session.connect(device_type, address, port, request.POST.get('try_username', ''),
                                         request.POST.get('try_password', ''), None,
                                         request.POST.get('try_enable_password') or None)

        def run(command):
            stdin, stdout, stderr = session.exec_command(command)  # nosec B601 - an administrator's own definition
            return stdout.read().decode('utf-8', errors='replace')

        output = ''.join(run(line) for line in lines_of('connection_test'))
        ok, message = device_types.test_result(device_type, output)
        result['steps'].append({'title': 'Connection test', 'ok': ok, 'message': message, 'output': output[:4000]})
        outputs = '\n'.join(run(line) for line in lines_of('inventory'))
        found = device_types.read_inventory(device_type, system_commands.parse_values(outputs), outputs)
        result['steps'].append({'title': 'Inventory', 'ok': any(found.get(f) for f in device_types.INVENTORY_IDENTITY),
                                'fields': [(label, found.get(field, '')) for field, label in device_types.INVENTORY_FIELDS],
                                'output': outputs[:6000]})
        for name, command in device_types.parse_named_lines('\n'.join(lines_of('backup_text', export_parameters=''))):
            text = device_types.clean_text(device_type, run(command).replace('\r\n', '\n'))
            result['steps'].append({'title': f'Text backup "{name}"', 'ok': bool(text.strip()),
                                    'message': f'{len(text.splitlines())} lines', 'output': text[:3000]})
        result['ok'] = all(step['ok'] for step in result['steps'])
    except Exception as error:
        logger.warning('Device type try on a device failed: %s', error.__class__.__name__, exc_info=True)
        result['message'] = describe_failure(error)
    finally:
        if session is not None:
            session.close()
    return JsonResponse(result)
