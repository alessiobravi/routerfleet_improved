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
                         ('AP02-INTSEC-CDC', '192.168.88.12', '48:a9:8a:01:02:03', 'cAP ax', '7.16.2 (stable)'))
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
        self.assertEqual((lldpd['identity'], lldpd['address']), ('AP02-INTSEC-CDC', '192.168.88.12'))

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
        self.assertIn('AP02-INTSEC-CDC', page)
        self.assertIn('Already in RouterFleet', page)
        device = DiscoveredDevice.objects.get(identity='AP02-INTSEC-CDC')
        form = self.client.get(f'/router/manage/?discovered={device.uuid}').content.decode()
        self.assertIn('value="AP02-INTSEC-CDC"', form)
        self.assertIn('value="192.168.88.12"', form)
        self.assertRegex(form, r'<option value="routeros"[^>]*selected')

    def test_dismissed_and_restored(self):
        self.client.force_login(make_user(30))
        device = DiscoveredDevice.objects.get(identity='AP02-INTSEC-CDC')
        self.client.post('/router/discovered/', {'action': 'dismiss', 'uuid': str(device.uuid)})
        self.assertNotIn('AP02-INTSEC-CDC', self.client.get('/router/discovered/').content.decode().split('Already in RouterFleet')[0])
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
        self.assertEqual(DiscoveredDevice.objects.get(identity='AP02-INTSEC-CDC').seen_by_router, router)


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
        page = self.client.get('/router/discovered/').content.decode()
        self.assertIn('Not running on this host', page)
        self.assertRegex(page, r'rf-set-action w-100 rf-set-disabled"\s+disabled')
        self.client.post('/router/discovered/', {'action': 'listen'})
        self.assertFalse((pathlib.Path(self.folder.name) / 'request.json').exists())

    def test_running_the_button_opens_a_window(self):
        import time
        self.heartbeat()
        self.assertIn('Ready, dormant', self.client.get('/router/discovered/').content.decode())
        self.client.post('/router/discovered/', {'action': 'listen'})
        until = json.loads((pathlib.Path(self.folder.name) / 'request.json').read_text())['until']
        self.assertAlmostEqual(until - time.time(), 180, delta=5)
        self.heartbeat(listening_for=120)
        page = self.client.get('/router/discovered/').content.decode()
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
                         ('bng-nw6moj.juniper.net', '10.204.39.232', '88:e0:f3:1f:14:e0', 'ge-0/0/8'))

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
        self.device = DiscoveredDevice.objects.get(identity='AP02-INTSEC-CDC')

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
        self.assertIn('value="AP02-INTSEC-CDC"', page)
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
        self.assertNotIn('Add to hosts', page)
        self.assertIn('Reachable from RouterFleet', page)
