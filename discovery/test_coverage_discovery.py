"""Discovery's less travelled paths: records the store skips or corrects, a
listener file that can not be used, the reachability probe's answers, and
the pages' refusals and rechecks. No socket is ever opened."""
import json
import pathlib
import tempfile
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from routerfleet.testing import make_router, make_user

from . import reach, store, views
from .models import DiscoveredDevice


def device(identity='d1', address='192.0.2.40', **fields):
    return DiscoveredDevice.objects.create(key=f'addr:{address}|{identity}', identity=identity, address=address,
                                           last_seen=timezone.now(), **fields)


class StoreEdgeTests(TestCase):
    """Empty records are skipped, a bad time is "now", an older sighting does
    not move last_seen back."""

    def test_a_record_with_nothing_to_key_on_is_skipped(self):
        self.assertEqual(store.store([{}, {'mac': '', 'address': '', 'identity': ''}]), 0)
        self.assertFalse(DiscoveredDevice.objects.exists())

    def test_an_unreadable_last_seen_is_now(self):
        before = timezone.now()
        for bad in ('soon', 1e20, [1]):
            with self.subTest(bad=bad):
                store.store([{'address': '192.0.2.41', 'identity': 'x', 'last_seen': bad}])
                self.assertGreaterEqual(DiscoveredDevice.objects.get().last_seen, before)

    def test_an_older_sighting_keeps_the_newer_time(self):
        store.store([{'address': '192.0.2.42', 'identity': 'y'}])
        newest = DiscoveredDevice.objects.get().last_seen
        store.store([{'address': '192.0.2.42', 'identity': 'y', 'last_seen': 1000, 'platform': 'Acme'}])
        row = DiscoveredDevice.objects.get()
        self.assertEqual(row.last_seen, newest)
        self.assertEqual(row.platform, 'Acme', 'the details are updated all the same')


class ReadNeighboursFailureTests(TestCase):
    """A neighbour command that fails is logged; with no output nothing is stored."""

    def test_every_command_failing_finds_none(self):
        router = make_router(name='mt-fail')
        session = mock.Mock()
        session.exec_command.side_effect = OSError('no lldpd')
        with mock.patch('fleet_commander.system_commands.lines', return_value=['lldpcli show neighbors']), \
                self.assertLogs('discovery.store', level='INFO') as logs:
            self.assertEqual(store.read_neighbours(session, router), 0)
        self.assertIn('Neighbour command on mt-fail failed', logs.output[0])

    def test_no_command_finds_none(self):
        router = make_router(name='mt-none')
        with mock.patch('fleet_commander.system_commands.lines', return_value=[]):
            self.assertEqual(store.read_neighbours(mock.Mock(), router), 0)


class ListenerFileTests(TestCase):
    """A listener file that is not JSON, or holds what is not a device."""

    def setUp(self):
        self.folder = pathlib.Path(tempfile.mkdtemp())
        self.path = self.folder / 'seen.json'

    def test_not_json(self):
        self.path.write_text('{broken')
        with self.assertLogs('discovery.store', level='WARNING'):
            self.assertEqual(store.import_listener_file(str(self.path)), 0)

    def test_entries_that_are_not_devices_are_skipped(self):
        self.path.write_text(json.dumps({'devices': {'a': 'text', 'b': {'address': '192.0.2.43', 'identity': 'z',
                                                                        'protocols': ['lldp'], 'nested': {'x': 1}}}}))
        self.assertEqual(store.import_listener_file(str(self.path)), 1)
        self.assertEqual(DiscoveredDevice.objects.get().protocols, 'lldp')

    def test_a_list_instead_of_a_mapping(self):
        self.path.write_text(json.dumps(['x']))
        self.assertEqual(store.import_listener_file(str(self.path)), 0)

    def test_asking_with_a_folder_that_is_not_there(self):
        with override_settings(DISCOVERY_FILE=str(self.folder / 'missing' / 'seen.json')):
            self.assertFalse(store.ask_listener())


class ProbeTests(TestCase):
    """What the probe says for each way a connection fails; what is no address."""

    def test_answers(self):
        cases = ((TimeoutError(), False, 'no answer on SSH (port 22)'),
                 (OSError(113, 'No route to host'), False, 'not reachable (No route to host)'),
                 (OSError('odd'), False, 'not reachable (OSError)'))
        for error, ok, detail in cases:
            with self.subTest(error=error), mock.patch('discovery.reach.socket.create_connection', side_effect=error):
                self.assertEqual(reach._probe('192.0.2.44'), (ok, detail))

    def test_what_is_not_an_address(self):
        for value in ('', None, 'router.example', '127.0.0.1', 'fe80::1', '224.0.0.1', '0.0.0.0'):
            with self.subTest(value=value):
                self.assertFalse(reach.is_ip(value))

    def test_nothing_to_check(self):
        self.assertEqual(reach.check([DiscoveredDevice(address='name-only')]), 0)


class ClassifyHelperTests(TestCase):
    """A device known by name, and prefill without a guessed type."""

    def test_known_by_its_name(self):
        router = make_router(name='Core-1', address='192.0.2.50')
        found = DiscoveredDevice(identity='core-1', address='192.0.2.99')
        self.assertEqual(views.known_router(found, [router]), router)
        self.assertIsNone(views.known_router(DiscoveredDevice(identity='', address=''), [router]))

    def test_prefill_without_a_type(self):
        row = device(identity='', address='192.0.2.45', platform='Unknown box')
        self.assertEqual(views.prefill(row.uuid), {'name': '192.0.2.45', 'address': '192.0.2.45'})
        self.assertIsNone(views.prefill(None))

    def test_only_types_with_a_neighbours_command(self):
        make_router(name='a-mt', router_type='routeros', address='192.0.2.51')
        make_router(name='b-ow', router_type='openwrt', address='192.0.2.52')
        make_router(name='c-mt', router_type='routeros', address='192.0.2.53')
        with mock.patch('fleet_commander.system_commands.payload_of',
                        side_effect=lambda key, kind: '/ip neighbor print' if kind == 'routeros' else '  ') as payload:
            self.assertEqual([r.name for r in views.neighbour_capable()], ['a-mt', 'c-mt'])
        self.assertEqual(payload.call_count, 2, 'asked once per type')


class DiscoveryPageTests(TestCase):
    """The discovery page below its level, a listener folder that can not be
    written, and an action the start does not know."""

    def setUp(self):
        self.folder = pathlib.Path(tempfile.mkdtemp())
        override = override_settings(DISCOVERY_FILE=str(self.folder / 'seen.json'))
        override.enable()
        self.addCleanup(override.disable)

    def test_below_host_manager(self):
        self.client.force_login(make_user(20))
        self.assertContains(self.client.get('/discovery/'), 'Access Denied')

    def test_the_shared_folder_can_not_be_written(self):
        from .models import DiscoveryRun
        self.client.force_login(make_user(30))
        with mock.patch('discovery.views.listener_state', return_value={'running': True, 'listening_left': 0}), \
                mock.patch('discovery.views.ask_listener', return_value=False):
            page = self.client.post('/discovery/', {'action': 'listen'}, follow=True)
        self.assertContains(page, 'can not be written')
        self.assertFalse(DiscoveryRun.objects.exists())

    def test_an_unknown_action_starts_nothing(self):
        request = RequestFactory().post('/discovery/', {'action': 'other'})
        response = views._start_discovery(request)
        self.assertEqual((response.status_code, response.url), (302, '/discovery/'))


class DiscoveredPageTests(TestCase):
    """Check again, the reachability check on opening the page, and a wizard
    form with mistakes."""

    def setUp(self):
        self.client.force_login(make_user(50))

    def test_check_again(self):
        row = device()
        with mock.patch('discovery.reach.check') as check:
            response = self.client.post('/router/discovered/?view=hidden', {'action': 'recheck', 'uuid': str(row.uuid)})
        self.assertRedirects(response, f'/router/discovered/?view=hidden&device={row.uuid}', fetch_redirect_response=False)
        self.assertEqual(check.call_args.args[0], [row])

    def test_the_page_checks_what_was_not_checked_lately(self):
        device()
        with override_settings(DISCOVERY_CHECK_ON_PAGE=True), mock.patch('discovery.reach.check_stale') as stale:
            self.assertEqual(self.client.get('/router/discovered/').status_code, 200)
        stale.assert_called_once_with()

    def test_a_wizard_form_with_mistakes_is_shown_again(self):
        row = device(reachable=True)
        url = f'/router/discovered/add/?uuid={row.uuid}'
        with mock.patch('discovery.reach.is_ip', return_value=True):
            page = self.client.post(url, {'name': '', 'router_type': 'routeros', 'groups': ['not-a-group']})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['chosen_groups'], {'not-a-group'})
        self.assertTrue(page.context['form'].errors)
        from router_manager.models import Router
        self.assertFalse(Router.objects.exists())
