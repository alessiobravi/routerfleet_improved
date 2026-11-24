"""Discovered devices: what neighbour protocols revealed, to be added to
RouterFleet in one click - the device form prefilled, the device type guessed."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from router_manager.models import DeviceType, Router
from routerlib.decorators import cron_endpoint
from user_manager.models import UserAcl

from .models import DiscoveredDevice
from .store import import_listener_file

LEVEL = 30   # Host Manager: who adds devices

# What a platform or version text says about the device type (shipped slugs)
GUESSES = (('mikrotik', 'routeros'), ('routeros', 'routeros'), ('openwrt', 'openwrt'), ('junos', 'juniper-junos'),
           ('juniper', 'juniper-junos'), ('huawei', 'huawei-vrp'), ('vrp', 'huawei-vrp'), ('ubiquiti', 'ubiquiti-airos'),
           ('airos', 'ubiquiti-airos'))


def guess_type(device):
    text = f'{device.platform} {device.model} {device.version}'.lower()
    slugs = set(DeviceType.objects.filter(enabled=True).values_list('slug', flat=True))
    if 'cisco' in text or ' ios' in f' {text}':
        xe = 'ios xe' in text or 'ios-xe' in text or 'denali' in text
        slug = 'cisco-ios' if xe or 'cisco-ios-classic' not in slugs else 'cisco-ios-classic'
        return slug if slug in slugs else ''
    for word, slug in GUESSES:
        if word in text and slug in slugs:
            return slug
    return ''


def known_router(device, routers):
    """The managed device this is, by address or name."""
    for router in routers:
        if device.address and router.address == device.address:
            return router
        if device.identity and router.name.lower() == device.identity.lower():
            return router
    return None


def allowed(request):
    return UserAcl.objects.filter(user=request.user, user_level__gte=LEVEL).exists()


@login_required
def view_discovered(request):
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    if request.method == 'POST' and request.POST.get('action') in ('dismiss', 'restore'):
        device = get_object_or_404(DiscoveredDevice, uuid=request.POST.get('uuid'))
        device.dismissed = request.POST.get('action') == 'dismiss'
        device.save(update_fields=['dismissed'])
        return redirect('/router/discovered/' + ('?dismissed=1' if request.GET.get('dismissed') else ''))
    routers = list(Router.objects.only('uuid', 'name', 'address'))
    new, known, dismissed = [], [], []
    for device in DiscoveredDevice.objects.select_related('seen_by_router'):
        router = known_router(device, routers)
        row = {'device': device, 'router': router, 'type': guess_type(device),
               'protocols': ', '.join(p.upper() for p in device.protocols.split(',') if p)}
        if router:
            known.append(row)
        elif device.dismissed:
            dismissed.append(row)
        else:
            new.append(row)
    return render(request, 'discovery/discovered.html', {
        'page_title': 'Discovered devices', 'new': new, 'known': known, 'dismissed': dismissed,
        'show_dismissed': bool(request.GET.get('dismissed'))})


def prefill(uuid):
    """The device form's starting values for a discovered device, or None."""
    device = DiscoveredDevice.objects.filter(uuid=uuid).first() if uuid else None
    if device is None:
        return None
    initial = {'name': (device.identity or device.address)[:100], 'address': device.address}
    guessed = guess_type(device)
    if guessed:
        initial['router_type'] = guessed
    return initial


@cron_endpoint
def view_cron_discovery(request):
    """Imports what the optional listener heard on RouterFleet's LAN."""
    return JsonResponse({'imported': import_listener_file()})
