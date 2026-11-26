"""Discovered devices: what neighbour protocols revealed, to be added to
RouterFleet in one click - the device form prefilled, the device type guessed."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from router_manager.models import DeviceType, Router, RouterGroup
from routerlib.actions import posted_confirmation
from routerlib.decorators import cron_endpoint
from user_manager.models import UserAcl

from .models import DiscoveredDevice
from . import runs
from .store import WINDOW_SECONDS, ask_listener, import_listener_file, listener_state

LEVEL = 30   # Host Manager: who sees discovered devices
ADD_LEVEL = 50   # Administrators: who adds them, with the wizard
MOST_DEVICES = 10   # neighbour tables read in one discovery

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


DISCOVERY_URL = '/discovery/'
LIST_URL = '/router/discovered/'


@login_required
def view_discovery(request):
    """Device Discovery: discover adjacent devices (RouterFleet's LAN, the
    default) or read the neighbour tables of chosen managed devices - one at
    a time, followed step by step."""
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    busy = runs.current()
    busy = busy if busy and busy.active else None
    if request.method == 'POST' and request.POST.get('action') in ('listen', 'read_devices'):
        # Two clicks at the same moment must not start two discoveries: a lock
        # shared by every worker for the time it takes to start one
        from django.core.cache import caches
        if busy or not caches['limits'].add('rf-discovery-start', 1, 30):
            messages.warning(request, 'Not started|A discovery is running - follow it below; one at a time.')
            return redirect(DISCOVERY_URL)
        try:
            return _start_discovery(request)
        finally:
            caches['limits'].delete('rf-discovery-start')
    run = runs.advance(runs.current())   # also imports what the listener heard so far
    busy = run if run and run.active else None
    return render(request, 'discovery/discovery.html', {
        'page_title': 'Device Discovery', 'listener': listener_state(), 'live': runs.page(run), 'busy': busy,
        'window_minutes': WINDOW_SECONDS // 60, 'capable': neighbour_capable(), 'most_devices': MOST_DEVICES,
        'can_add': UserAcl.objects.filter(user=request.user, user_level__gte=ADD_LEVEL).exists()})



def _start_discovery(request):
    """Starts the discovery asked for (the caller holds the start lock)."""
    if request.POST.get('action') == 'read_devices':
        chosen = [r for r in neighbour_capable() if str(r.uuid) in request.POST.getlist('routers')][:MOST_DEVICES]
        if not chosen:
            messages.warning(request, 'Not started|Choose at least one device whose type reads neighbour tables.')
        else:
            runs.start_devices(request.user.get_full_name() or request.user.username, chosen)
        return redirect(DISCOVERY_URL)
    if request.POST.get('action') == 'listen':
        if not listener_state()['running']:
            messages.warning(request, 'Not started|The discovery listener is not running on this host - see Discovery in the documentation.')
        elif ask_listener():
            runs.start(request.user.get_full_name() or request.user.username)
        else:
            messages.error(request, 'Not started|The shared discovery folder can not be written - check the compose file.')
        return redirect(DISCOVERY_URL)
    return redirect(DISCOVERY_URL)


VIEWS = ('new', 'known', 'hidden')


def _classify():
    """The discovered devices as rows of the three views: new, already in
    RouterFleet, hidden. A device in RouterFleet heard by several protocols is
    one row; row['ids'] holds every record behind a row."""
    routers = list(Router.objects.only('uuid', 'name', 'address'))
    new, known, dismissed = [], [], []
    known_rows = {}   # one row per device in RouterFleet, however many protocols heard it
    for device in DiscoveredDevice.objects.select_related('seen_by_router'):
        router = known_router(device, routers)
        row = {'device': device, 'router': router, 'type': guess_type(device), 'ids': [device.pk], 'uuids': [str(device.uuid)],
               'protocols': ', '.join(p.upper() for p in device.protocols.split(',') if p)}
        if router:
            same = known_rows.get((router.pk, (device.identity or '').lower(), device.address))
            if same:
                merged = {p.strip() for p in (same['protocols'] + ', ' + row['protocols']).split(',') if p.strip()}
                same['protocols'] = ', '.join(sorted(merged))
                same['ids'].append(device.pk)
                same['uuids'].append(str(device.uuid))
                continue
            known_rows[(router.pk, (device.identity or '').lower(), device.address)] = row
            known.append(row)
        elif device.dismissed:
            dismissed.append(row)
        else:
            new.append(row)
    return new, known, dismissed


def _back(view, device=None):
    """The list again, on the same view and device - only values it knows."""
    query = f'?view={view}' if view in VIEWS else ''
    if device is not None:
        query += ('&' if query else '?') + f'device={device.uuid}'
    return redirect(LIST_URL + query)


@login_required
def view_discovered(request):
    """Discovered devices, as list and detail: new ones (added with the
    wizard when reachable), the ones already in RouterFleet, hidden ones on
    the left; the chosen device on the right. It refreshes itself while a
    discovery runs."""
    if not allowed(request):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    view = request.GET.get('view') if request.GET.get('view') in VIEWS else 'new'
    if request.method == 'POST' and request.POST.get('action') == 'recheck':
        from .reach import check
        device = get_object_or_404(DiscoveredDevice, uuid=request.POST.get('uuid'))
        check([device])
        return _back(view, device)
    if request.method == 'POST' and request.POST.get('action') in ('remove', 'clear'):
        # Only RouterFleet's record goes: a device heard again comes back as new
        rows = dict(zip(VIEWS, _classify()))[view]
        if request.POST.get('action') == 'remove':
            # Any record behind the row names it - which one the row shows depends on the database's order
            row = next((r for r in rows if request.POST.get('uuid', '') in r['uuids']), None)
            if row is None:
                raise Http404
            ids, label = row['ids'], (row['router'].name if row['router'] else row['device'].identity or row['device'].address or row['device'].mac)
        elif posted_confirmation(request) == 'clear':
            ids, label = [pk for r in rows for pk in r['ids']], None
        else:
            messages.warning(request, 'Not cleared|The confirmation was missing or wrong.')
            return _back(view)
        removed = DiscoveredDevice.objects.filter(pk__in=ids).delete()[0]
        messages.success(request, (f'Removed|{label} - it comes back if it is heard again.' if label else
                                   f'List cleared|{removed} discovered device{"s" if removed != 1 else ""} removed; '
                                   'they come back if they are heard again.'))
        return _back(view)
    if request.method == 'POST' and request.POST.get('action') in ('dismiss', 'restore'):
        device = get_object_or_404(DiscoveredDevice, uuid=request.POST.get('uuid'))
        device.dismissed = request.POST.get('action') == 'dismiss'
        device.save(update_fields=['dismissed'])
        if request.headers.get('Accept', '').startswith('application/json'):
            return JsonResponse({'ok': True, 'dismissed': device.dismissed})   # the row goes at once, no reload
        # Hidden: the list it left; shown again: it is new again, chosen there
        return _back('new', device) if not device.dismissed else _back(view)
    run = runs.advance(runs.current())   # what a running discovery found so far
    busy = run if run and run.active else None
    from django.conf import settings
    if getattr(settings, 'DISCOVERY_CHECK_ON_PAGE', True):
        from .reach import check_stale
        check_stale()        # reachability of what was not checked lately (parallel, bounded)
    new, known, dismissed = _classify()
    rows = {'new': new, 'known': known, 'hidden': dismissed}[view]
    chosen = next((row for row in rows if str(row['device'].uuid) == request.GET.get('device', '')), rows[0] if rows else None)
    return render(request, 'discovery/discovered.html', {
        'page_title': 'Discovered devices', 'new': new, 'known': known, 'dismissed': dismissed,
        'view': view, 'rows': rows, 'chosen': chosen, 'busy': busy,
        'can_add': UserAcl.objects.filter(user=request.user, user_level__gte=ADD_LEVEL).exists()})


def neighbour_capable():
    """The managed devices whose device type has a neighbours command."""
    from fleet_commander import system_commands
    capable = {}
    result = []
    for router in Router.objects.filter(enabled=True).select_related('routerinformation').order_by('name'):
        if router.router_type not in capable:
            capable[router.router_type] = bool(system_commands.payload_of('neighbors', router.router_type).strip())
        if capable[router.router_type]:
            result.append(router)
    return result


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
    """Imports what the listener heard, and checks the reachability of what
    was not checked lately."""
    from .reach import check_stale
    run = runs.advance(runs.current())
    return JsonResponse({'imported': import_listener_file(), 'checked': check_stale(),
                         'run': run.state if run else None})


@login_required
def view_add_discovered(request):
    """Adding a discovered device, for administrators, when RouterFleet's
    host reaches it: by its IP address - the device, how to sign in (tested
    before saving, if wished), backups and monitoring."""
    from router_manager.forms import RouterForm
    from .reach import is_ip
    if not UserAcl.objects.filter(user=request.user, user_level__gte=ADD_LEVEL).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    device = get_object_or_404(DiscoveredDevice, uuid=request.GET.get('uuid'))
    if not is_ip(device.address) or not device.reachable:
        messages.warning(request, 'Not added|RouterFleet\'s host does not reach this device by its IP address on SSH - check again first.')
        return redirect('/router/discovered/')
    if request.method == 'POST':
        data = request.POST.copy()
        data['address'] = device.address   # by its IP address, as discovered
        form = RouterForm(data)
        new_group = (request.POST.get('new_group') or '').strip()[:100]
        if form.is_valid():
            from django.db import transaction
            with transaction.atomic():
                router = form.save()
                groups = list(RouterGroup.objects.filter(uuid__in=request.POST.getlist('groups')))
                if new_group:
                    # An existing name (whatever its case) is that group, not a second one
                    group = RouterGroup.objects.filter(name__iexact=new_group).first() \
                        or RouterGroup.objects.create(name=new_group)
                    groups.append(group)
                for group in groups:
                    group.routers.add(router)
            from router_manager.views import saved_router_message
            text = saved_router_message(router, True, set(form.changed_data))
            if groups:
                text += f' In {", ".join(sorted({g.name for g in groups}))}.'
            messages.success(request, text)
            return redirect(f'/router/details/?uuid={router.uuid}')
        chosen = set(request.POST.getlist('groups'))
    else:
        form = RouterForm(initial=prefill(device.uuid))
        new_group = ''
        # The device list opens on the default group: a new device is shown there
        chosen = {str(u) for u in RouterGroup.objects.filter(default_group=True).values_list('uuid', flat=True)}
    for name in ('router_type', 'ssh_key', 'backup_profile'):
        form.fields[name].widget.attrs['class'] = 'form-select'
    return render(request, 'discovery/add.html', {
        'page_title': 'Add a discovered device', 'device': device, 'form': form,
        'groups': RouterGroup.objects.order_by('name'), 'chosen_groups': chosen, 'new_group': new_group})
