"""The less travelled paths of routerlib: key loading, the connection, the
interactive shell, the SCP guard, encryption, the inventory and the backup
pipeline. No test opens a connection: paramiko and the sessions are fakes."""
import csv
import io
import socket
from unittest import mock

import paramiko
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from scp import SCPException

from backup_data.models import RouterBackup
from router_manager.models import DeviceType, RouterStatus
from routerfleet.testing import (FakeSCPClient, FakeSSHClient, edit_type, fake_ssh, make_information,
                                 make_profile, make_router, temporary_media_root)
from routerlib import backup_functions, device_session, encryption, functions, host_keys, router_functions
from routerlib.safe_scp import SafeSCPClient

TEST_ONLY_PASSWORD = 'test-only-password'
ROUTEROS_RESOURCE = 'version: 7.16.2 (stable)\nboard-name: hAP ax3\narchitecture-name: arm64\n'


# ---------------------------------------------------------------------------
# functions.py
# ---------------------------------------------------------------------------

def key_types(rsa=None, ecdsa=None, ed25519=None):
    """Patches the three key loaders, each raising what it is given."""
    not_valid = paramiko.ssh_exception.SSHException('not a valid private key file')
    patches = []
    for name, error in (('RSAKey', rsa), ('ECDSAKey', ecdsa), ('Ed25519Key', ed25519)):
        patches.append(mock.patch.object(getattr(paramiko, name), 'from_private_key',
                                         side_effect=error or not_valid))
    return patches


class PrivateKeyLoadingTests(SimpleTestCase):
    """A key that does not open: which reason is given, and when None is."""

    def load(self, passphrase=None, **errors):
        patches = key_types(**errors)
        for patch in patches:
            patch.start()
        try:
            return functions.load_private_key_from_string('FAKE-TEST-KEY', passphrase)
        finally:
            for patch in patches:
                patch.stop()

    def test_a_protected_key_without_a_passphrase(self):
        with self.assertRaisesMessage(functions.KeyPassphraseError, 'protected by a passphrase'):
            self.load(rsa=paramiko.ssh_exception.PasswordRequiredException('needs one'))

    def test_a_wrong_passphrase_surfacing_as_a_padding_error(self):
        with self.assertRaisesMessage(functions.KeyPassphraseError, 'missing or wrong'):
            self.load(passphrase=TEST_ONLY_PASSWORD, ecdsa=ValueError('bad padding'))

    def test_a_padding_error_without_a_passphrase_is_just_not_a_key(self):
        self.assertIsNone(self.load(ed25519=ValueError('bad padding')))

    def test_another_ssh_error_with_a_passphrase_means_it_did_not_open(self):
        with self.assertRaises(functions.KeyPassphraseError):
            self.load(passphrase=TEST_ONLY_PASSWORD, rsa=paramiko.ssh_exception.SSHException('checkints do not match'))

    def test_a_key_that_opens_is_returned(self):
        patches = key_types(rsa=None)
        with patches[0] as rsa:
            rsa.side_effect = None
            rsa.return_value = mock.sentinel.key
            self.assertIs(functions.load_private_key_from_string('FAKE-TEST-KEY'), mock.sentinel.key)


class ConnectToSshTests(SimpleTestCase):
    """The connection itself, with paramiko's client faked: the checked address,
    the SSH key, and closing everything on a failure."""

    def connect(self, client, **kwargs):
        with mock.patch('routerlib.functions.paramiko.SSHClient', return_value=client), \
                mock.patch.object(host_keys, 'prepare', return_value=None), \
                mock.patch.object(host_keys, 'remember') as remember:
            result = functions.connect_to_ssh('192.0.2.1', 22, 'admin', TEST_ONLY_PASSWORD, **kwargs)
        return result, remember

    def test_the_checked_address_and_an_ssh_key(self):
        client = mock.Mock()
        sock = mock.Mock()
        sshkey = mock.Mock(private_key='FAKE-TEST-KEY', passphrase='')
        with mock.patch('socket.create_connection', return_value=sock) as create, \
                mock.patch.object(functions, 'load_private_key_from_string', return_value=mock.sentinel.pkey):
            result, remember = self.connect(client, sshkey=sshkey, connect_to='192.0.2.2')
        self.assertIs(result, client)
        create.assert_called_once_with(('192.0.2.2', 22), timeout=10)
        kwargs = client.connect.call_args.kwargs
        self.assertIs(kwargs['pkey'], mock.sentinel.pkey)
        self.assertIs(kwargs['sock'], sock)
        self.assertNotIn('password', kwargs)
        remember.assert_called_once()

    def test_a_changed_host_key_closes_the_socket_and_says_so(self):
        client = mock.Mock()
        key = mock.Mock()
        key.get_name.return_value = 'ssh-ed25519'
        key.asbytes.return_value = b'test-key'
        client.connect.side_effect = paramiko.BadHostKeyException('192.0.2.1', key, key)
        sock = mock.Mock()
        stored = mock.Mock(key_type='ssh-ed25519', fingerprint='SHA256:stored')
        with mock.patch('socket.create_connection', return_value=sock), \
                mock.patch('routerlib.functions.paramiko.SSHClient', return_value=client), \
                mock.patch.object(host_keys, 'prepare', return_value=stored), \
                self.assertRaises(host_keys.HostKeyChanged) as raised:
            functions.connect_to_ssh('192.0.2.1', 22, 'admin', TEST_ONLY_PASSWORD, connect_to='192.0.2.1')
        self.assertIn('SHA256:stored', str(raised.exception))
        client.close.assert_called_once()
        sock.close.assert_called_once()

    def test_any_other_failure_closes_the_socket_too(self):
        client = mock.Mock()
        client.connect.side_effect = TimeoutError('timed out')
        sock = mock.Mock()
        with mock.patch('socket.create_connection', return_value=sock), \
                self.assertRaises(socket.timeout):
            self.connect(client, connect_to='192.0.2.1')
        client.close.assert_called_once()
        sock.close.assert_called_once()


# ---------------------------------------------------------------------------
# device_session.py
# ---------------------------------------------------------------------------

class ScriptedChannel:
    """A shell that hands out prepared chunks, then nothing (a timeout)."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []
        self.closed = False

    def settimeout(self, _):
        pass

    def recv(self, _):
        if not self.chunks:
            raise TimeoutError
        return self.chunks.pop(0)

    def send(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


class ShellClient:
    def __init__(self, channel):
        self.channel = channel
        self.closed = False

    def invoke_shell(self, **_):
        return self.channel

    def get_transport(self):
        return mock.sentinel.transport

    def close(self):
        self.closed = True


def shell_type(**fields):
    fields.setdefault('prompt_pattern', r'(?m)^[\w.-]+[>#]\s*$')
    return DeviceType(name='Test shell', session_mode='shell', **fields)


class ShellSessionEdgeTests(SimpleTestCase):
    """The interactive shell's rarer turns: a bad prompt pattern, a closed
    session, a pager right before the prompt, output after the prompt line."""

    def test_an_invalid_prompt_pattern_is_refused(self):
        with self.assertRaisesMessage(device_session.SessionError, 'not valid'):
            device_session.ShellSession(ShellClient(ScriptedChannel([])), shell_type(prompt_pattern='(['))

    def test_blank_setup_lines_are_not_sent(self):
        channel = ScriptedChannel([b'r1>', b'terminal length 0\r\nr1>'])
        device_session.ShellSession(ShellClient(channel), shell_type(session_setup='\n  \nterminal length 0\n'))
        self.assertEqual(channel.sent, ['terminal length 0\n'])

    def test_a_closed_session_is_an_error(self):
        channel = ScriptedChannel([b''])
        with self.assertRaisesMessage(device_session.SessionError, 'closed the session'):
            device_session.ShellSession(ShellClient(channel), shell_type())

    def test_a_pager_right_before_the_prompt_is_answered_and_the_prompt_seen(self):
        channel = ScriptedChannel([b'r1>', b'show x\r\nline\r\nr1>--More--'])
        session = device_session.ShellSession(ShellClient(channel), shell_type())
        _, stdout, _ = session.exec_command('show x')
        self.assertEqual(stdout.read(), b'line\n')
        self.assertIn(' ', channel.sent, 'the pager was answered')

    def test_already_privileged_needs_no_enable_password(self):
        channel = ScriptedChannel([b'r1>', b'enable\r\nr1#'])
        session = device_session.ShellSession(ShellClient(channel), shell_type(enable_command='enable'),
                                              enable_password=TEST_ONLY_PASSWORD)
        self.assertEqual(channel.sent, ['enable\n'])
        self.assertEqual(session.last_prompt, 'r1>')

    def test_output_without_an_echo_and_a_newline_after_the_prompt(self):
        channel = ScriptedChannel([b'r1>', b'only output\r\nr1>\r\n'])
        session = device_session.ShellSession(ShellClient(channel), shell_type())
        _, stdout, _ = session.exec_command('show y')
        self.assertIn(b'only output', stdout.read())

    def test_transport_and_close(self):
        channel = ScriptedChannel([b'r1>'])
        client = ShellClient(channel)
        session = device_session.ShellSession(client, shell_type())
        self.assertIs(session.get_transport(), mock.sentinel.transport)
        session.close()
        self.assertTrue(channel.closed)
        self.assertTrue(client.closed)

    def test_the_client_is_closed_even_when_the_channel_will_not(self):
        channel = ScriptedChannel([b'r1>'])
        channel.close = mock.Mock(side_effect=OSError('gone'))
        client = ShellClient(channel)
        session = device_session.ShellSession(client, shell_type())
        with self.assertRaises(OSError):
            session.close()
        self.assertTrue(client.closed)


class ExecSessionAndConnectTests(SimpleTestCase):
    """exec types: an answer the command does not wait for; connect() for shell
    types, and the client closed when the shell shows no prompt."""

    def test_an_answer_nobody_reads_is_ignored(self):
        client = mock.Mock()
        stdin = mock.Mock()
        stdin.write.side_effect = OSError('channel closed')
        client.exec_command.return_value = (stdin, 'out', 'err')
        session = device_session.ExecSession(client)
        self.assertEqual(session.exec_command('/system reboot <<< y'), (stdin, 'out', 'err'))
        client.get_transport.return_value = mock.sentinel.transport
        self.assertIs(session.get_transport(), mock.sentinel.transport)
        session.close()
        client.close.assert_called_once()

    def test_a_shell_type_gets_a_shell_session(self):
        client = ShellClient(ScriptedChannel([b'r1>']))
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client):
            session = device_session.connect(shell_type(), '192.0.2.1', 22, 'u', TEST_ONLY_PASSWORD)
        self.assertIsInstance(session, device_session.ShellSession)

    def test_a_shell_that_fails_closes_the_client(self):
        client = ShellClient(ScriptedChannel([b'']))
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client), \
                self.assertRaises(device_session.SessionError):
            device_session.connect(shell_type(), '192.0.2.1', 22, 'u', TEST_ONLY_PASSWORD)
        self.assertTrue(client.closed)

    def test_without_a_type_it_is_an_exec_session(self):
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=mock.Mock()):
            self.assertIsInstance(device_session.connect(None, '192.0.2.1', 22, 'u', 'p'), device_session.ExecSession)


# ---------------------------------------------------------------------------
# safe_scp.py
# ---------------------------------------------------------------------------

class RecordingChannel:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


class SafeScpHeaderTests(SimpleTestCase):
    """The file header and time messages, checked one by one."""

    def scp(self, received=0):
        client = SafeSCPClient(mock.Mock())
        client.channel = RecordingChannel()
        client._files_received = received
        return client

    def test_a_time_message_after_the_file_is_refused(self):
        client = self.scp(received=1)
        with self.assertRaisesMessage(SCPException, 'more after its file'):
            client._set_time(b'T1 0 1 0')
        self.assertTrue(client.channel.closed)
        self.assertTrue(client.channel.sent[0].startswith(b'\x01'))

    def test_a_time_message_before_the_file_goes_through(self):
        client = self.scp()
        with mock.patch('scp.SCPClient._set_time', return_value='ok') as parent:
            self.assertEqual(client._set_time(b'T1 0 1 0'), 'ok')
        parent.assert_called_once()

    def test_a_header_with_missing_parts(self):
        with self.assertRaisesMessage(SCPException, 'Bad file header'):
            self.scp()._recv_file(b'C0644 5\n')

    def test_a_size_that_is_not_a_number(self):
        with self.assertRaisesMessage(SCPException, 'Bad file size'):
            self.scp()._recv_file(b'C0644 five name\n')

    def test_a_negative_size(self):
        with self.assertRaisesMessage(SCPException, 'larger than'):
            self.scp()._recv_file(b'C0644 -1 name\n')


# ---------------------------------------------------------------------------
# encryption.py
# ---------------------------------------------------------------------------

class EncryptionEdgeTests(SimpleTestCase):
    """The unreadable-secret flag when the cache is down, CSV that does not
    parse, and a value the database hands back as bytes."""

    def test_the_flag_never_breaks_a_read(self):
        with mock.patch('django.core.cache.caches') as caches:
            caches.__getitem__.side_effect = RuntimeError('cache down')
            encryption.note_unreadable()
            self.assertFalse(encryption.unreadable_seen())

    def test_the_flag_is_remembered(self):
        with mock.patch('django.core.cache.caches') as caches:
            store = {}
            caches.__getitem__.return_value.set.side_effect = lambda key, value, timeout: store.update({key: value})
            caches.__getitem__.return_value.get.side_effect = store.get
            encryption.note_unreadable()
            self.assertTrue(encryption.unreadable_seen())

    def test_csv_that_does_not_parse_is_kept(self):
        raw = 'name,password\n"a,' + 'x' * (csv.field_size_limit() + 10) + '\n'
        with self.assertLogs('routerlib.encryption', level='WARNING'):
            self.assertEqual(encryption.mask_passwords_in_csv(raw), raw)

    def test_bytes_from_the_database_are_decrypted(self):
        token = encryption.encrypt_value('test-only-secret')
        field = encryption.EncryptedTextField()
        self.assertEqual(field.from_db_value(token.encode(), None, None), 'test-only-secret')
        self.assertEqual(field.from_db_value(memoryview(token.encode()), None, None), 'test-only-secret')
        self.assertIsNone(field.from_db_value(None, None, None))
        self.assertIsNone(field.get_prep_value(None))


# ---------------------------------------------------------------------------
# router_functions.py
# ---------------------------------------------------------------------------

class VendorCheckTests(TestCase):
    """The vendor's release list: unreadable confirmations, keys that point
    nowhere, a type that confirms nothing."""

    def setUp(self):
        router_functions._vendor_cache.clear()
        router_functions._confirm_cache.clear()
        self.addCleanup(router_functions._vendor_cache.clear)
        self.addCleanup(router_functions._confirm_cache.clear)

    def vendor_type(self, **fields):
        fields.setdefault('vendor_url', 'https://downloads.example.com/releases.json')
        fields.setdefault('vendor_keys', 'stable')
        return DeviceType(name='Vendor', **fields)

    def test_an_unreadable_confirmation_is_asked_again(self):
        import requests
        with mock.patch('routerlib.outbound.https_get', side_effect=requests.ConnectionError('down')) as get:
            self.assertFalse(router_functions._confirmed('https://downloads.example.com/x'))
            self.assertFalse(router_functions._confirmed('https://downloads.example.com/x'))
        self.assertEqual(get.call_count, 2, 'not cached')

    def test_dig_into_a_value_that_is_not_a_dict(self):
        self.assertEqual(router_functions._dig({'a': 'text'}, 'a.b'), '')
        self.assertEqual(router_functions._dig({'a': {'b': [1]}}, 'a.b'), '')
        self.assertEqual(router_functions._dig({'a': {'b': 2}}, 'a.b'), '2')

    def test_keys_that_find_nothing(self):
        router_functions._vendor_cache[self.vendor_type().vendor_url] = (__import__('time').time(), {'other': '9'})
        self.assertEqual(router_functions.vendor_available_version(self.vendor_type(), '1.0', {}, ''), ('', False))

    def test_without_a_confirmation_address_the_newer_release_counts(self):
        device_type = self.vendor_type()
        router_functions._vendor_cache[device_type.vendor_url] = (__import__('time').time(), {'stable': '2.0'})
        self.assertEqual(router_functions.vendor_available_version(device_type, '1.0', {}, ''), ('2.0', True))

    def test_no_installed_version_or_no_address(self):
        self.assertEqual(router_functions.vendor_available_version(self.vendor_type(), '', {}, ''), ('', False))
        self.assertEqual(router_functions.vendor_available_version(self.vendor_type(vendor_url=''), '1.0', {}, ''),
                         ('', False))


class AvailableVersionTests(SimpleTestCase):
    """Choosing the newest of the device's, the vendor's and the repository's
    offers."""

    def device_type(self, **fields):
        base = {'name': 'T', 'check_device': False, 'check_vendor': False, 'check_repository': False}
        base.update(fields)
        return DeviceType(**base)

    def test_a_vendor_check_with_nothing_and_an_empty_repository(self):
        device_type = self.device_type(check_vendor=True, check_repository=True)
        with mock.patch.object(router_functions, 'vendor_available_version', return_value=('', False)), \
                mock.patch('firmware.models.FirmwareImage.newest_for', return_value=None):
            self.assertEqual(router_functions.available_version(device_type, {'os_version': '1.0'}, {}, ''),
                             ('', False))

    def test_a_failing_repository_never_fails_the_inventory(self):
        device_type = self.device_type(check_device=True, check_repository=True)
        with mock.patch('firmware.models.FirmwareImage.newest_for', side_effect=RuntimeError('db')), \
                self.assertLogs('routerlib.router_functions', level='WARNING'):
            result = router_functions.available_version(
                device_type, {'os_version': '1.0', 'available_version': '1.1'}, {}, '')
        self.assertEqual(result, ('1.1', True))

    def test_the_newest_offer_wins(self):
        device_type = self.device_type(check_device=True, check_vendor=True)
        with mock.patch.object(router_functions, 'vendor_available_version', return_value=('1.3', True)):
            result = router_functions.available_version(
                device_type, {'os_version': '1.0', 'available_version': '1.1'}, {}, '')
        self.assertEqual(result, ('1.3', True))

    def test_an_older_later_offer_does_not_replace_the_newest(self):
        device_type = self.device_type(check_device=True, check_vendor=True)
        with mock.patch.object(router_functions, 'vendor_available_version', return_value=('1.1', True)):
            result = router_functions.available_version(
                device_type, {'os_version': '1.0', 'available_version': '1.3'}, {}, '')
        self.assertEqual(result, ('1.3', True))


@mock.patch('routerlib.router_functions.time.sleep')
class InventoryEdgeTests(TestCase):
    """The inventory read: a bad repeat pattern, a repeat read that fails, an
    update check that raises, the rights read, a session that will not close."""

    def setUp(self):
        self.router = make_router(router_type='routeros', username='admin')
        self.info = make_information(self.router)

    def test_an_invalid_repeat_pattern_reads_once(self, sleep):
        edit_type('routeros', inventory_repeat='([')
        self.router.refresh_from_db()
        router_functions._run_inventory(FakeSSHClient({'/system resource print': ROUTEROS_RESOURCE}), self.router)
        sleep.assert_not_called()

    def test_a_repeat_read_that_fails_stops_waiting(self, sleep):
        calls = {'n': 0}

        def update(command):
            calls['n'] += 1
            if calls['n'] == 1:
                return 'status: finding out latest version...\n'
            raise OSError('dropped')
        client = FakeSSHClient({'/system resource print': ROUTEROS_RESOURCE, '/system package update print': update})
        with self.assertLogs('routerlib.router_functions', level='WARNING') as logs:
            _, values = router_functions._run_inventory(client, self.router)
        self.assertIn('Update check failed', ' '.join(logs.output))
        self.assertNotIn('latest-version', values)
        self.assertEqual(sleep.call_count, 1)

    def run_read(self, client, **patches):
        with fake_ssh('routerlib.router_functions', client):
            if patches:
                with mock.patch.multiple(router_functions, **patches):
                    return router_functions.get_router_information(self.info)
            return router_functions.get_router_information(self.info)

    def test_an_update_check_that_raises_offers_nothing(self, _sleep):
        client = FakeSSHClient({'/system resource print': ROUTEROS_RESOURCE})
        with self.assertLogs('routerlib.router_functions', level='WARNING'):
            ok, error = self.run_read(client, available_version=mock.Mock(side_effect=RuntimeError('boom')))
        self.assertTrue(ok, error)
        self.info.refresh_from_db()
        self.assertEqual((self.info.available_version, self.info.update_available), ('', False))

    def test_the_account_rights_are_read_and_a_failure_leaves_them_unknown(self, _sleep):
        def device(command):
            if command.startswith(':put [/user group'):
                return 'read,write,!policy\n'
            return ROUTEROS_RESOURCE if command == '/system resource print' else ''
        ok, error = self.run_read(FakeSSHClient({}, default=device))
        self.assertTrue(ok, error)
        self.info.refresh_from_db()
        self.assertEqual(self.info.account_rights, 'read,write')

        def broken(command):
            if command.startswith(':put [/user group'):
                raise OSError('dropped')
            return device(command)
        with self.assertLogs('routerlib.router_functions', level='WARNING') as logs:
            ok, error = self.run_read(FakeSSHClient({}, default=broken))
        self.assertTrue(ok, error)
        self.assertIn('Rights check failed', ' '.join(logs.output))
        self.info.refresh_from_db()
        self.assertEqual(self.info.account_rights, '')

    def test_a_session_that_will_not_close_does_not_matter(self, _sleep):
        client = FakeSSHClient({'/system resource print': ROUTEROS_RESOURCE})
        client.close = mock.Mock(side_effect=OSError('already gone'))
        ok, error = self.run_read(client)
        self.assertTrue(ok, error)


class UpdateRouterInformationEdgeTests(TestCase):
    """The retry bookkeeping around a read."""

    def setUp(self):
        self.router = make_router(router_type='routeros', name='inv-1')
        self.info = make_information(self.router)

    def test_max_retries_appends_to_an_earlier_message(self):
        self.info.retry_count, self.info.error_message = 4, 'timed out'
        self.info.save()
        ok, message = router_functions.update_router_information(self.info)
        self.assertFalse(ok)
        self.assertEqual(message, 'timed out\nMax retries reached for inv-1')

    def test_a_read_that_raises_is_recorded(self):
        with mock.patch.object(router_functions, 'get_router_information', side_effect=RuntimeError('boom')):
            ok, message = router_functions.update_router_information(self.info)
        self.assertFalse(ok)
        self.assertIn('Exception: boom', message)
        self.info.refresh_from_db()
        self.assertEqual(self.info.retry_count, 1)

    def test_a_failure_without_a_message_gets_one(self):
        with mock.patch.object(router_functions, 'get_router_information', return_value=(False, '')):
            router_functions.update_router_information(self.info)
        self.info.refresh_from_db()
        self.assertEqual(self.info.error_message, 'Failed to update device information for inv-1')

    def test_a_success_records_nothing_more(self):
        with mock.patch.object(router_functions, 'get_router_information', return_value=(True, '')):
            self.assertEqual(router_functions.update_router_information(self.info), (True, ''))
        self.info.refresh_from_db()
        self.assertEqual(self.info.retry_count, 0)


# ---------------------------------------------------------------------------
# backup_functions.py
# ---------------------------------------------------------------------------

class BackupHelperTests(TestCase):
    """The console log and the stream helpers, with and without a backup to
    write to."""

    def setUp(self):
        self.router = make_router(router_type='openwrt', backup_profile=make_profile())
        self.backup = RouterBackup.objects.create(router=self.router, schedule_type='instant')

    def test_a_line_without_a_newline_and_an_empty_log(self):
        self.backup.task_console_output = None
        backup_functions.append_task_console_output(self.backup, 'first', new_line=False)
        self.assertTrue(self.backup.task_console_output.endswith('] first'))

    def test_scp_with_and_without_a_backup_to_log_to(self):
        scp = mock.Mock()
        backup_functions.run_scp_get(scp, '/a', '/tmp/a')
        scp.get.side_effect = OSError('no such file')
        with self.assertRaises(OSError):
            backup_functions.run_scp_get(scp, '/a', '/tmp/a')
        self.backup.task_console_output = None
        with self.assertRaises(OSError):
            backup_functions.run_scp_get(scp, '/a', '/tmp/a', self.backup)
        self.assertIn('[scp_error] no such file', self.backup.task_console_output)

    def test_read_limited_stops_at_the_limit(self):
        with self.assertRaisesMessage(ValueError, 'more than 1 MB'):
            backup_functions.read_limited(io.BytesIO(b'x' * (1024 * 1024 + 1)), 1024 * 1024)

    def test_latin1_fallback_without_a_backup(self):
        self.assertEqual(backup_functions.decode_text('caf\xe9'.encode('latin-1')), 'caf\xe9')

    def test_a_command_without_a_backup_and_one_with_errors(self):
        client = FakeSSHClient({'x': (0, 'out\n', 'warn\n')})
        self.assertEqual(backup_functions.run_ssh_command(client, 'x'), (0, 'out\n', 'warn\n'))
        self.backup.task_console_output = None
        backup_functions.run_ssh_command(client, 'x', self.backup, skip_stdout=True)
        self.assertIn('[stdout suppressed]', self.backup.task_console_output)
        self.assertIn('[stderr]\nwarn', self.backup.task_console_output)
        quiet = FakeSSHClient({'y': (0, '', '')})
        backup_functions.run_ssh_command(quiet, 'y', self.backup, skip_stdout=True)
        self.assertIn('[exit_code=0]', self.backup.task_console_output)

    def test_cat_is_not_tried_in_a_shell_and_a_failing_one_falls_back(self):
        self.assertFalse(backup_functions._download_by_cat(None, DeviceType(session_mode='shell'), '/a', '/tmp/a',
                                                           self.backup))
        client = FakeSSHClient({}, default=OSError('channel refused'))
        self.assertFalse(backup_functions._download_by_cat(client, DeviceType(session_mode='exec'), '/a', '/tmp/a',
                                                           self.backup))
        self.assertIn('[cat unavailable: channel refused]', self.backup.task_console_output)

    def test_values_without_a_profile(self):
        router = make_router(router_type='openwrt')
        backup = RouterBackup.objects.create(router=router, schedule_type='instant')
        self.assertEqual(backup_functions._backup_values(backup)['export_parameters'], '')


class BackupPipelineEdgeTests(TestCase):
    """perform_backup and its phases where the usual tests do not go."""

    def setUp(self):
        self.profile = make_profile(parameter_sensitive=True)
        FakeSCPClient.files, FakeSCPClient.fetched = {}, []
        self.media = temporary_media_root()
        self.media.__enter__()
        self.addCleanup(self.media.__exit__, None, None, None)

    def make_backup(self, router_type, **fields):
        router = make_router(router_type=router_type, backup_profile=self.profile)
        RouterStatus.objects.filter(router=router).update(backup_lock=timezone.now())
        return RouterBackup.objects.create(router=router, schedule_type='instant', **fields)

    def set_commands(self, router_type, **payloads):
        from fleet_commander.models import CommandVariant
        for key, payload in payloads.items():
            CommandVariant.objects.filter(command__system_key=key, router_type=router_type).update(payload=payload)

    def run_phase(self, backup, client, function=backup_functions.perform_backup):
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client), \
                mock.patch('routerlib.backup_functions.SafeSCPClient', FakeSCPClient):
            result = function(backup)
        backup.refresh_from_db()
        return result

    def test_retrieval_without_a_schedule_time_counts_from_creation(self):
        backup = self.make_backup('routeros', backup_pending_retrieval=True, backup_text='cfg')
        with mock.patch.object(backup_functions, 'retrieve_backup', return_value=(True, '')), \
                mock.patch.object(backup_functions, 'clean_up_backup_files'):
            backup_functions.perform_backup(backup)
        backup.refresh_from_db()
        self.assertTrue(backup.success)
        self.assertIsNotNone(backup.queue_length)

    def test_a_retrieval_that_fails_without_a_reason(self):
        backup = self.make_backup('routeros', backup_pending_retrieval=True)
        with mock.patch.object(backup_functions, 'retrieve_backup', return_value=(False, '')):
            backup_functions.perform_backup(backup)
        backup.refresh_from_db()
        self.assertEqual(backup.retry_count, 1)

    def test_nothing_to_create_downloads_the_files_as_they_are(self):
        backup = self.make_backup('openwrt')
        self.set_commands('openwrt', backup_text='', backup_create='')
        ok, files, error = self.run_phase(backup, FakeSSHClient(), backup_functions.execute_backup)
        self.assertEqual((ok, files, error), (True, [], ''))
        self.assertIn('Nothing to create on the device', backup.task_console_output)

    def test_a_refused_create_command_fails(self):
        backup = self.make_backup('openwrt')
        name = backup_functions.gen_backup_name(backup)
        client = FakeSSHClient({'uci export': 'config system\n',
                                f'sysupgrade --create-backup /tmp/{name}.tar.gz': (1, '', 'no space left\n')})
        ok, _, error = self.run_phase(backup, client, backup_functions.execute_backup)
        self.assertFalse(ok)
        self.assertIn('the device refused "sysupgrade', error)
        self.assertIn('no space left', error)

    def test_a_refused_create_command_without_a_message(self):
        backup = self.make_backup('openwrt')
        name = backup_functions.gen_backup_name(backup)
        client = FakeSSHClient({'uci export': 'config system\n',
                                f'sysupgrade --create-backup /tmp/{name}.tar.gz': (2, '', '')})
        ok, _, error = self.run_phase(backup, client, backup_functions.execute_backup)
        self.assertFalse(ok)
        self.assertIn('no message', error)

    def test_retrieval_of_an_unsupported_type(self):
        backup = self.make_backup('monitoring')
        ok, error = backup_functions.retrieve_backup(backup)
        self.assertFalse(ok)
        self.assertIn('not supported', error)

    def test_a_second_binary_file_is_skipped(self):
        backup = self.make_backup('openwrt', backup_text='config system\n')
        self.set_commands('openwrt', backup_download='/tmp/one.tar.gz\n/tmp/two.tar.gz')
        FakeSCPClient.files = {'/tmp/one.tar.gz': b'one', '/tmp/two.tar.gz': b'two'}
        ok, error = self.run_phase(backup, FakeSSHClient(), backup_functions.retrieve_backup)
        self.assertTrue(ok, error)
        self.assertEqual(FakeSCPClient.fetched, ['/tmp/one.tar.gz'])
        self.assertIn('[skipped] /tmp/two.tar.gz', backup.task_console_output)

    def test_text_only_with_files_off_and_no_text_yet(self):
        backup = self.make_backup('openwrt')
        self.set_commands('openwrt', backup_download='/tmp/x.tar.gz')
        with mock.patch('router_manager.models.Router.takes_file_backups', return_value=False):
            ok, error = self.run_phase(backup, FakeSSHClient(), backup_functions.retrieve_backup)
        self.assertFalse(ok)
        self.assertIn('takes no text backup', error)

    def test_clean_up_of_an_unmanaged_device(self):
        backup = self.make_backup('monitoring')
        backup_functions.clean_up_backup_files(backup)
        self.assertIn('Device type not supported', backup.task_console_output)

    def test_clean_up_with_nothing_to_clean(self):
        backup = self.make_backup('openwrt')
        self.set_commands('openwrt', backup_cleanup='')
        backup_functions.clean_up_backup_files(backup)
        self.assertIn('Nothing to clean up', backup.task_console_output)

    def test_clean_up_that_can_not_connect(self):
        backup = self.make_backup('routeros')
        with mock.patch('routerlib.device_session.connect_to_ssh', side_effect=OSError('refused')):
            backup_functions.clean_up_backup_files(backup)
        self.assertIn('Failed to clean up backup files: refused', backup.task_console_output)
