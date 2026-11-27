"""Device types and SSH keys, the less travelled paths: helper edge cases, the
editor's and wizard's refusals, "try on a device" with a stored type, and
keys in other formats. No connection is ever opened."""
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import SimpleTestCase, TestCase

from fleet_commander import system_commands

from router_manager import device_type_views, device_types, sshkeys
from router_manager.models import DeviceType, Router
from routerfleet.testing import FakeSSHClient, make_router, make_user

TEST_ONLY_PASSPHRASE = 'test-only-passphrase'


class LookupAndChoiceTests(TestCase):
    """get() without a slug, a preset coming back under a taken name, and
    the choices offered."""

    def test_no_slug_no_type(self):
        self.assertIsNone(device_types.get(''))
        self.assertIsNone(device_types.get(None))
        self.assertEqual(device_types.name_of(''), '')
        self.assertEqual(device_types.name_of('nonexistent-type'), 'nonexistent-type')

    def test_a_missing_preset_under_a_name_now_taken(self):
        original = DeviceType.objects.get(slug='openwrt')
        name = original.name
        original.delete()
        DeviceType.objects.create(slug='someone-else', name=name)
        restored = device_types.get('openwrt')
        self.assertEqual(restored.name, f'{name} (openwrt)')

    def test_choices(self):
        DeviceType.objects.filter(slug='openwrt').update(enabled=False)
        slugs = [slug for slug, _ in device_types.choices()]
        self.assertNotIn('openwrt', slugs)
        self.assertIn('openwrt', [slug for slug, _ in device_types.choices(include='openwrt')])
        self.assertNotIn('monitoring', [slug for slug, _ in device_types.choices(include_monitoring=False)])
        self.assertNotIn('monitoring', [slug for slug, _ in device_types.choices(managed_only=True)])

    def test_reset_cache_only_within_a_request(self):
        device_types.clear_cache()
        device_types.reset_cache()
        self.assertIsNone(device_types._local.cache)
        device_types.start_request_cache()
        try:
            first = device_types.get('routeros')
            self.assertIs(device_types.get('routeros'), first, 'kept for the request')
            device_types.reset_cache()
            self.assertEqual(device_types._local.cache, {})
        finally:
            device_types.clear_cache()


class HelperEdgeTests(SimpleTestCase):
    """Patterns that do not compile, sources of no known kind, definitions of
    the wrong shape."""

    def test_connection_test_results(self):
        broken = DeviceType(name='Broken', test_pattern='([')
        ok, message = device_types.test_result(broken, 'anything')
        self.assertFalse(ok)
        self.assertIn('invalid test pattern', message)
        self.assertEqual(device_types.test_result(DeviceType(name='Plain', test_pattern=''), ''),
                         (True, 'Success: connected (Plain has no recognition pattern)'))
        self.assertEqual(device_types.test_result(DeviceType(name='Acme', test_pattern='Acme'), 'Acme OS'),
                         (True, 'Success: Acme device confirmed'))
        self.assertEqual(device_types.test_result(DeviceType(name='Acme', test_pattern='Acme'), None),
                         (False, 'Device is not Acme'))

    def test_sources(self):
        self.assertEqual(device_types.source_value('re:([', {}, 'text'), '')
        self.assertEqual(device_types.source_value('re:nothing', {}, 'text'), '')
        self.assertEqual(device_types.source_value('re:te.t', {}, 'a text'), 'text')
        self.assertEqual(device_types.source_value('other:x', {}, 'text'), '')
        self.assertEqual(device_types.source_value(None, {}, ''), '')

    def test_inventory_definitions_of_the_wrong_shape(self):
        self.assertEqual(device_types.validate_inventory_fields(['x']),
                         ['The inventory fields must be a mapping of field to sources.'])
        problems = device_types.validate_inventory_fields({'os_version': 're:x', 'model_name': ['re:([', 'what']})
        self.assertEqual(len(problems), 3)
        self.assertIn('the sources must be a list', problems[0])
        self.assertIn('invalid pattern', problems[1])
        self.assertIn('neither key:NAME nor re:PATTERN', problems[2])

    def test_an_invalid_ignore_pattern_drops_nothing(self):
        text = 'a\n# generated now\nb'
        self.assertEqual(device_types.clean_text(DeviceType(text_ignore_pattern='(['), text), text)
        self.assertEqual(device_types.clean_text(None, text), text)

    def test_named_lines_skip_empty_ones_and_number_repeats(self):
        self.assertEqual(device_types.parse_named_lines('\n  \nshow run\nshow start\n'),
                         [('configuration', 'show run'), ('configuration-2', 'show start')])

    def test_json(self):
        self.assertEqual(device_types.to_json({'b': 1, 'a': [2]}), '{\n  "a": [\n    2\n  ],\n  "b": 1\n}')


class ManagementLinkTests(TestCase):
    """A device without a type or a username has no links; IPv6 is bracketed."""

    def test_links(self):
        router = make_router(router_type='routeros', username='', address='192.0.2.60')
        self.assertEqual(device_types.links_for(router), [])
        unknown = Router(router_type='no-such-type', username='admin', address='192.0.2.61')
        self.assertEqual(device_types.links_for(unknown), [])
        DeviceType.objects.filter(slug='routeros').update(management_links='Web: https://{address}:{port}/?u={username}')
        router = Router(router_type='routeros', username='a b', address='2001:db8::1', port=22)
        self.assertEqual(device_types.links_for(router), [('Web', 'https://[2001:db8::1]:22/?u=a%20b')])


class DescribeFailureTests(SimpleTestCase):
    """The shell not behaving as the type says has its own sentence."""

    def test_session_error(self):
        from routerlib.device_session import SessionError
        self.assertIn('did not behave as the device type says',
                      device_type_views.describe_failure(SessionError('detail')))


class EditorTests(TestCase):
    """The editor's and wizard's refusals, and the monitoring type kept on."""

    def setUp(self):
        self.client.force_login(make_user(50))

    def wizard(self, **overrides):
        data = {'name': 'Acme OS', 'slug': 'acme-os', 'session_mode': 'exec', 'text_extension': 'txt',
                'vendor_version_field': 'os_version', 'command_connection_test': 'show version',
                'command_inventory': 'show version'}
        data.update(overrides)
        return self.client.post('/system/device-types/new/', data)

    def problems(self, response):
        return [str(m) for m in response.context['messages']]

    def test_refusals(self):
        routeros_name = DeviceType.objects.get(slug='routeros').name
        cases = (({'name': ''}, 'Enter a name.'),
                 ({'name': routeros_name}, 'A device type with this name exists.'),
                 ({'binary_extension': 'tar gz!'}, 'Binary extension: letters, digits and dots'),
                 ({'vendor_target': 'json:path'}, 'The target is a source like the inventory'),
                 ({'firmware_upload_path': '/tmp/{{ version }}'}, 'Upload path: only {{ firmware_name }}'),
                 ({'option_terse': 'terse; reboot'}, 'Export options: words'),
                 ({'legacy_before_version': 'seven'}, 'Older versions: a version number'))
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                problems = self.problems(self.wizard(**overrides))
                self.assertTrue(any(expected in p for p in problems), problems)
                self.assertFalse(DeviceType.objects.filter(slug='acme-os').exists())

    def test_defaults_filled_in(self):
        self.assertEqual(self.client.get('/system/device-types/new/').status_code, 200)
        self.wizard(text_extension='', vendor_version_field='bogus', vendor_target='key:target',
                    firmware_upload_path='/tmp/{{ firmware_name }}')
        created = DeviceType.objects.get(slug='acme-os')
        self.assertEqual((created.text_extension, created.vendor_version_field, created.vendor_target),
                         ('txt', 'os_version', 'key:target'))
        self.assertEqual(created.firmware_upload_path, '/tmp/{{ firmware_name }}')

    def test_a_system_command_without_its_command_row(self):
        from fleet_commander.models import Command, CommandVariant
        Command.objects.filter(system_key='neighbors').update(system_key=None)
        self.wizard()
        keys = set(CommandVariant.objects.filter(router_type='acme-os').values_list('command__system_key', flat=True))
        self.assertNotIn('neighbors', keys)
        self.assertIn('inventory', keys)

    def test_only_administrators_edit(self):
        routeros = DeviceType.objects.get(slug='routeros')
        self.client.force_login(make_user(40))
        self.assertContains(self.client.get(f'/system/device-types/edit/?uuid={routeros.uuid}'), 'Access Denied')

    def test_delete_needs_the_confirmation(self):
        huawei = device_types.get('huawei-vrp')
        response = self.client.post(f'/system/device-types/edit/?uuid={huawei.uuid}', {'action': 'delete',
                                                                                     'confirmation': 'nope'})
        self.assertRedirects(response, f'/system/device-types/edit/?uuid={huawei.uuid}', fetch_redirect_response=False)
        self.assertTrue(DeviceType.objects.filter(slug='huawei-vrp').exists())

    def test_the_monitoring_type_stays_on(self):
        monitoring = device_types.get('monitoring')
        response = self.client.post(f'/system/device-types/edit/?uuid={monitoring.uuid}',
                                    {'name': monitoring.name, 'session_mode': 'exec', 'text_extension': 'txt',
                                     'vendor_version_field': 'os_version'})
        self.assertEqual(response.status_code, 302)
        monitoring.refresh_from_db()
        self.assertTrue(monitoring.enabled, 'posted without "enabled", still on')


class TryTests(TestCase):
    """Try on a device: a definition with a mistake, a port that is no
    number, and a stored type's own commands."""

    def setUp(self):
        self.client.force_login(make_user(50))

    def test_a_mistake_in_the_definition(self):
        result = self.client.post('/system/device-types/try/', {'name': 'X', 'session_mode': 'exec', 'test_pattern': '(['}).json()
        self.assertFalse(result['ok'])
        self.assertIn('Recognition pattern', result['message'])

    def test_a_port_that_is_no_number(self):
        routeros = DeviceType.objects.get(slug='routeros')
        result = self.client.post('/system/device-types/try/', {
            'uuid': str(routeros.uuid), 'name': routeros.name, 'session_mode': 'exec', 'text_extension': 'rsc',
            'try_address': '192.0.2.70', 'try_port': 'ssh'}).json()
        self.assertEqual(result, {'ok': False, 'message': 'Enter the address and port of a device to try on.'})

    def test_a_stored_type_uses_its_own_commands(self):
        routeros = DeviceType.objects.get(slug='routeros')
        client = FakeSSHClient({'/system resource print': 'platform: MikroTik\nversion: 7.16 (stable)\n'})
        data = {'uuid': str(routeros.uuid), 'name': routeros.name, 'session_mode': 'exec', 'text_extension': 'rsc',
                'test_pattern': routeros.test_pattern, 'try_address': '192.0.2.71', 'try_port': '22',
                'try_username': 'admin', 'try_password': 'test-only-password'}
        with mock.patch('console.network.check', return_value='192.0.2.71'), \
                mock.patch('routerlib.device_session.connect_to_ssh', return_value=client), \
                mock.patch('fleet_commander.system_commands.lines', wraps=system_commands.lines) as lines:
            result = self.client.post('/system/device-types/try/', data).json()
        self.assertTrue(lines.called)
        self.assertTrue(all(call.args[1] == 'routeros' for call in lines.call_args_list))
        self.assertEqual(result['steps'][0]['title'], 'Connection test')
        self.assertTrue(client.closed)
        self.assertTrue(client.commands)


class DescribeKeyTests(TestCase):
    """Public key lines of other algorithms, and an RSA line that does not decode."""

    def test_other_algorithms(self):
        kind, label, fingerprint = sshkeys.describe('ssh-dss AAAAB3NzaC1kc3M= comment')
        self.assertEqual((kind, label), ('other', 'ssh-dss'))
        self.assertTrue(fingerprint.startswith('SHA256:'))

    def test_an_rsa_line_that_does_not_decode(self):
        kind, label, _ = sshkeys.describe('ssh-rsa AAAA')
        self.assertEqual((kind, label), ('rsa', 'RSA'))


class ReprotectTests(SimpleTestCase):
    """A stored PEM key is opened as PEM; a key that opens as neither is refused."""

    def pem(self, passphrase=None):
        key = ec.generate_private_key(ec.SECP256R1())
        encryption = (serialization.BestAvailableEncryption(passphrase.encode()) if passphrase
                      else serialization.NoEncryption())
        return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                 encryption).decode()

    def test_a_pem_key_is_written_again_in_openssh_format(self):
        result = sshkeys.reprotected(self.pem(TEST_ONLY_PASSPHRASE), TEST_ONLY_PASSPHRASE, '')
        self.assertIn('BEGIN OPENSSH PRIVATE KEY', result)
        serialization.load_ssh_private_key(result.encode(), password=None)

    def test_a_wrong_passphrase_or_no_key(self):
        for key, passphrase in ((self.pem(TEST_ONLY_PASSPHRASE), 'wrong-test-passphrase'), ('not a key', None)):
            with self.subTest(key=key[:20]), self.assertRaises(sshkeys.KeyProblem):
                sshkeys.reprotected(key, passphrase, 'new-test-passphrase')
