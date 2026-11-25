import importlib.util
import json
import pathlib
import struct
import tempfile

from django.test import TestCase, override_settings

from routerfleet.testing import make_router, make_user

from . import parsers
from .models import DiscoveredDevice
from .store import import_listener_file, store

SAMPLES = pathlib.Path(__file__).resolve().parent / 'samples'


def sample(name):
    return parsers.parse((SAMPLES / name).read_text())


class NeighbourParserTests(TestCase):
    """Each vendor's neighbour output, recognised by its own markers (samples
    written from the documented formats)."""

    def test_routeros(self):
        first, second = sample('routeros_terse.txt')
        self.assertEqual((first['identity'], first['address'], first['mac'], first['model'], first['version']),
                         ('AP02-EXAMPLE', '192.168.88.12', '48:a9:8a:01:02:03', 'cAP ax', '7.16.2 (stable)'))
        self.assertEqual(first['protocols'], {'cdp', 'lldp', 'mndp'})
        self.assertEqual((second['identity'], second['remote_interface']), ('core-sw-01', 'GigabitEthernet0/24'))

    def test_cisco_cdp_and_lldp(self):
        cdp = sample('cisco_cdp.txt')
        self.assertEqual([(r['identity'], r['address']) for r in cdp],
                         [('access-sw-02.example.net', '10.20.0.2'), ('rtr-edge', '10.20.0.1')])
        self.assertIn('15.2(7)E8', cdp[0]['version'])
        lldp, = sample('cisco_lldp.txt')
        self.assertEqual((lldp['identity'], lldp['address'], lldp['mac'], lldp['interface']),
                         ('hap-office', '10.20.0.30', '48:a9:8a:01:02:03', 'Gi0/3'))

    def test_huawei_junos_lldpcli(self):
        huawei, = sample('huawei_lldp.txt')
        self.assertEqual((huawei['identity'], huawei['address'], huawei['mac'], huawei['interface']),
                         ('S5735-core', '10.30.0.2', '00:e0:fc:12:34:56', 'GigabitEthernet0/0/1'))
        self.assertEqual([r['identity'] for r in sample('junos_lldp.txt')], ['branch-hap', 'core-sw-02'])
        lldpd, = sample('lldpcli.txt')
        self.assertEqual((lldpd['identity'], lldpd['address']), ('AP02-EXAMPLE', '192.168.88.12'))

    def test_nothing_recognised_finds_nothing(self):
        self.assertEqual(parsers.parse('bash: lldpcli: not found'), [])


class StoreTests(TestCase):
    def test_merged_by_mac_across_sources_and_protocols(self):
        router = make_router(name='seer')
        store(sample('routeros_terse.txt'), seen_by='seer', router=router)
        store(sample('lldpcli.txt'), seen_by='other')
        device = DiscoveredDevice.objects.get(mac='48:a9:8a:01:02:03')
        self.assertEqual(DiscoveredDevice.objects.filter(mac='48:a9:8a:01:02:03').count(), 1)
        self.assertEqual(device.protocols, 'cdp,lldp,mndp')
        self.assertEqual(device.seen_by, 'other')

    def test_the_listener_file_is_imported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'seen.json'
            path.write_text(json.dumps({'devices': {'48:a9:8a:00:00:01': {
                'mac': '48:a9:8a:00:00:01', 'identity': 'lan-hap', 'address': '192.168.1.2',
                'protocols': ['mndp', 'evil'], 'platform': 'MikroTik'}}}))
            self.assertEqual(import_listener_file(str(path)), 1)
            self.assertEqual(import_listener_file(str(pathlib.Path(folder) / 'missing.json')), 0)
        device = DiscoveredDevice.objects.get(identity='lan-hap')
        self.assertEqual((device.seen_by, device.protocols), ('RouterFleet LAN', 'mndp'))


class DiscoveredPageTests(TestCase):
    def setUp(self):
        store(sample('routeros_terse.txt'), seen_by='seer')
        make_router(name='core-sw-01', address='10.0.0.5')

    def test_new_and_known_and_one_click_add(self):
        self.client.force_login(make_user(30))
        page = self.client.get('/router/discovered/').content.decode()
        self.assertIn('AP02-EXAMPLE', page)
        self.assertIn('Already in RouterFleet', page)
        device = DiscoveredDevice.objects.get(identity='AP02-EXAMPLE')
        form = self.client.get(f'/router/manage/?discovered={device.uuid}').content.decode()
        self.assertIn('value="AP02-EXAMPLE"', form)
        self.assertIn('value="192.168.88.12"', form)
        self.assertRegex(form, r'<option value="routeros"[^>]*selected')

    def test_dismissed_and_restored(self):
        self.client.force_login(make_user(30))
        device = DiscoveredDevice.objects.get(identity='AP02-EXAMPLE')
        self.client.post('/router/discovered/', {'action': 'dismiss', 'uuid': str(device.uuid)})
        self.assertNotIn('AP02-EXAMPLE', self.client.get('/router/discovered/').content.decode().split('Already in RouterFleet')[0])
        self.client.post('/router/discovered/', {'action': 'restore', 'uuid': str(device.uuid)})
        self.assertFalse(DiscoveredDevice.objects.get(pk=device.pk).dismissed)

    def test_below_host_manager_denied(self):
        self.client.force_login(make_user(20))
        self.assertContains(self.client.get('/router/discovered/'), 'Access Denied')


def listener():
    path = pathlib.Path(__file__).resolve().parent.parent / 'containers' / 'monitoring' / 'listener.py'
    spec = importlib.util.spec_from_file_location('discovery_listener', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ListenerPacketTests(TestCase):
    """The optional listener's decoding of MNDP, LLDP and CDP, from frames
    built byte by byte after the protocols' layouts."""

    def test_mndp(self):
        tlv = lambda kind, value: struct.pack('>HH', kind, len(value)) + value
        data = b'\x00\x00\x00\x01' + tlv(1, bytes.fromhex('48a98a010203')) + tlv(5, b'hap-lan') + tlv(7, b'7.16.2 (stable)') \
            + tlv(8, b'MikroTik') + tlv(12, b'hAP ax3') + tlv(17, bytes([192, 168, 88, 1]))
        record = listener().parse_mndp(data, '192.168.88.1')
        self.assertEqual((record['identity'], record['mac'], record['address'], record['model']),
                         ('hap-lan', '48:a9:8a:01:02:03', '192.168.88.1', 'hAP ax3'))

    def test_lldp_frame(self):
        def tlv(kind, value):
            return struct.pack('>H', (kind << 9) | len(value)) + value
        payload = tlv(1, b'\x04' + bytes.fromhex('001122334455')) + tlv(2, b'\x05ether1') + tlv(3, b'\x00\x78') \
            + tlv(5, b'sw-lldp') + tlv(6, b'Linux lldpd') + tlv(8, b'\x05\x01' + bytes([10, 0, 0, 9]) + b'\x02\x00\x00\x00\x01\x00') + tlv(0, b'')
        frame = bytes.fromhex('0180c200000e') + bytes.fromhex('001122334455') + b'\x88\xcc' + payload
        protocol, record = listener().parse_frame(frame)
        self.assertEqual((protocol, record['identity'], record['mac'], record['address'], record['remote_interface']),
                         ('lldp', 'sw-lldp', '00:11:22:33:44:55', '10.0.0.9', 'ether1'))

    def test_cdp_frame(self):
        def tlv(kind, value):
            return struct.pack('>HH', kind, len(value) + 4) + value
        address = struct.pack('>I', 1) + bytes([1, 1, 0xCC]) + struct.pack('>H', 4) + bytes([10, 0, 0, 7])
        payload = b'\x02\xb4\x00\x00' + tlv(1, b'sw-cdp') + tlv(2, address) + tlv(3, b'Gi0/1') + tlv(6, b'cisco WS-C2960')
        frame = bytes.fromhex('01000ccccccc') + bytes.fromhex('00aabbccddee') + struct.pack('>H', len(payload) + 8) \
            + b'\xaa\xaa\x03\x00\x00\x0c\x20\x00' + payload
        protocol, record = listener().parse_frame(frame)
        self.assertEqual((protocol, record['identity'], record['address'], record['platform']),
                         ('cdp', 'sw-cdp', '10.0.0.7', 'cisco WS-C2960'))


class ReadNeighboursTests(TestCase):
    def test_through_the_system_command_of_the_device_type(self):
        from unittest import mock
        from .store import read_neighbours
        router = make_router(name='mt1', router_type='routeros')
        output = (SAMPLES / 'routeros_terse.txt').read_bytes()
        session = mock.Mock()
        session.exec_command.return_value = (None, mock.Mock(read=mock.Mock(return_value=output)), None)
        self.assertEqual(read_neighbours(session, router), 2)
        session.exec_command.assert_called_once_with('/ip neighbor print terse')
        self.assertEqual(DiscoveredDevice.objects.get(identity='AP02-EXAMPLE').seen_by_router, router)


class DiscoverAdjacentTests(TestCase):
    """"Discover adjacent devices" asks the dormant listener for a window
    through the shared folder; the page shows whether it runs."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.settings_override = override_settings(DISCOVERY_FILE=str(pathlib.Path(self.folder.name) / 'seen.json'))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.client.force_login(make_user(30))

    def heartbeat(self, listening_for=0):
        import time
        (pathlib.Path(self.folder.name) / 'listener.json').write_text(json.dumps(
            {'alive': time.time(), 'listening_until': time.time() + listening_for if listening_for else 0}))

    def test_not_running_the_button_is_greyed_out(self):
        page = self.client.get('/discovery/').content.decode()
        self.assertIn('Not running on this host', page)
        self.assertRegex(page, r'rf-set-action w-100 rf-set-disabled"\s+disabled')
        self.client.post('/discovery/', {'action': 'listen'})
        self.assertFalse((pathlib.Path(self.folder.name) / 'request.json').exists())

    def test_running_the_button_opens_a_window(self):
        import time
        self.heartbeat()
        self.assertIn('Ready, dormant', self.client.get('/discovery/').content.decode())
        self.client.post('/discovery/', {'action': 'listen'})
        until = json.loads((pathlib.Path(self.folder.name) / 'request.json').read_text())['until']
        self.assertAlmostEqual(until - time.time(), 180, delta=5)
        self.heartbeat(listening_for=120)
        page = self.client.get('/discovery/').content.decode()
        self.assertIn('Listening -', page)
        self.assertIn('data-rf-live="active"', page)

    def test_the_listener_caps_a_window_and_ignores_a_bad_request(self):
        import time
        module = listener()
        module.REQUEST = str(pathlib.Path(self.folder.name) / 'request.json')
        self.assertEqual(module.requested_until(), 0)
        pathlib.Path(module.REQUEST).write_text(json.dumps({'until': time.time() + 99999}))
        self.assertLessEqual(module.requested_until(), time.time() + module.LONGEST_WINDOW + 1)
        pathlib.Path(module.REQUEST).write_text('not json')
        self.assertEqual(module.requested_until(), 0)


class MoreVendorsTests(TestCase):
    """Huawei CDP (received with lldp compliance cdp receive), Junos LLDP
    details, lldpd CDP neighbours, Ubiquiti discovery announcements."""

    def test_huawei_cdp(self):
        record, = sample('huawei_cdp.txt')
        self.assertEqual((record['identity'], record['mac'], record['platform'], record['remote_interface'], record['protocols']),
                         ('ME3400', 'b4:a4:e3:cf:e9:84', 'cisco ME-3400EG-2CS-A', 'GigabitEthernet0/4', {'cdp'}))
        self.assertIn('12.2(55)SE3', record['version'])

    def test_junos_detail(self):
        record, = sample('junos_detail.txt')
        self.assertEqual((record['identity'], record['address'], record['mac'], record['interface']),
                         ('edge-junos-01.example.net', '192.0.2.232', '00:00:5e:00:53:e0', 'ge-0/0/8'))

    def test_junos_old_mac_spelling(self):
        self.assertEqual(parsers.normal_mac('00 19 bb 20 de 80'), '00:19:bb:20:de:80')

    def test_lldpd_cdp_neighbour(self):
        record, = sample('lldpcli_cdp.txt')
        self.assertEqual((record['identity'], record['address'], record['protocols']), ('core-sw-03', '10.40.0.3', {'cdp'}))

    def test_ubiquiti_announcement(self):
        def tlv(kind, value):
            return bytes([kind]) + struct.pack('>H', len(value)) + value
        payload = tlv(0x02, bytes.fromhex('245a4c112233') + bytes([192, 168, 1, 20])) + tlv(0x0B, b'nanobeam-roof') \
            + tlv(0x0C, b'NBE-5AC-Gen2') + tlv(0x03, b'XC.ar934x.v8.7.11')
        data = b'\x01\x00' + struct.pack('>H', len(payload)) + payload
        record = listener().parse_ubnt(data, '192.168.1.20')
        self.assertEqual((record['identity'], record['mac'], record['address'], record['model']),
                         ('nanobeam-roof', '24:5a:4c:11:22:33', '192.168.1.20', 'NBE-5AC-Gen2'))
        self.assertIsNone(listener().parse_ubnt(b'\x07\x00\x00\x00'))


class ListenerWithoutFolderTests(TestCase):
    def test_an_older_compose_file_leaves_monitoring_alone(self):
        module = listener()
        module.FOLDER = '/no/such/folder'
        self.assertIsNone(module.main())   # returns at once instead of failing on its first write


class ReachabilityAndWizardTests(TestCase):
    """A discovered device is checked from RouterFleet's host (its SSH port);
    reachable, it can be added by its IP address with the wizard - by an
    administrator."""

    def setUp(self):
        store(sample('routeros_terse.txt'), seen_by='seer')
        self.device = DiscoveredDevice.objects.get(identity='AP02-EXAMPLE')

    def test_the_check_records_what_it_found(self):
        from unittest import mock
        from .reach import check
        with mock.patch('discovery.reach.socket.create_connection', side_effect=ConnectionRefusedError()):
            check([self.device])
        self.device.refresh_from_db()
        self.assertFalse(self.device.reachable)
        self.assertIn('refused', self.device.reachable_detail)
        with mock.patch('discovery.reach.socket.create_connection') as connect:
            check([self.device])
        connect.assert_called_once_with(('192.168.88.12', 22), timeout=1.5)
        self.device.refresh_from_db()
        self.assertTrue(self.device.reachable)
        self.assertIsNotNone(self.device.checked_at)

    def test_the_wizard_adds_it_by_its_ip_address(self):
        from router_manager.models import Router
        DiscoveredDevice.objects.filter(pk=self.device.pk).update(reachable=True, reachable_detail='SSH (port 22) answers')
        self.client.force_login(make_user(50))
        url = f'/router/discovered/add/?uuid={self.device.uuid}'
        page = self.client.get(url).content.decode()
        self.assertIn('value="AP02-EXAMPLE"', page)
        self.assertIn('Test connection', page)
        response = self.client.post(url, {'name': 'ap02', 'router_type': 'routeros', 'username': 'rfbackup',
                                          'password': 'device-secret-1', 'port': '22', 'address': '10.66.66.66',
                                          'monitoring': 'on', 'enabled': 'on', 'keep_unchanged': 'profile'})
        router = Router.objects.get(name='ap02')
        self.assertRedirects(response, f'/router/details/?uuid={router.uuid}', fetch_redirect_response=False)
        self.assertEqual(router.address, '192.168.88.12', 'the discovered IP address, whatever was posted')

    def test_not_reachable_or_not_an_administrator(self):
        self.client.force_login(make_user(50))
        DiscoveredDevice.objects.filter(pk=self.device.pk).update(reachable=False)
        self.assertRedirects(self.client.get(f'/router/discovered/add/?uuid={self.device.uuid}'), '/router/discovered/',
                             fetch_redirect_response=False)
        DiscoveredDevice.objects.filter(pk=self.device.pk).update(reachable=True)
        self.client.force_login(make_user(40))
        self.assertContains(self.client.get(f'/router/discovered/add/?uuid={self.device.uuid}'), 'Access Denied')
        from unittest import mock
        with mock.patch('discovery.reach.check_stale'):
            page = self.client.get('/router/discovered/').content.decode()
        self.assertNotIn('/router/discovered/add/', page, 'no add button below administrator')
        self.assertIn('Reachable from RouterFleet', page)


class IPv6DiscoveryTests(TestCase):
    """IPv6 management addresses in every format and in the listener's
    decoding; link-local addresses are never offered for adding."""

    def test_neighbour_tables(self):
        record, = parsers.parse(' 0 interface=ether1 address=fe80::1 address6=2001:db8:10::2 '
                                'mac-address=48:A9:8A:00:00:09 identity=v6-only platform=MikroTik discovered-by=mndp')
        self.assertEqual(record['address'], '2001:db8:10::2')
        record, = parsers.parse('------\nLocal Intf: Gi0/1\nSystem Name: v6ap\n\nManagement Addresses:\n    IPV6: 2001:db8:30::4\n------\n')
        self.assertEqual(record['address'], '2001:db8:30::4')
        self.assertEqual(parsers.usable_address('fe80::1%ether1', 'ff02::1'), '')

    def test_listener_lldp_and_cdp(self):
        module = listener()

        def lldp(kind, value):
            return struct.pack('>H', (kind << 9) | len(value)) + value
        v6 = bytes.fromhex('20010db8000000000000000000000042')
        payload = lldp(1, b'\x04' + bytes.fromhex('001122334466')) + lldp(5, b'v6-sw') \
            + lldp(8, b'\x11\x02' + v6 + b'\x02\x00\x00\x00\x01\x00') + lldp(0, b'')
        frame = bytes.fromhex('0180c200000e') + bytes.fromhex('001122334466') + b'\x88\xcc' + payload
        self.assertEqual(module.parse_frame(frame)[1]['address'], '2001:db8::42')

        def cdp(kind, value):
            return struct.pack('>HH', kind, len(value) + 4) + value
        entry = bytes([2, 8]) + bytes.fromhex('aaaa0300000086dd') + struct.pack('>H', 16) + v6
        payload = b'\x02\xb4\x00\x00' + cdp(1, b'v6-cdp') + cdp(2, struct.pack('>I', 1) + entry)
        frame = bytes.fromhex('01000ccccccc') + bytes.fromhex('00aabbccdd01') + struct.pack('>H', len(payload) + 8) \
            + b'\xaa\xaa\x03\x00\x00\x0c\x20\x00' + payload
        self.assertEqual(module.parse_frame(frame)[1]['address'], '2001:db8::42')
        self.assertEqual(module.usable('fe80::1%eth0'), '')

    def test_reachable_and_added_by_an_ipv6_address(self):
        from unittest import mock
        from .reach import check, is_ip
        self.assertTrue(is_ip('2001:db8::7'))
        self.assertFalse(is_ip('fe80::7'))
        device = DiscoveredDevice.objects.create(key='mac:00:00:00:00:00:77', address='2001:db8::7', identity='v6',
                                                 last_seen='2026-11-25T10:00:00Z')
        with mock.patch('discovery.reach.socket.create_connection') as connect:
            check([device])
        connect.assert_called_once_with(('2001:db8::7', 22), timeout=1.5)


class DiscoveryRunTests(TestCase):
    """"Discover adjacent devices" as a run with a timeline: asked, the
    listener started, every device heard (new or known; new ones checked for
    SSH), finished with a summary - or the listener did not start."""

    def setUp(self):
        self.folder = pathlib.Path(tempfile.mkdtemp())
        override = override_settings(DISCOVERY_FILE=str(self.folder / 'seen.json'))
        override.enable()
        self.addCleanup(override.disable)
        self.client.force_login(make_user(30))

    def heartbeat(self, listening_for=0):
        import time
        (self.folder / 'listener.json').write_text(json.dumps(
            {'alive': time.time(), 'listening_until': time.time() + listening_for if listening_for else 0}))

    def heard(self, devices):
        (self.folder / 'seen.json').write_text(json.dumps({'devices': devices}))

    def run_events(self):
        from .models import DiscoveryRun
        run = DiscoveryRun.objects.get()
        return run, [e['text'] for e in run.events]

    def test_a_run_step_by_step(self):
        import time
        from unittest import mock
        from django.utils import timezone
        from . import runs
        make_router(name='gw', address='192.0.2.254')
        self.heard({'old': {'identity': 'heard-last-week', 'address': '192.0.2.9', 'protocols': ['mndp'],
                            'last_seen': int(time.time()) - 7 * 86400}})
        self.heartbeat()
        self.client.post('/discovery/', {'action': 'listen'})
        run, events = self.run_events()
        self.assertEqual(run.state, 'waiting')
        self.assertIn('listen 3 minutes', events[0])
        page = self.client.get('/discovery/').content.decode()
        self.assertIn('data-rf-live="active"', page, 'refreshes itself from the click on')
        self.assertIn('Waiting for the listener', page)
        # The listener starts; two devices announce themselves
        self.heartbeat(listening_for=170)
        now = int(time.time())
        self.heard({'old': {'identity': 'heard-last-week', 'address': '192.0.2.9', 'protocols': ['mndp'],
                            'last_seen': now - 7 * 86400},
                    'gw': {'identity': 'gw', 'address': '192.0.2.254', 'protocols': ['lldp'], 'last_seen': now},
                    'sta': {'identity': 'station-example', 'address': '192.0.2.249', 'mac': '00:11:22:33:44:55',
                            'protocols': ['mndp'], 'last_seen': now}})

        def reachable(devices, port=22):
            for device in devices:
                device.reachable, device.reachable_detail = True, 'SSH (port 22) answers'
                device.save()
            return len(devices)
        with mock.patch('discovery.reach.check', side_effect=reachable):
            page = self.client.get('/discovery/').content.decode()
        run, events = self.run_events()
        self.assertEqual(run.state, 'listening')
        self.assertTrue(any('started listening' in e for e in events))
        self.assertTrue(any('Heard gw' in e and 'already in RouterFleet as gw' in e for e in events), events)
        self.assertTrue(any('Heard station-example (192.0.2.249) over MNDP - new' in e for e in events))
        self.assertTrue(any('station-example: SSH answers' in e for e in events))
        self.assertFalse(any('heard-last-week' in e for e in events), 'heard before this run: not in it')
        self.assertIn('2 devices heard so far', page)
        # What it found is on this page too: new first, then the known one
        found = [r['device'].identity for r in self.client.get('/discovery/').context['live']['found']]
        self.assertEqual(found, ['station-example', 'gw'])
        self.assertIn('Found so far - 2', page)
        # The window ends: finished, with a summary; the page stops refreshing
        from .models import DiscoveryRun
        DiscoveryRun.objects.update(until=timezone.now() - timezone.timedelta(seconds=1))
        self.heartbeat()
        page = self.client.get('/discovery/').content.decode()
        run, events = self.run_events()
        self.assertEqual(run.state, 'done')
        self.assertIn('Finished: 2 devices heard, 1 new', events[-1])
        self.assertIn('data-rf-live="done"', page)
        self.assertIn('Last discovery', page)
        self.assertEqual(runs.advance(run), run, 'a finished run stays as it is')

    def test_the_listener_does_not_start(self):
        from django.utils import timezone
        from .models import DiscoveryRun
        self.heartbeat()
        self.client.post('/discovery/', {'action': 'listen'})
        DiscoveryRun.objects.update(created=timezone.now() - timezone.timedelta(seconds=40))
        self.client.get('/discovery/')
        run, events = self.run_events()
        self.assertEqual(run.state, 'failed')
        self.assertIn('did not start within 30 seconds', events[-1])

    def test_nothing_heard_says_why_it_may_be(self):
        from django.utils import timezone
        from . import runs
        from .models import DiscoveryRun
        run = runs.start('admin')
        DiscoveryRun.objects.filter(pk=run.pk).update(state='listening', until=timezone.now() - timezone.timedelta(seconds=90))
        run = runs.advance(DiscoveryRun.objects.get(pk=run.pk), check_reach=False)
        self.assertEqual(run.state, 'done')
        self.assertIn('nothing heard', run.events[-1]['text'])

    def test_one_known_device_heard_over_two_protocols_is_one_row(self):
        make_router(name='gw', address='192.0.2.254')
        store([{'identity': 'GW', 'address': '192.0.2.254', 'mac': '00:aa:bb:cc:dd:01', 'protocols': {'mndp'}}])
        store([{'identity': 'GW', 'address': '192.0.2.254', 'protocols': {'lldp'}}])
        page = self.client.get('/router/discovered/')
        self.assertEqual(len(page.context['known']), 1)
        self.assertEqual(page.context['known'][0]['protocols'], 'LLDP, MNDP')

    def test_the_scheduler_moves_a_run_on(self):
        from . import runs
        runs.start('admin')
        self.heartbeat(listening_for=100)
        from django.test import Client
        answer = Client().get('/cron/discovery/', REMOTE_ADDR='127.0.0.1')
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()['run'], 'listening')


class DeviceDiscoveryTests(TestCase):
    """Neighbour tables are read only from the managed devices chosen, one
    after the other, followed in the run's timeline; one discovery at a time;
    the eye hides a row in the background."""

    def setUp(self):
        self.client.force_login(make_user(30))
        self.mt1 = make_router(name='mt1', router_type='routeros', address='192.0.2.1')
        self.mt2 = make_router(name='mt2', router_type='routeros', address='192.0.2.2')

    def session(self, output):
        from unittest import mock
        session = mock.Mock()
        session.exec_command.return_value = (None, mock.Mock(read=mock.Mock(return_value=output)), None)
        return session

    def test_only_the_chosen_devices_are_read_and_the_timeline_says_so(self):
        from unittest import mock
        from .models import DiscoveryRun
        page = self.client.get('/discovery/')
        self.assertEqual([r.name for r in page.context['capable']], ['mt1', 'mt2'])
        output = (SAMPLES / 'routeros_terse.txt').read_bytes()
        with mock.patch('routerlib.device_session.connect_router', return_value=self.session(output)) as connect, \
                mock.patch('discovery.reach.check', return_value=0):
            self.client.post('/discovery/', {'action': 'read_devices', 'routers': [str(self.mt2.uuid)]})
        self.assertEqual([c.args[0] for c in connect.call_args_list], [self.mt2], 'mt1 was not chosen: not read')
        run = DiscoveryRun.objects.get()
        texts = [e['text'] for e in run.events]
        self.assertEqual((run.kind, run.state), ('devices', 'done'))
        self.assertIn('read the neighbour tables (LLDP, CDP, MNDP) of mt2', texts[0])
        self.assertIn('Connecting to mt2 (192.0.2.2)', texts[1])
        self.assertTrue(any(t.startswith('mt2 reports AP02-EXAMPLE') for t in texts), texts)
        self.assertIn('Finished: 2 neighbours reported', texts[-1])
        page = self.client.get('/discovery/')
        self.assertIn('Last discovery - neighbour tables', page.content.decode())
        self.assertEqual(sorted(r['device'].identity for r in page.context['live']['found']),
                         ['AP02-EXAMPLE', 'core-sw-01'], 'what was found is on the page')
        self.assertIn('Found in the last discovery - 2', page.content.decode())

    def test_a_device_that_does_not_answer_is_in_the_timeline_and_the_others_go_on(self):
        from unittest import mock
        from .models import DiscoveryRun
        output = (SAMPLES / 'routeros_terse.txt').read_bytes()
        with mock.patch('routerlib.device_session.connect_router',
                        side_effect=[OSError('timed out'), self.session(output)]), \
                mock.patch('discovery.reach.check', return_value=0):
            self.client.post('/discovery/', {'action': 'read_devices',
                                                     'routers': [str(self.mt1.uuid), str(self.mt2.uuid)]})
        texts = [e['text'] for e in DiscoveryRun.objects.get().events]
        self.assertIn('mt1: not read - timed out.', texts)
        self.assertTrue(any(t.startswith('mt2 reports') for t in texts))

    def test_one_discovery_at_a_time_and_nothing_chosen(self):
        from . import runs
        from .models import DiscoveryRun
        self.client.post('/discovery/', {'action': 'read_devices'})
        self.assertFalse(DiscoveryRun.objects.exists())
        runs.start('someone')
        page = self.client.post('/discovery/', {'action': 'read_devices', 'routers': [str(self.mt1.uuid)]}, follow=True)
        self.assertContains(page, 'A discovery is running')
        self.assertEqual(DiscoveryRun.objects.count(), 1)

    def test_a_devices_run_cut_short_by_a_restart_ends(self):
        from django.utils import timezone
        from . import runs
        from .models import DiscoveryRun
        run = DiscoveryRun.objects.create(kind='devices', state='reading', until=timezone.now(), targets=[])
        DiscoveryRun.objects.filter(pk=run.pk).update(created=timezone.now() - timezone.timedelta(hours=1))
        run = runs.advance(DiscoveryRun.objects.get(pk=run.pk))
        self.assertEqual(run.state, 'failed')

    def test_the_eye_hides_in_the_background(self):
        device = DiscoveredDevice.objects.create(key='addr:198.51.100.5|x', identity='x', address='198.51.100.5',
                                                 last_seen=__import__('django.utils.timezone', fromlist=['now']).now())
        answer = self.client.post('/router/discovered/', {'action': 'dismiss', 'uuid': str(device.uuid)},
                                  HTTP_ACCEPT='application/json')
        self.assertEqual(answer.json(), {'ok': True, 'dismissed': True})
        device.refresh_from_db()
        self.assertTrue(device.dismissed)

    def test_inventory_no_longer_reads_every_neighbour_table(self):
        source = (pathlib.Path(__file__).resolve().parent.parent / 'routerlib/router_functions.py').read_text()
        self.assertNotIn('read_neighbours(', source)


class PagesTests(TestCase):
    """Device Discovery holds the actions and the run; the Discovered tab
    only the devices - an unreachable one explained, with Check again."""

    def setUp(self):
        self.client.force_login(make_user(30))

    def test_the_menu_the_actions_page_and_the_list_without_actions(self):
        page = self.client.get('/discovery/').content.decode()
        self.assertIn('href="/discovery/"', page, 'Device Discovery in the menu')
        self.assertIn('Device Discovery', page)
        self.assertIn('Adjacent devices', page)
        self.assertIn('From managed devices', page)
        listing = self.client.get('/router/discovered/').content.decode()
        for action in ('Discover adjacent devices', 'Read their neighbours', 'Discovery listener'):
            self.assertNotIn(action, listing)

    def test_an_unreachable_device_is_explained_with_check_again_and_hidden_ones_shown_centrally(self):
        from django.utils import timezone
        DiscoveredDevice.objects.create(key='addr:198.51.100.7|far', identity='far', address='198.51.100.7',
                                        last_seen=timezone.now(), reachable=False,
                                        reachable_detail='no answer on SSH (port 22)')
        DiscoveredDevice.objects.create(key='addr:198.51.100.8|gone', identity='gone', address='198.51.100.8',
                                        last_seen=timezone.now(), dismissed=True)
        listing = self.client.get('/router/discovered/').content.decode()
        self.assertIn('class="rf-unreachable"', listing)
        self.assertIn("Not reachable from RouterFleet's host</b> - no answer on SSH (port 22).", listing)
        self.assertIn('Check again', listing)
        self.assertIn('Show 1 hidden device', listing)
        shown = self.client.get('/router/discovered/?dismissed=1').content.decode()
        self.assertIn('Show again', shown)


class ListenerBoundsTests(TestCase):
    """What anyone on the LAN can send is bounded: devices remembered, field
    lengths, devices imported; loopback is never an address to probe."""

    def test_the_listener_remembers_a_bounded_number_and_length(self):
        module = listener()
        module.seen.clear()
        module.MOST_DEVICES = 5
        for number in range(20):
            module.remember({'mac': f'00:00:5e:00:53:{number:02x}', 'identity': 'x' * 5000}, 'lldp')
        self.assertEqual(len(module.seen), 5)
        self.assertTrue(all(len(entry['identity']) <= module.LONGEST_FIELD for entry in module.seen.values()))
        module.seen.clear()

    def test_loopback_is_not_an_address(self):
        from .reach import is_ip
        self.assertEqual(listener().usable('127.0.0.1'), '')
        self.assertFalse(is_ip('127.0.0.1'))
        self.assertFalse(is_ip('::1'))
        self.assertTrue(is_ip('192.0.2.5'))

    def test_an_oversized_listener_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'seen.json'
            path.write_bytes(b' ' * (8 * 1024 * 1024 + 1))
            self.assertEqual(import_listener_file(str(path)), 0)


class FoundFirstTests(TestCase):
    """What a discovery found comes before its timeline, one row per known
    device however many protocols heard it."""

    def test_found_above_the_timeline_and_merged(self):
        from django.utils import timezone
        from . import runs
        from .models import DiscoveryRun
        self.client.force_login(make_user(30))
        gw = make_router(name='gw', address='192.0.2.254')
        a = DiscoveredDevice.objects.create(key='mac:00:00:5e:00:53:01', identity='gw', address='192.0.2.254',
                                            protocols='lldp', seen_by='RouterFleet LAN', last_seen=timezone.now())
        b = DiscoveredDevice.objects.create(key='addr:192.0.2.254|gw', identity='gw', address='192.0.2.254',
                                            protocols='cdp,mndp', seen_by='RouterFleet LAN', last_seen=timezone.now())
        run = runs.start('admin')
        DiscoveryRun.objects.filter(pk=run.pk).update(state='done', heard=[a.key, b.key], finished_at=timezone.now())
        page = self.client.get('/discovery/')
        found = page.context['live']['found']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['router'], gw)
        self.assertEqual(found[0]['protocols'], 'CDP, LLDP, MNDP')
        html = page.content.decode()
        self.assertLess(html.index('Found in the last discovery'), html.index('rf-run-timeline'))



class WizardGroupsTests(TestCase):
    """The wizard puts the new device in the groups ticked, and creates a new
    group on the way - an existing name, whatever its case, is that group."""

    def setUp(self):
        from django.utils import timezone
        from router_manager.models import RouterGroup
        self.client.force_login(make_user(50))
        self.device = DiscoveredDevice.objects.create(key='addr:192.0.2.77|edge', identity='edge', address='192.0.2.77',
                                                      platform='MikroTik', last_seen=timezone.now(), reachable=True)
        self.office = RouterGroup.objects.create(name='Office', default_group=True)
        self.lab = RouterGroup.objects.create(name='Lab')

    def post(self, **extra):
        data = {'name': 'edge', 'router_type': 'routeros', 'username': 'admin', 'password': 'pw', 'port': 22,
                'monitoring': 'on', 'enabled': 'on', 'keep_unchanged': 'profile', 'telnet_port': 23}
        data.update(extra)
        return self.client.post(f'/router/discovered/add/?uuid={self.device.uuid}', data)

    def test_the_default_group_is_ticked_to_begin_with(self):
        page = self.client.get(f'/router/discovered/add/?uuid={self.device.uuid}')
        self.assertEqual(page.context['chosen_groups'], {str(self.office.uuid)})
        self.assertContains(page, '4 · Groups')

    def test_ticked_groups_and_a_new_one(self):
        from router_manager.models import Router, RouterGroup
        self.post(groups=[str(self.lab.uuid)], new_group='Branch offices')
        router = Router.objects.get(name='edge')
        self.assertEqual(sorted(g.name for g in router.routergroup_set.all()), ['Branch offices', 'Lab'])
        self.assertTrue(RouterGroup.objects.filter(name='Branch offices').exists())

    def test_an_existing_name_is_that_group(self):
        from router_manager.models import Router, RouterGroup
        self.post(new_group='lab')
        self.assertEqual(RouterGroup.objects.filter(name__iexact='lab').count(), 1)
        self.assertIn(self.lab, Router.objects.get(name='edge').routergroup_set.all())
