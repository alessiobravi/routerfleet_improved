"""A discovery run: "Discover adjacent devices" asks the listener to listen on
RouterFleet's LAN for a while; this follows it step by step, so the page can
show what is happening and when - and what came of it:

1. asked - the listener reads the request within seconds;
2. listening - the listener confirms in its heartbeat; devices announce
   themselves on their own clock (LLDP every 30 s, CDP and MNDP every minute);
3. every device heard - new, or already in RouterFleet; a new one is checked
   for SSH from RouterFleet's host at once;
4. finished - with a summary.

advance() moves a run on from the listener's files; the page calls it while it
refreshes itself, and so does the scheduler, so the timeline is complete even
when nobody watches.
"""
import logging

from django.utils import timezone

from .models import DiscoveredDevice, DiscoveryRun
from .store import LAN, WINDOW_SECONDS, import_listener_file, listener_state

logger = logging.getLogger(__name__)

WAIT_SECONDS = 30      # for the listener to pick the request up
GRACE_SECONDS = 60     # after the window, finished even without the listener's word
STUCK_SECONDS = 900    # a devices run longer than this stopped with its worker


def _event(run, text, state='info', when=None):
    run.events.append({'when': (when or timezone.now()).isoformat(), 'text': text, 'state': state})


def start(user_name):
    run = DiscoveryRun.objects.create(until=timezone.now() + timezone.timedelta(seconds=WINDOW_SECONDS),
                                      requested_by_name=(user_name or '')[:150])
    _event(run, f'Asked by {user_name or "someone"}: listen {WINDOW_SECONDS // 60} minutes on RouterFleet\'s LAN '
                'for LLDP, CDP, MikroTik MNDP and Ubiquiti discovery. The listener picks the request up within seconds.')
    run.save(update_fields=['events'])
    return run


def current():
    """The run to show: the active one, else the last of the past hour."""
    run = DiscoveryRun.objects.first()
    if run and (run.active or run.created > timezone.now() - timezone.timedelta(hours=1)):
        return run
    return None


def name_of(device):
    return device.identity or device.address or device.mac or 'a device'


def _heard(run, device, routers, check_reach=True, via=''):
    """One device heard: in the run and its timeline - new or known, and for a
    new one whether SSH answers from RouterFleet's host."""
    from .reach import check, is_ip
    from .views import known_router
    if device.key in run.heard:
        return
    run.heard.append(device.key)
    protocols = ', '.join(p.upper() for p in device.protocols.split(',') if p) or 'a neighbour protocol'
    where = name_of(device) + (f' ({device.address})' if device.address and device.address != name_of(device) else '')
    how = f'{via} reports {where} over {protocols}' if via else f'Heard {where} over {protocols}'
    router = known_router(device, routers)
    if router:
        _event(run, f'{how} - already in RouterFleet as {router.name}.', 'info')
        return
    _event(run, f'{how} - new: see "Found in this discovery" below.', 'ok')
    if check_reach and is_ip(device.address):
        check([device])
        device.refresh_from_db()
        if device.reachable:
            _event(run, f'{name_of(device)}: SSH answers from RouterFleet\'s host - it can be added.', 'ok')
        else:
            _event(run, f'{name_of(device)}: SSH does not answer from RouterFleet\'s host '
                        f'({device.reachable_detail or "no answer"}) - it can not be added from here.', 'warn')


def start_devices(user_name, routers):
    """Reads the neighbour tables of the chosen managed devices, one after the
    other, in the background; the run's timeline says how it goes."""
    import threading
    from django.conf import settings
    run = DiscoveryRun.objects.create(kind='devices', state='reading', until=timezone.now(),
                                      requested_by_name=(user_name or '')[:150],
                                      targets=[str(r.uuid) for r in routers])
    names = ', '.join(r.name for r in routers)
    _event(run, f'Asked by {user_name or "someone"}: read the neighbour tables (LLDP, CDP, MNDP) of {names}. '
                'RouterFleet connects to each in turn over SSH.')
    run.save(update_fields=['events'])
    if getattr(settings, 'DISCOVERY_BACKGROUND', True):
        threading.Thread(target=_in_thread, args=(run.pk,), name='discovery-devices', daemon=True).start()
    else:
        read_devices(run.pk)
    return run


def _in_thread(run_pk):
    """read_devices in a thread of its own, which closes its database
    connection when done - never the caller's."""
    from django.db import connection
    try:
        read_devices(run_pk)
    finally:
        connection.close()


def read_devices(run_pk, check_reach=True):
    """The work of a 'devices' run."""
    from router_manager.models import Router
    from routerlib import device_session
    from .store import read_neighbours
    from .views import known_router
    try:
        run = DiscoveryRun.objects.get(pk=run_pk)
        routers = list(Router.objects.only('uuid', 'name', 'address'))
        found = 0
        chosen = {str(r.uuid): r for r in Router.objects.filter(uuid__in=run.targets)}
        for router in [chosen[u] for u in run.targets if u in chosen]:   # in the order chosen
            _event(run, f'Connecting to {router.name} ({router.address})…', 'run')
            run.save(update_fields=['events'])
            before = timezone.now()
            try:
                session = device_session.connect_router(router)
                try:
                    count = read_neighbours(session, router)
                finally:
                    session.close()
            except Exception as error:   # noqa: BLE001 - one device failing is not the others'
                _event(run, f'{router.name}: not read - {str(error)[:160] or type(error).__name__}.', 'bad')
                run.save(update_fields=['events'])
                continue
            if not count:
                _event(run, f'{router.name}: no neighbour reported (LLDP, CDP or MNDP may be off there, or its '
                            'device type has no neighbours command).', 'warn')
            for device in DiscoveredDevice.objects.filter(seen_by_router=router, last_seen__gte=before).order_by('identity'):
                found += 1
                _heard(run, device, routers, check_reach, via=router.name)
            run.save(update_fields=['events', 'heard'])
        new = sum(1 for key in run.heard if not known_router(DiscoveredDevice.objects.get(key=key), routers))
        run.state, run.finished_at = 'done', timezone.now()
        _event(run, f'Finished: {len(run.heard)} neighbour{"s" if len(run.heard) != 1 else ""} reported, {new} new.',
               'ok' if run.heard else 'warn')
        run.save()
    except Exception:   # noqa: BLE001 - recorded, never left running
        logger.exception('Discovery run failed')
        DiscoveryRun.objects.filter(pk=run_pk).update(state='failed', finished_at=timezone.now())


def advance(run, check_reach=True):
    """Moves the run on; returns it."""
    if run is None or not run.active:
        return run
    from .views import known_router
    from router_manager.models import Router
    now = timezone.now()
    if run.kind == 'devices':
        # Its thread does the work; one that stopped (the worker restarted) is said so
        if (now - run.created).total_seconds() > STUCK_SECONDS:
            run.state, run.finished_at = 'failed', now
            _event(run, 'Stopped before the end: the application restarted meanwhile. Start it again.', 'bad')
            run.save()
        return run
    state = listener_state()
    if run.state == 'waiting':
        if state['listening_left'] > 0:
            run.state, run.started_at = 'listening', now
            _event(run, 'The listener started listening. Devices announce themselves on their own clock: LLDP '
                        'about every 30 seconds, CDP and MNDP about every minute - each one heard is listed below '
                        'as it comes in.', 'run')
        elif (now - run.created).total_seconds() > WAIT_SECONDS:
            run.state, run.finished_at = 'failed', now
            _event(run, f'The listener did not start within {WAIT_SECONDS} seconds: it runs in the monitoring '
                        'container - is it up (docker compose ps)?', 'bad')
    import_listener_file()
    routers = list(Router.objects.only('uuid', 'name', 'address'))
    # The listener writes whole seconds, and is silent until asked: a few seconds of slack
    fresh = (DiscoveredDevice.objects.filter(seen_by=LAN, last_seen__gte=run.created - timezone.timedelta(seconds=5))
             .exclude(key__in=run.heard).order_by('last_seen'))
    for device in fresh:
        _heard(run, device, routers, check_reach)
    if run.state == 'listening' and ((now >= run.until and state['listening_left'] == 0)
                                     or (now - run.until).total_seconds() > GRACE_SECONDS):
        run.state, run.finished_at = 'done', now
        new = [k for k in run.heard if not known_router(DiscoveredDevice.objects.get(key=k), routers)] \
            if run.heard else []
        if run.heard:
            _event(run, f'Finished: {len(run.heard)} device{"s" if len(run.heard) != 1 else ""} heard, '
                        f'{len(new)} new. The listener is quiet again.', 'ok')
        else:
            _event(run, 'Finished: nothing heard. Devices on this LAN segment may not announce themselves '
                        '(LLDP, CDP or MNDP switched off), or RouterFleet\'s host is on another segment.', 'warn')
    run.save()
    return run


def page(run):
    """What the page shows of a run: progress and the timeline, newest first."""
    if run is None:
        return None
    from django.utils.dateparse import parse_datetime
    now = timezone.now()
    total = WINDOW_SECONDS
    if run.state == 'listening':
        left = max(0, int((run.until - now).total_seconds()))
        percent = round(100 * (total - left) / total)
    elif run.state == 'waiting':
        left, percent = total, 2
    else:
        left, percent = 0, 100
    if run.kind == 'devices':
        done = sum(1 for e in run.events if e['text'].startswith('Connecting to'))
        total_devices = len(run.targets) or 1
        percent = 100 if not run.active else max(3, round(100 * (done - 1) / total_devices))
        left = 0
        steps = [('Asked', True), (f'Reading {len(run.targets)} device{"s" if len(run.targets) != 1 else ""}', run.state == 'done'),
                 ('Finished', run.state == 'done')]
    else:
        steps = [('Asked', True),
                 ('Listener started', run.state in ('listening', 'done')),
                 (f'Listening {total // 60} min', run.state == 'done'),
                 ('Finished', run.state == 'done')]
    timeline = [{'when': parse_datetime(e['when']), 'text': e['text'], 'state': e.get('state', 'info')}
                for e in reversed(run.events)]
    return {'run': run, 'left': left, 'percent': percent, 'steps': steps, 'timeline': timeline,
            'heard': len(run.heard), 'found': found(run)}


def found(run):
    """The devices this discovery found, new ones first: what the page shows
    under "Found in this discovery"."""
    from router_manager.models import Router
    from .views import guess_type, known_router
    routers = list(Router.objects.only('uuid', 'name', 'address'))
    rows = []
    for device in DiscoveredDevice.objects.filter(key__in=run.heard):
        rows.append({'device': device, 'router': known_router(device, routers), 'type': guess_type(device),
                     'protocols': ', '.join(p.upper() for p in device.protocols.split(',') if p)})
    rows.sort(key=lambda r: (r['router'] is not None, (r['device'].identity or r['device'].address).lower()))
    return rows
