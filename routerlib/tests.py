import io
import json
from unittest import mock

from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from routerfleet.testing import edit_type, FakeSSHClient, fake_ssh, make_information, make_router
from routerlib import encryption, router_functions
from routerlib.decorators import cron_endpoint
from routerlib.functions import (gen_backup_name, get_router_backup_file_extension,
                                 load_private_key_from_string, test_authentication)
from router_manager.models import Router, RouterInformation


class EncryptionTests(SimpleTestCase):
    def test_round_trip(self):
        token = encryption.encrypt_value('s3cret')
        self.assertNotEqual(token, 's3cret')
        self.assertTrue(token.startswith('gAAAAA'))
        self.assertEqual(encryption.decrypt_value(token), 's3cret')

    def test_empty_values_pass_through(self):
        for value in (None, ''):
            self.assertEqual(encryption.encrypt_value(value), value)
            self.assertEqual(encryption.decrypt_value(value), value)

    def test_encrypting_twice_does_not_double_encrypt(self):
        # The data migrations depend on this: a value assigned already encrypted
        # is encrypted again by the field on save, and must come out unchanged.
        token = encryption.encrypt_value('s3cret')
        self.assertEqual(encryption.encrypt_value(token), token)

    def test_non_string_is_stringified(self):
        self.assertEqual(encryption.decrypt_value(encryption.encrypt_value(1234)), '1234')

    def test_undecryptable_value_is_returned_and_warned_about(self):
        with self.assertLogs('routerlib.encryption', level='WARNING') as logs:
            self.assertEqual(encryption.decrypt_value('plain text'), 'plain text')
        message = ' '.join(logs.output)
        # Both causes are named, not only the alarming one
        self.assertIn('not encrypted yet', message)
        self.assertIn('different key', message)

    def test_key_falls_back_to_secret_key_outside_docker(self):
        with mock.patch.object(encryption, '_fernet', None), \
                self.settings(ENCRYPTION_KEY=None, SECRET_KEY='abc'):
            first = encryption.get_fernet()
        with mock.patch.object(encryption, '_fernet', None), \
                self.settings(ENCRYPTION_KEY=None, SECRET_KEY='abc'):
            second = encryption.get_fernet()
        token = first.encrypt(b'x')
        self.assertEqual(second.decrypt(token), b'x')

    def test_configured_key_is_used(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        with mock.patch.object(encryption, '_fernet', None), self.settings(ENCRYPTION_KEY=key):
            token = encryption.encrypt_value('v')
            self.assertEqual(Fernet(key.encode()).decrypt(token.encode()), b'v')


class MaskPasswordsTests(SimpleTestCase):
    def test_password_column_is_masked(self):
        raw = 'name,password,address\na,secret1,1.1.1.1\nb,,2.2.2.2\n'
        masked = encryption.mask_passwords_in_csv(raw)
        self.assertNotIn('secret1', masked)
        self.assertIn('a,********,1.1.1.1', masked)
        # An empty password stays empty rather than looking set
        self.assertIn('b,,2.2.2.2', masked)

    def test_csv_without_password_column_is_untouched(self):
        raw = 'name,address\na,1.1.1.1\n'
        self.assertEqual(encryption.mask_passwords_in_csv(raw), raw)

    def test_empty_input_is_untouched(self):
        self.assertEqual(encryption.mask_passwords_in_csv(''), '')


class EncryptedFieldTests(TestCase):
    def raw_password(self, router):
        with connection.cursor() as cursor:
            cursor.execute('SELECT password FROM router_manager_router WHERE id = %s', [router.id])
            return cursor.fetchone()[0]

    def test_save_encrypts_at_rest_and_reads_plaintext(self):
        router = make_router(password='hunter2')
        self.assertTrue(self.raw_password(router).startswith('gAAAAA'))
        self.assertEqual(Router.objects.get(pk=router.pk).password, 'hunter2')

    def test_queryset_update_also_encrypts(self):
        router = make_router(password='old')
        Router.objects.filter(pk=router.pk).update(password='new-one')
        self.assertNotIn('new-one', self.raw_password(router))
        self.assertEqual(Router.objects.get(pk=router.pk).password, 'new-one')

    def test_null_stays_null(self):
        router = make_router(password=None)
        self.assertIsNone(self.raw_password(router))
        self.assertIsNone(Router.objects.get(pk=router.pk).password)


class CronEndpointTests(SimpleTestCase):
    def setUp(self):
        self.view = cron_endpoint(lambda request: HttpResponse('ran'))
        self.factory = RequestFactory()

    def test_direct_call_runs(self):
        response = self.view(self.factory.get('/cron/x/'))
        self.assertEqual(response.content, b'ran')

    def test_request_through_a_proxy_is_refused_as_not_found(self):
        response = self.view(self.factory.get('/cron/x/', HTTP_X_REAL_IP='203.0.113.9'))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'ran', response.content)

    def test_wraps_keeps_the_view_name(self):
        def my_view(request):
            return HttpResponse()
        self.assertEqual(cron_endpoint(my_view).__name__, 'my_view')


class DeviceTypeTests(TestCase):
    def test_file_extensions_come_from_the_type(self):
        self.assertEqual(get_router_backup_file_extension('routeros'), {'text': 'rsc', 'binary': None})
        self.assertEqual(get_router_backup_file_extension('openwrt'), {'text': 'txt', 'binary': 'tar.gz'})
        self.assertEqual(get_router_backup_file_extension('ubiquiti-airos'), {'text': 'cfg', 'binary': None})
        self.assertEqual(get_router_backup_file_extension('cisco-ios'), {'text': 'cfg', 'binary': None})
        self.assertEqual(get_router_backup_file_extension('other'), {'text': 'txt', 'binary': 'bin'})

    def test_backup_name(self):
        backup = mock.Mock(id=7, schedule_type='daily',
                           created=timezone.datetime(2026, 3, 1, 4, 5))
        self.assertEqual(gen_backup_name(backup), 'routerfleet-backup-7-daily-2026-03-01_04-05')

    def test_unparseable_private_key(self):
        self.assertIsNone(load_private_key_from_string('not a key'))


class AuthenticationTests(TestCase):
    def check(self, router_type, responses):
        client = FakeSSHClient(responses)
        with fake_ssh('routerlib.functions', client):
            return test_authentication(router_type, '192.0.2.1', 22, 'admin', 'pw')

    def test_routeros_confirmed(self):
        ok, message = self.check('routeros', {'/system resource print': 'platform: MikroTik\n'})
        self.assertTrue(ok, message)

    def test_routeros_rejects_another_platform(self):
        ok, message = self.check('routeros', {'/system resource print': 'platform: Acme\n'})
        self.assertFalse(ok)
        self.assertIn('not Mikrotik (RouterOS)', message)

    def test_openwrt(self):
        self.assertTrue(self.check('openwrt', {'ubus call system board': '{"release": "OpenWrt"}'})[0])
        self.assertFalse(self.check('openwrt', {'ubus call system board': 'Debian'})[0])

    def test_airos_names_the_version_found(self):
        ok, message = self.check('ubiquiti-airos', {'cat /etc/version': 'XW.v6.3.11\n'})
        self.assertTrue(ok)
        self.assertIn('(XW.v6.3.11)', message)
        self.assertFalse(self.check('ubiquiti-airos', {'cat /etc/version': 'something'})[0])

    def test_the_pattern_of_an_edited_type_is_used(self):
        edit_type('openwrt', test_pattern=r'Debian (\d+)')
        ok, message = self.check('openwrt', {'ubus call system board': 'Debian 12'})
        self.assertTrue(ok)
        self.assertIn('(12)', message)

    def test_monitoring_type_is_not_reached(self):
        ok, message = test_authentication('monitoring', '192.0.2.1', 22, 'u', 'p')
        self.assertFalse(ok)
        self.assertIn('not reached over SSH', message)

    def test_connection_error_is_reported(self):
        with mock.patch('routerlib.device_session.connect_to_ssh', side_effect=OSError('refused')):
            ok, message = test_authentication('routeros', '192.0.2.1', 22, 'u', 'p')
        self.assertFalse(ok)
        self.assertIn('refused', message)


ROUTEROS_RESOURCE = '''
                   uptime: 1d
                  version: 7.16.2 (stable)
                    board-name: hAP ax3
        architecture-name: arm64
                      cpu: ARM64
'''
ROUTEROS_RB = '''
       routerboard: yes
             model: C53UiG+5HPaxD2HPaxD
     serial-number: HE1234567
  current-firmware: 7.16.2
'''
ROUTEROS_UPDATE = '''
            channel: stable
  installed-version: 7.16.2
     latest-version: 7.17
'''


@mock.patch('routerlib.router_functions.time.sleep')
class RouterOSInformationTests(TestCase):
    def setUp(self):
        self.router = make_router(router_type='routeros')
        self.info = make_information(self.router)

    def run_with(self, responses):
        client = FakeSSHClient(responses)
        with fake_ssh('routerlib.router_functions', client):
            return router_functions.get_router_information(self.info), client

    def test_fields_and_available_version(self, _sleep):
        (ok, error), client = self.run_with({
            '/system resource print': ROUTEROS_RESOURCE,
            '/system routerboard print': ROUTEROS_RB,
            '/system package update print': ROUTEROS_UPDATE,
        })
        self.assertTrue(ok, error)
        info = RouterInformation.objects.get(pk=self.info.pk)
        self.assertEqual(info.model_name, 'hAP ax3')
        self.assertEqual(info.os_version, '7.16.2 (stable)')
        self.assertEqual(info.architecture, 'arm64')
        self.assertEqual(info.model_version, 'C53UiG+5HPaxD2HPaxD')
        self.assertEqual(info.serial_number, 'HE1234567')
        self.assertEqual(info.firmware_version, '7.16.2')
        self.assertEqual(info.available_version, '7.17')
        self.assertTrue(info.update_available)
        self.assertTrue(info.success)
        self.assertIsNotNone(info.last_retrieval)
        self.assertIn('/system resource print', json.loads(info.json_data))
        self.assertIn('/system package update check-for-updates', client.commands)
        self.assertTrue(client.closed)

    def test_failed_update_check_does_not_fail_the_read(self, _sleep):
        with self.assertLogs('routerlib.router_functions', level='WARNING') as logs:
            (ok, _), _ = self.run_with({
                '/system resource print': ROUTEROS_RESOURCE,
                '/system routerboard print': ROUTEROS_RB,
                '/system package update check-for-updates': RuntimeError('no route to update server'),
            })
        self.assertIn('Inventory line failed', ' '.join(logs.output))
        self.assertTrue(ok)
        info = RouterInformation.objects.get(pk=self.info.pk)
        self.assertEqual(info.available_version, '')
        self.assertFalse(info.update_available)

    # RouterOS 6.49 long-term on x86 (a PC or a VM, not a RouterBOARD): the
    # output of a real device of that kind, names left out. x86 has no
    # routerboard menu, so that line answers with an error.
    ROUTEROS6_X86_RESOURCE = """
                   uptime: 2w5d13h43m42s
                  version: 6.49.21 (long-term)
               build-time: Sep/03/2026 11:45:39
              free-memory: 223.0MiB
             total-memory: 256.0MiB
                      cpu: Common
                cpu-count: 1
            cpu-frequency: 2699MHz
                 cpu-load: 5%
           free-hdd-space: 31.3GiB
          total-hdd-space: 31.4GiB
  write-sect-since-reboot: 28042
         write-sect-total: 28042
        architecture-name: x86
               board-name: x86
                 platform: MikroTik
"""
    ROUTEROS6_UPDATE = """            channel: stable
  installed-version: 6.49.21
     latest-version: 6.49.23
             status: New version is available
"""

    def test_routeros6_on_x86(self, _sleep):
        (ok, error), _ = self.run_with({
            '/system resource print': self.ROUTEROS6_X86_RESOURCE,
            '/system routerboard print': 'bad command name routerboard (line 1 column 9)\n',
            '/system package update check-for-updates': self.ROUTEROS6_UPDATE + '\n',
            '/system package update print': self.ROUTEROS6_UPDATE,
        })
        self.assertTrue(ok, error)
        info = RouterInformation.objects.get(pk=self.info.pk)
        self.assertEqual(info.os_version, '6.49.21 (long-term)')
        self.assertEqual(info.model_name, 'x86')
        self.assertEqual(info.architecture, 'x86')
        self.assertEqual(info.available_version, '6.49.23')
        self.assertTrue(info.update_available)
        self.assertEqual(info.update_status, 'New version is available')

    def test_no_output_is_a_failure(self, _sleep):
        (ok, error), _ = self.run_with({})
        self.assertFalse(ok)
        self.assertIn('Failed to retrieve', error)

    def offered(self, version, latest):
        (ok, error), _ = self.run_with({
            '/system resource print': ROUTEROS_RESOURCE.replace('7.16.2 (stable)', version),
            '/system routerboard print': ROUTEROS_RB,
            '/system package update print': f'latest-version: {latest}\n',
        })
        self.assertTrue(ok, error)
        info = RouterInformation.objects.get(pk=self.info.pk)
        return info.available_version, info.update_available

    def test_up_to_date(self, _sleep):
        self.assertEqual(self.offered('7.17 (stable)', '7.17'), ('7.17', False))

    def test_ahead_of_the_channel_is_not_an_update(self, _sleep):
        self.assertEqual(self.offered('7.18beta2', '7.17'), ('7.17', False))

    def status_client(self, answers):
        class Client:
            def exec_command(self, command):
                if command.endswith('update print'):
                    body = next(answers)
                elif command.endswith('resource print'):
                    body = ROUTEROS_RESOURCE
                else:
                    body = ''
                return None, io.BytesIO(body.encode()), io.BytesIO()
        return Client()

    def test_a_slow_update_server_is_waited_for(self, sleep):
        answers = iter(['installed-version: 7.16.2\nstatus: finding out latest version...\n',
                        'installed-version: 7.16.2\nstatus: finding out latest version...\n',
                        'installed-version: 7.16.2\nlatest-version: 7.17\nstatus: New version is available\n'])
        _, values = router_functions._run_inventory(self.status_client(answers), self.router)
        self.assertEqual(values['latest-version'], '7.17')
        self.assertEqual(sleep.call_count, 2)

    def test_the_wait_is_bounded(self, sleep):
        import itertools
        answers = itertools.repeat('status: finding out latest version...\n')
        _, values = router_functions._run_inventory(self.status_client(answers), self.router)
        self.assertNotIn('latest-version', values)
        self.assertEqual(sleep.call_count * router_functions.REPEAT_DELAY,
                         router_functions.REPEAT_TIMEOUT)

    def test_an_older_version_is_behind(self, _sleep):
        self.assertEqual(self.offered('7.17', '7.18'), ('7.18', True))

    def test_without_the_device_check_nothing_is_offered(self, _sleep):
        edit_type('routeros', check_device=False)
        self.assertEqual(self.offered('7.17', '7.18'), ('', False))


OS_RELEASE = '''NAME="OpenWrt"
VERSION="23.05.3"
VERSION_ID="23.05.3"
OPENWRT_BOARD="ramips/mt7621"
OPENWRT_DEVICE_MODEL="Xiaomi AX3000"
OPENWRT_RELEASE="OpenWrt 23.05.3 r23809"
'''


class OpenWrtInformationTests(TestCase):
    """The OpenWrt preset's vendor check: the published release list, and a
    release counts only when it ships images for the device's board."""

    def setUp(self):
        router_functions._vendor_cache.clear()
        router_functions._confirm_cache.clear()
        self.router = make_router(router_type='openwrt')
        self.info = make_information(self.router)
        patcher = mock.patch('console.network.check', return_value='198.51.100.1')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.device_type = self.router.device_type

    def fake_requests(self, stable='24.10.1', oldstable='23.05.5', boards_with_images=('24.10.1',)):
        def get(url, **kwargs):
            response = mock.Mock()
            if url == self.device_type.vendor_url:
                response.json.return_value = {'stable_version': stable, 'oldstable_version': oldstable}
                response.raise_for_status.return_value = None
            else:
                version = url.split('/releases/')[1].split('/')[0]
                response.status_code = 200 if version in boards_with_images else 404
            return response
        return mock.patch('routerlib.router_functions.requests.get', side_effect=get)

    def offered(self, version_id='23.05.3', board='ramips/mt7621'):
        values = {'VERSION_ID': version_id, 'OPENWRT_BOARD': board}
        return router_functions.vendor_available_version(self.device_type, version_id, values, '')

    def test_fields_and_newer_release_with_images(self):
        client = FakeSSHClient({
            'cat /etc/os-release': OS_RELEASE,
            'echo "hostname=$(uci get system.@system[0].hostname)"': 'hostname=ap-1\n',
            'echo "architecture=$(uname -m)"': 'architecture=mips\n',
            'echo "mac=$(cat /sys/class/net/eth0/address)"': 'mac=aa:bb:cc:dd:ee:ff\n',
        })
        with fake_ssh('routerlib.router_functions', client), self.fake_requests() as get:
            ok, error = router_functions.get_router_information(self.info)
        self.assertTrue(ok, error)
        info = RouterInformation.objects.get(pk=self.info.pk)
        self.assertEqual(info.model_name, 'Xiaomi AX3000')
        self.assertEqual(info.model_version, '23.05.3')
        self.assertEqual(info.os_version, '23.05.3')
        self.assertEqual(info.firmware_version, 'OpenWrt 23.05.3 r23809')
        self.assertEqual(info.serial_number, 'aa:bb:cc:dd:ee:ff')
        self.assertEqual(info.architecture, 'mips')
        self.assertEqual(info.available_version, '24.10.1')
        self.assertTrue(info.update_available)
        self.assertIn('/releases/24.10.1/targets/ramips/mt7621/profiles.json', get.call_args_list[-1].args[0])
        self.assertFalse(get.call_args_list[0].kwargs['allow_redirects'])

    def test_release_without_images_for_the_board_is_not_offered(self):
        with self.fake_requests(boards_with_images=()):
            self.assertEqual(self.offered(), ('23.05.3', False))

    def test_falls_back_to_oldstable_when_stable_has_no_image(self):
        with self.fake_requests(boards_with_images=('23.05.5',)):
            self.assertEqual(self.offered(), ('23.05.5', True))

    def test_older_releases_are_not_offered(self):
        with self.fake_requests(stable='23.05.3', oldstable='22.03.7', boards_with_images=('23.05.3', '22.03.7')):
            self.assertEqual(self.offered(), ('23.05.3', False))

    def test_an_up_to_date_device_counts_as_up_to_date(self):
        from router_manager.templatetags.router_tags import os_version_state
        with self.fake_requests(stable='23.05.3', oldstable='22.03.7'):
            available, _ = self.offered()
        self.info.os_version, self.info.available_version = '23.05.3', available
        self.assertEqual(os_version_state(self.info)['css_class'], 'text-success')

    def test_snapshot_and_unreadable_release_list_are_unknown(self):
        with self.fake_requests():
            self.assertEqual(self.offered('SNAPSHOT'), ('', False))
        with mock.patch('routerlib.router_functions.requests.get', side_effect=OSError('down')), \
                self.assertLogs('routerlib.router_functions', level='WARNING'):
            self.assertEqual(self.offered(), ('', False))

    def test_missing_board_is_not_confirmed(self):
        with self.fake_requests():
            self.assertEqual(self.offered(board=''), ('23.05.3', False))

    def test_release_list_is_cached(self):
        with self.fake_requests() as get:
            self.offered()
            self.offered()
        list_calls = [c for c in get.call_args_list if c.args[0] == self.device_type.vendor_url]
        self.assertEqual(len(list_calls), 1)

    def test_only_https_and_never_an_internal_address(self):
        from console.network import Refused
        from router_manager.models import DeviceType
        DeviceType.objects.filter(pk=self.device_type.pk).update(vendor_url='http://downloads.example.com/v.json')
        self.device_type.refresh_from_db()
        with mock.patch('routerlib.router_functions.requests.get') as get, \
                self.assertLogs('routerlib.router_functions', level='WARNING'):
            self.assertEqual(self.offered(), ('', False))
        get.assert_not_called()
        DeviceType.objects.filter(pk=self.device_type.pk).update(vendor_url='https://internal.example/v.json')
        self.device_type.refresh_from_db()
        with mock.patch('console.network.check', side_effect=Refused('internal')), \
                mock.patch('routerlib.router_functions.requests.get') as get, \
                self.assertLogs('routerlib.router_functions', level='WARNING'):
            self.assertEqual(self.offered(), ('', False))
        get.assert_not_called()

    def test_version_key(self):
        self.assertLess(router_functions._version_key('23.05.3'), router_functions._version_key('24.10.0'))
        self.assertEqual(router_functions._version_key(''), (0,))


class AirOSInformationTests(TestCase):
    def test_fields_and_no_update_check(self):
        router = make_router(router_type='ubiquiti-airos')
        info = make_information(router, available_version='stale', update_available=True)
        client = FakeSSHClient({
            'echo "firmware=$(cat /etc/version)"': 'firmware=XW.v6.3.11\n',
            'cat /etc/board.info': 'board.name=NanoStation M5\nboard.model=NSM5\nboard.device_id=DEV1\n',
            'echo "kernel=$(uname -r)"': 'kernel=2.6.32.71\n',
            'cat /proc/cpuinfo': 'system type : Atheros AR9342\ncpu model : MIPS 74Kc V5.0\n',
        })
        with fake_ssh('routerlib.router_functions', client):
            ok, error = router_functions.get_router_information(info)
        self.assertTrue(ok, error)
        info.refresh_from_db()
        self.assertEqual(info.model_name, 'NanoStation M5')
        self.assertEqual(info.model_version, 'NSM5')
        self.assertEqual(info.serial_number, 'DEV1')
        self.assertEqual(info.firmware_version, 'XW.v6.3.11')
        self.assertEqual(info.os_version, '2.6.32.71')
        self.assertEqual(info.architecture, 'MIPS')
        self.assertEqual(info.cpu, 'Atheros AR9342')
        self.assertEqual(info.available_version, '')
        self.assertFalse(info.update_available)

    def test_missing_board_info_fails(self):
        router = make_router(router_type='ubiquiti-airos')
        info = make_information(router)
        with fake_ssh('routerlib.router_functions', FakeSSHClient({})):
            ok, error = router_functions.get_router_information(info)
        self.assertFalse(ok)
        self.assertIn('Failed to retrieve router information', error)


class UpdateRouterInformationTests(TestCase):
    def setUp(self):
        self.router = make_router(router_type='monitoring')
        self.info = make_information(self.router)

    def test_unsupported_type_counts_a_retry(self):
        with fake_ssh('routerlib.router_functions', FakeSSHClient()):
            ok, error = router_functions.update_router_information(self.info)
        self.assertFalse(ok)
        self.info.refresh_from_db()
        self.assertEqual(self.info.retry_count, 1)
        self.assertTrue(self.info.error)
        self.assertIsNotNone(self.info.next_retry)
        self.assertIn('not supported', self.info.error_message)

    def test_connection_failure_is_recorded(self):
        self.router.router_type = 'routeros'
        self.router.save()
        with mock.patch('routerlib.device_session.connect_to_ssh', side_effect=OSError('timed out')):
            ok, error = router_functions.update_router_information(self.info)
        self.assertFalse(ok)
        self.assertIn('timed out', error)

    def test_after_max_retries_the_device_is_marked_errored_and_reset(self):
        self.info.retry_count = 4
        self.info.save()
        ok, message = router_functions.update_router_information(self.info)
        self.assertFalse(ok)
        self.info.refresh_from_db()
        self.assertEqual(self.info.retry_count, 0)
        self.assertIsNone(self.info.next_retry)
        self.assertIn('Max retries reached', self.info.error_message)


class SecretsAtRestTests(TestCase):
    """Every stored credential is ciphertext in its column."""

    def stored(self, table, column, pk):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT {column} FROM {table} WHERE id = %s', [pk])
            return cursor.fetchone()[0]

    def test_ssh_private_key(self):
        from routerfleet.testing import make_sshkey
        key = make_sshkey()
        self.assertTrue(self.stored('router_manager_sshkey', 'private_key', key.pk).startswith('gAAAAA'))
        from router_manager.models import SSHKey
        self.assertEqual(SSHKey.objects.get(pk=key.pk).private_key, 'FAKE-TEST-PRIVATE-KEY-MATERIAL')

    def test_channel_token(self):
        from message_center.models import MessageChannel
        channel = MessageChannel.objects.create(name='c', channel_type='telegram', destination='1', token='TOK')
        self.assertNotIn('TOK', self.stored('message_center_messagechannel', 'token', channel.pk))
        self.assertEqual(MessageChannel.objects.get(pk=channel.pk).token, 'TOK')

    def test_channel_token_is_still_a_one_line_input(self):
        from django import forms
        from message_center.forms import MessageChannelForm
        self.assertIsInstance(MessageChannelForm().fields['token'].widget, forms.TextInput)
