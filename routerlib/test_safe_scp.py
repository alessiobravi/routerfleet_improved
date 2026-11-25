"""A backup download takes exactly one file, at the path RouterFleet chose:
whatever the device sends - directories, more files, names with a path, a
huge size - nothing is written anywhere else."""
import os
import pathlib
import tempfile

from django.test import SimpleTestCase, override_settings
from scp import SCPException

from routerlib.safe_scp import SafeSCPClient


class FakeChannel:
    closed = False

    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    def settimeout(self, timeout): pass
    def exec_command(self, command): self.command = command
    def sendall(self, data): pass
    def send(self, data): self.sent.append(data)
    def shutdown_write(self): pass
    def recv_exit_status(self): return 0

    def recv(self, size):
        if not self.messages:
            self.closed = True
            return b''
        return self.messages.pop(0)

    def close(self): self.closed = True


class FakeTransport:
    def __init__(self, channel): self.channel = channel
    def getpeername(self): return ('192.0.2.1', 22)
    def open_session(self): return self.channel


class SafeDownloadTests(SimpleTestCase):
    def setUp(self):
        self.folder = pathlib.Path(tempfile.mkdtemp())
        (self.folder / 'work').mkdir()
        self.target = self.folder / 'work' / '0-backup.tar.gz'

    def files_outside_work(self):
        return [p for p in self.folder.rglob('*') if p.is_file() and 'work' not in p.relative_to(self.folder).parts[:1]]

    def download(self, messages):
        channel = FakeChannel(messages)
        SafeSCPClient(FakeTransport(channel)).get('/tmp/backup.tar.gz', str(self.target))
        return channel

    def test_one_file_goes_where_routerfleet_chose(self):
        self.download([b'C0644 5 backup.tar.gz\n', b'hello', b'\x00'])
        self.assertEqual(self.target.read_bytes(), b'hello')

    def test_a_directory_climbing_out_is_refused_and_nothing_written_outside(self):
        with self.assertRaises(SCPException):
            self.download([b'D0755 0 x\n', b'D0755 0 ../../target\n', b'C0644 5 planted.txt\n', b'hello', b'\x00'])
        self.assertEqual(self.files_outside_work(), [])
        self.assertFalse((self.folder / 'target').exists())

    def test_a_second_file_is_refused(self):
        with self.assertRaises(SCPException):
            self.download([b'C0644 5 a\n', b'hello', b'\x00', b'C0644 5 b\n', b'world', b'\x00'])
        self.assertFalse((self.folder / 'work' / 'b').exists())

    def test_a_name_with_a_path_is_refused(self):
        for name in (b'../evil', b'x/../../evil', b'..'):
            with self.subTest(name=name), self.assertRaises(SCPException):
                self.download([b'C0644 5 ' + name + b'\n', b'hello', b'\x00'])
        self.assertEqual(self.files_outside_work(), [])

    @override_settings(BACKUP_DOWNLOAD_MAX_BYTES=1024)
    def test_a_file_larger_than_the_limit_is_refused(self):
        with self.assertRaises(SCPException):
            self.download([b'C0644 999999 backup.tar.gz\n', b'x', b'\x00'])
        self.assertFalse(self.target.exists())

    def test_recursive_is_never_asked(self):
        with self.assertRaises(SCPException):
            SafeSCPClient(FakeTransport(FakeChannel([]))).get('/tmp', str(self.target), recursive=True)


class CronKeyTests(SimpleTestCase):
    """The scheduler's endpoints: with CRON_KEY (compose files with the
    service_keys volume) a call needs the key; without it, as before."""

    def call(self, **headers):
        from django.test import RequestFactory
        from routerlib.decorators import cron_endpoint
        from django.http import JsonResponse
        view = cron_endpoint(lambda request: JsonResponse({'ran': True}))
        return view(RequestFactory().get('/cron/housekeeping/', **headers))

    @override_settings(CRON_KEY='the-scheduler-key')
    def test_the_key_is_needed_when_set(self):
        self.assertEqual(self.call().status_code, 404)
        self.assertEqual(self.call(HTTP_X_RF_CRON_KEY='wrong').status_code, 404)
        self.assertEqual(self.call(HTTP_X_RF_CRON_KEY='the-scheduler-key').status_code, 200)
        # through nginx: refused even with the key
        self.assertEqual(self.call(HTTP_X_RF_CRON_KEY='the-scheduler-key', HTTP_X_REAL_IP='203.0.113.5').status_code, 404)

    @override_settings(CRON_KEY='')
    def test_without_a_key_set_an_older_compose_file_keeps_working(self):
        self.assertEqual(self.call().status_code, 200)

    def test_every_scheduled_call_sends_the_key(self):
        tasks = (pathlib.Path(__file__).resolve().parent.parent / 'containers/cron/cron_tasks').read_text()
        calls = [line for line in tasks.splitlines() if '/cron/' in line and not line.startswith('#')]
        self.assertTrue(calls)
        for line in calls:
            self.assertIn('-H "X-RF-Cron-Key: $(cat /service_keys/cron_key 2>/dev/null)"', line)
