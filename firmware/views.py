"""The firmware repository and rollouts, in System settings (Configuration
Manager and above)."""
import hashlib
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import File
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from routerlib.actions import posted_action, posted_confirmation
from routerlib.decorators import cron_endpoint
from router_manager.models import DeviceType, Router
from user_manager.models import UserAcl

from . import rollout as engine
from .models import FirmwareImage, FirmwareRollout, RolloutTarget

LEVEL = 40
VERSION = re.compile(r'^[0-9A-Za-z][0-9A-Za-z._\-+() ]{0,63}$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')


def max_bytes():
    return getattr(settings, 'FIRMWARE_MAX_BYTES', 1024 * 1024 * 1024)


def allowed(request):
    return UserAcl.objects.filter(user=request.user, user_level__gte=LEVEL).exists()


def safe_filename(name):
    """A file name that is safe in a path on a device and in a shell command."""
    base = re.sub(r'[^A-Za-z0-9._-]', '_', (name or '').rsplit('/', 1)[-1].rsplit('\\', 1)[-1])
    base = base.lstrip('.')[:100]
    return base or 'firmware.bin'


@login_required
def view_repository(request):
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    types = []
    for device_type in DeviceType.objects.filter(managed=True):
        images = list(device_type.firmware_images.all())
        from fleet_commander import system_commands
        installable = bool(system_commands.payload_of('firmware_install', device_type.slug).strip())
        types.append({'type': device_type, 'images': images, 'installable': installable})
    return render(request, 'firmware/repository.html', {
        'page_title': 'Firmware repository', 'types': types,
        'rollouts': FirmwareRollout.objects.select_related('image__device_type')[:10],
        'max_mb': max_bytes() // (1024 * 1024)})


@login_required
def view_upload(request):
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    types = DeviceType.objects.filter(managed=True)
    if request.method == 'POST':
        errors = []
        device_type = types.filter(uuid=request.POST.get('device_type') or None).first() \
            if re.match(r'^[0-9a-f-]{36}$', request.POST.get('device_type', '')) else None
        version = (request.POST.get('version') or '').strip()
        pattern = (request.POST.get('models_pattern') or '').strip()
        architecture = (request.POST.get('architecture') or '').strip()[:50]
        expected = (request.POST.get('expected_sha256') or '').strip().lower()
        upload = request.FILES.get('file')
        if device_type is None:
            errors.append('Choose the device type.')
        if not VERSION.match(version):
            errors.append('Enter the version the firmware installs, e.g. 7.18.2.')
        if pattern:
            try:
                re.compile(pattern)
            except re.error as error:
                errors.append(f'The model pattern is not valid: {error}')
        if expected and not SHA256.match(expected):
            errors.append('The vendor checksum must be a SHA-256: 64 hexadecimal characters.')
        if upload is None:
            errors.append('Choose the firmware file.')
        elif upload.size > max_bytes():
            errors.append(f'The file is larger than {max_bytes() // (1024 * 1024)} MB.')
        elif upload.size == 0:
            errors.append('The file is empty.')
        if not errors:
            digest = hashlib.sha256()
            for chunk in upload.chunks():
                digest.update(chunk)
            sha256 = digest.hexdigest()
            if expected and expected != sha256:
                errors.append('The file does not match the vendor checksum: it is not the file the vendor '
                              'published, or it was damaged on the way. Nothing was stored.')
        if errors:
            for error in errors:
                messages.warning(request, 'Not uploaded|' + error)
            return redirect('/system/firmware/upload/')
        upload.seek(0)
        image = FirmwareImage(device_type=device_type, version=version, filename=safe_filename(upload.name),
                              size=upload.size, sha256=sha256, models_pattern=pattern, architecture=architecture,
                              notes=(request.POST.get('notes') or '')[:2000], uploaded_by=request.user,
                              uploaded_by_name=request.user.username)
        image.file.save(f'{device_type.slug}-{image.filename}', File(upload), save=False)
        image.save()
        messages.success(request, f'Firmware stored|{device_type.name} {version}, SHA-256 {sha256[:16]}...')
        return redirect(f'/system/firmware/image/?uuid={image.uuid}')
    return render(request, 'firmware/upload.html', {'page_title': 'Upload firmware', 'types': types,
                                                    'max_mb': max_bytes() // (1024 * 1024)})


@login_required
def view_image(request):
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    image = get_object_or_404(FirmwareImage, uuid=request.GET.get('uuid'))
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete' and posted_confirmation(request) == 'delete':
            if image.rollouts.exists():
                messages.warning(request, 'Not deleted|Rollouts refer to this firmware: switch it off instead.')
                return redirect(f'/system/firmware/image/?uuid={image.uuid}')
            engine.remove_file(image)
            image.delete()
            messages.success(request, 'Firmware deleted')
            return redirect('/system/firmware/')
        if action == 'toggle':
            image.enabled = not image.enabled
            image.save(update_fields=['enabled', 'updated'])
            return redirect(f'/system/firmware/image/?uuid={image.uuid}')
    candidates = [r for r in Router.objects.filter(router_type=image.device_type.slug, enabled=True)
                  .select_related('routerinformation') if image.matches_router(r)]
    return render(request, 'firmware/image.html', {'page_title': f'{image.device_type.name} {image.version}',
                                                   'image': image, 'candidates': candidates})


@login_required
def view_start_rollout(request):
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    image = get_object_or_404(FirmwareImage, uuid=request.GET.get('uuid'), enabled=True)
    from router_manager.templatetags.router_tags import compare_versions
    candidates = []
    for router in Router.objects.filter(router_type=image.device_type.slug, enabled=True) \
            .select_related('routerinformation').order_by('name'):
        if not image.matches_router(router):
            continue
        info = getattr(router, 'routerinformation', None)
        running = getattr(info, 'os_version', '') or ''
        candidates.append({'router': router, 'running': running,
                           'behind': compare_versions(running, image.version) == -1 if running else None})
    if request.method == 'POST':
        chosen = request.POST.getlist('routers')
        busy = set(RolloutTarget.objects.filter(rollout__state='running').values_list('router__uuid', flat=True))
        routers = [c['router'] for c in candidates if str(c['router'].uuid) in chosen]
        try:
            batch = max(1, min(int(request.POST.get('batch_size', 3)), 20))
        except ValueError:
            batch = 3
        if posted_confirmation(request) != 'update':
            messages.warning(request, 'Not started|The confirmation was not given.')
        elif not routers:
            messages.warning(request, 'Not started|Choose at least one device.')
        elif any(r.uuid in busy for r in routers):
            messages.warning(request, 'Not started|A chosen device is already in a running rollout.')
        else:
            # The canary first: the device chosen to go first, else the first in the list
            canary = request.POST.get('canary')
            routers.sort(key=lambda r: (str(r.uuid) != canary, r.name))
            rollout = FirmwareRollout.objects.create(image=image, batch_size=batch, created_by=request.user,
                                                     created_by_name=request.user.username)
            for position, router in enumerate(routers):
                RolloutTarget.objects.create(rollout=rollout, router=router, position=position)
            messages.success(request, f'Rollout started|{routers[0].name} goes first; the others follow when it '
                                      f'comes back on {image.version}.')
            return redirect(f'/system/firmware/rollout/?uuid={rollout.uuid}')
        return redirect(f'/system/firmware/rollout/start/?uuid={image.uuid}')
    return render(request, 'firmware/start_rollout.html', {'page_title': f'Update to {image.version}',
                                                           'image': image, 'candidates': candidates})


@login_required
def view_rollout(request):
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    rollout = get_object_or_404(FirmwareRollout.objects.select_related('image__device_type'),
                                uuid=request.GET.get('uuid'))
    if posted_action(request, 'abort') and posted_confirmation(request) == 'abort':
        engine.abort(rollout, request.user)
        messages.success(request, 'Rollout aborted|Devices not started yet are skipped.')
        return redirect(f'/system/firmware/rollout/?uuid={rollout.uuid}')
    targets = rollout.targets.select_related('router', 'backup')
    counts = {state: targets.filter(state=state).count() for state, _ in RolloutTarget.STATES}
    return render(request, 'firmware/rollout.html', {'page_title': 'Firmware rollout', 'rollout': rollout,
                                                     'targets': targets, 'counts': counts})


@cron_endpoint
def view_cron_rollouts(request):
    engine.advance_all()
    return JsonResponse({'running': FirmwareRollout.objects.filter(state='running').count()})
