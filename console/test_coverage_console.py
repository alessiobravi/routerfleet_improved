"""The console's less travelled paths: Telnet negotiation corner cases, the
consumer's bridge and closing driven directly (no WebSocket, no device), the
form's refusals and a session's duration."""
import json
from unittest import mock

from django.contrib.sessions.backends.db import SessionStore
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from console import consumers
from console.consumers import ConsoleConsumer, TelnetFilter
from console.models import ConsoleSession
from routerfleet.testing import make_router, make_user

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240


class TelnetNegotiationTests(SimpleTestCase):
    """Refusals, sub-negotiations, other commands and sequences cut short."""

    def test_refusals_from_the_device_need_no_answer(self):
        text, replies = TelnetFilter().feed(bytes((IAC, DONT, 1, IAC, WONT, 3)) + b'ok')
        self.assertEqual((text, replies), (b'ok', b''))

    def test_a_request_for_another_option_is_refused(self):
        _, replies = TelnetFilter().feed(bytes((IAC, DO, 24)))
        self.assertEqual(replies, bytes((IAC, WONT, 24)))

    def test_a_sub_negotiation_is_dropped(self):
        text, _ = TelnetFilter().feed(b'a' + bytes((IAC, SB, 24, 1, IAC, SE)) + b'b')
        self.assertEqual(text, b'ab')

    def test_a_sub_negotiation_split_over_two_reads(self):
        telnet = TelnetFilter()
        self.assertEqual(telnet.feed(b'a' + bytes((IAC, SB, 24, 1))), (b'a', b''))
        self.assertEqual(telnet.feed(bytes((IAC, SE)) + b'b'), (b'b', b''))

    def test_an_option_split_after_its_command(self):
        telnet = TelnetFilter()
        self.assertEqual(telnet.feed(bytes((IAC, WILL))), (b'', b''))
        self.assertEqual(telnet.feed(bytes((1,)) + b'x'), (b'x', bytes((IAC, DO, 1))))

    def test_another_two_byte_command_is_skipped(self):
        # IAC NOP (241)
        self.assertEqual(TelnetFilter().feed(b'a' + bytes((IAC, 241)) + b'b'), (b'ab', b''))

    def test_a_sub_negotiation_that_never_ends_is_dropped(self):
        telnet = TelnetFilter()
        telnet.feed(bytes((IAC, SB)))
        text, _ = telnet.feed(b'x' * (TelnetFilter.MOST_PENDING + 10))
        self.assertEqual(text, b'')
        self.assertEqual(telnet.pending, b'')


class LazyConnectTests(SimpleTestCase):
    """The consumer's connect_to_ssh hands on to routerlib's."""

    def test_handed_on(self):
        with mock.patch('routerlib.functions.connect_to_ssh', return_value='client') as connect:
            self.assertEqual(consumers.connect_to_ssh('192.0.2.1', 22, user='u'), 'client')
        connect.assert_called_once_with('192.0.2.1', 22, user='u')


def consumer(**scope):
    """A consumer without a WebSocket: what it sends and closes is recorded."""
    instance = ConsoleConsumer()
    instance.scope = {'type': 'websocket', 'headers': [], **scope}
    instance.send = mock.AsyncMock()
    instance.close = mock.AsyncMock()
    return instance


def statuses(instance):
    return [json.loads(c.kwargs['text_data'])['text'] for c in instance.send.call_args_list if 'text_data' in c.kwargs]


class FakeSession:
    pk = None
    bytes_to_device = 0
    bytes_from_device = 0


class ConsumerUnitTests(SimpleTestCase):
    """The bridge and its edges, without a database, a socket or a device."""

    async def test_no_wait_for_a_ticket_once_one_came(self):
        instance = consumer()
        instance.session = FakeSession()
        with mock.patch.object(consumers, 'FIRST_MESSAGE_SECONDS', 0):
            await instance.expect_first_message()
        instance.close.assert_not_called()

    async def test_a_first_message_that_is_not_a_ticket_closes(self):
        for data in (b'not json', b'{"no": "ticket"}', b'[1]', b'\xff\xfe'):
            with self.subTest(data=data):
                instance = consumer()
                await instance.receive(bytes_data=data)
                instance.close.assert_awaited_once_with(code=consumers.CLOSE_FORBIDDEN)

    async def test_messages_that_are_not_input_are_ignored(self):
        instance = consumer()
        instance.session = FakeSession()
        instance.to_device = mock.AsyncMock()
        await instance.receive(text_data='not json')
        await instance.receive(text_data=json.dumps({'type': 'something else'}))
        await instance.receive(text_data=json.dumps({'type': 'resize', 'cols': 80, 'rows': 24}))
        instance.to_device.assert_not_awaited()
        await instance.receive(text_data=json.dumps({'type': 'input', 'data': 'ls\r'}))
        instance.to_device.assert_awaited_once_with(b'ls\r')

    def test_resizing_without_a_device_or_with_one_that_fails(self):
        instance = consumer()
        instance.resize(80, 24)   # nothing to resize yet
        instance.device = mock.Mock()
        instance.device.resize_pty.side_effect = OSError('closed')
        instance.resize(80, 24)
        instance.device.resize_pty.assert_called_once_with(width=80, height=24)

    async def test_input_with_nowhere_to_go(self):
        instance = consumer()
        await instance.to_device(b'lost')   # no session, no device, no Telnet
        instance.session = FakeSession()
        await instance.to_device(b'12345')
        self.assertEqual(instance.session.bytes_to_device, 5)

    async def test_nothing_is_shown_after_closing(self):
        instance = consumer()
        instance.session = FakeSession()
        instance.closing = True
        await instance.from_device(b'late')
        instance.send.assert_not_awaited()
        self.assertEqual(instance.session.bytes_from_device, 0)

    def test_the_ssh_reader_stops_on_closing_and_on_errors(self):
        instance = consumer()
        instance.loop = mock.sentinel.loop
        instance.end = mock.Mock(return_value=mock.sentinel.ending)
        with mock.patch('console.consumers.asyncio.run_coroutine_threadsafe') as run:
            instance.closing = True
            instance.device = mock.Mock()
            instance.read_ssh()
            instance.device.recv.assert_not_called()
            instance.closing = False
            instance.device.recv.side_effect = OSError('channel closed')
            instance.read_ssh()
        self.assertEqual(run.call_args_list, [mock.call(mock.sentinel.ending, mock.sentinel.loop)] * 2)

    async def test_the_telnet_reader(self):
        instance = consumer()
        instance.session = FakeSession()
        instance.telnet = TelnetFilter()
        instance.telnet_writer = mock.Mock()
        instance.end = mock.AsyncMock()

        class Reader:
            def __init__(self, chunks):
                self.chunks = list(chunks)

            async def read(self, n):
                chunk = self.chunks.pop(0)
                if isinstance(chunk, Exception):
                    raise chunk
                return chunk

        # Negotiation only (answered, nothing shown), then text only, then a failure
        await instance.read_telnet(Reader([bytes((IAC, WILL, 1)), b'login: ', OSError('reset')]))
        instance.telnet_writer.write.assert_called_once_with(bytes((IAC, DO, 1)))
        instance.send.assert_awaited_once_with(bytes_data=b'login: ')
        instance.end.assert_awaited_once_with('the device closed the connection')
        # Closing already: nothing is read
        instance.closing = True
        reader = Reader([])
        await instance.read_telnet(reader)
        self.assertEqual(instance.end.await_count, 2)

    async def test_the_watchdog_ends_at_the_length_limit(self):
        instance = consumer()
        instance.last_input = instance.opened = 0
        instance.end = mock.AsyncMock(side_effect=lambda reason: setattr(instance, 'closing', True))
        with mock.patch.object(consumers, 'CHECK_SECONDS', 0), mock.patch('console.consumers.time.monotonic', return_value=100), \
                override_settings(CONSOLE_IDLE_SECONDS=1000, CONSOLE_MAX_SECONDS=50):
            await instance.watch()
        self.assertIn('-hour limit was reached', instance.end.await_args.args[0])

    async def test_the_watchdog_checks_again_while_allowed(self):
        instance = consumer()
        instance.last_input = instance.opened = 100
        instance.still_allowed = mock.AsyncMock(side_effect=[True, False])
        instance.end = mock.AsyncMock(side_effect=lambda reason: setattr(instance, 'closing', True))
        with mock.patch.object(consumers, 'CHECK_SECONDS', 0), mock.patch('console.consumers.time.monotonic', return_value=100):
            await instance.watch()
        self.assertEqual(instance.still_allowed.await_count, 2)
        instance.end.assert_awaited_once_with('signed out, or no longer allowed a console')

    async def test_ending_twice_ends_once(self):
        instance = consumer()
        instance.closing = True
        await instance.end('again')
        instance.send.assert_not_awaited()

    async def test_disconnecting_before_anything_opened_and_closers_that_fail(self):
        instance = consumer()
        instance.closing = True
        instance.finish = mock.AsyncMock()
        instance.device = mock.Mock()
        instance.device.close.side_effect = OSError('already closed')
        instance.ssh = mock.Mock()
        await instance.disconnect(1000)
        instance.finish.assert_not_awaited()
        instance.ssh.close.assert_called_once_with()

    def test_sizes_and_reasons(self):
        self.assertEqual(ConsoleConsumer.size(10, 1000), (20, 200))
        self.assertEqual(ConsoleConsumer.size('x', 1), (120, 32))
        self.assertEqual(ConsoleConsumer.explain(TimeoutError()), 'TimeoutError')
        self.assertEqual(ConsoleConsumer.explain(OSError('first\nsecond')), 'first')

    def test_the_client_address_is_bounded(self):
        self.assertEqual(consumer().client_address(), '')
        self.assertEqual(consumer(client=(None, 0)).client_address(), '')
        self.assertEqual(len(consumer(client=('x' * 60, 1)).client_address()), 45)


class ConsumerDatabaseTests(TransactionTestCase):
    """The ticket claim's limit on open consoles, and the check that the
    user is still signed in and allowed. A TransactionTestCase, like the
    console tests in tests.py: the consumer closes database connections."""

    def setUp(self):
        self.user = make_user(30)

    async def test_at_most_so_many_consoles_at_once(self):
        def prepare():
            now = timezone.now()
            for number in range(3):
                router = make_router(name=f'open-{number}', address=f'192.0.2.{20 + number}')
                session, _ = ConsoleSession.open(self.user, router, 'ssh', 'backup', 'admin')
                ConsoleSession.objects.filter(pk=session.pk).update(connected_at=now)
            router = make_router(name='another', address='192.0.2.30')
            return ConsoleSession.open(self.user, router, 'ssh', 'backup', 'admin')[1]
        from asgiref.sync import sync_to_async
        ticket = await sync_to_async(prepare)()
        instance = consumer(user=self.user, client=('198.51.100.7', 1))
        with override_settings(CONSOLE_SESSIONS_PER_USER=3):
            session, problem = await instance.claim(ticket)
        self.assertIsNotNone(session)
        self.assertEqual(problem, 'At most 3 consoles can be open at once.')

    async def test_still_allowed_with_a_live_session(self):
        from asgiref.sync import sync_to_async

        def live_session():
            store = SessionStore()
            store.create()
            return store
        store = await sync_to_async(live_session)()
        self.assertTrue(await consumer(user=self.user, session=store).still_allowed())
        self.assertFalse(await consumer(user=self.user, session=None).still_allowed())

    async def test_finishing_without_a_session_writes_nothing(self):
        self.assertIsNone(await consumer().finish('nothing to record'))


class SessionDurationTests(TestCase):
    """A session's duration: none before it connected, to its end or to now."""

    def test_duration(self):
        session, _ = ConsoleSession.open(make_user(30), make_router(), 'ssh', 'backup', 'admin')
        self.assertIsNone(session.duration)
        start = timezone.now() - timezone.timedelta(minutes=5)
        session.connected_at = start
        self.assertGreaterEqual(session.duration, timezone.timedelta(minutes=5))
        session.ended_at = start + timezone.timedelta(minutes=2)
        self.assertEqual(session.duration, timezone.timedelta(minutes=2))


class FormRefusalTests(TestCase):
    """The console form refuses an unknown transport or account, and the
    backup account where the device stores none."""

    def setUp(self):
        self.client.force_login(make_user(30))

    def post(self, router, **data):
        return self.client.post('/router/console/', {'action': 'open', 'router': str(router.uuid), **data})

    def test_unknown_transport_and_account(self):
        router = make_router(password='test-only-password')
        self.assertContains(self.post(router, transport='carrier-pigeon'), 'Choose SSH or Telnet.')
        self.assertContains(self.post(router, transport='ssh', account='nobody'), 'Choose an account.')
        self.assertFalse(ConsoleSession.objects.exists())

    def test_no_backup_account_stored(self):
        router = make_router(password='')
        self.assertContains(self.post(router, transport='ssh', account='backup'), 'no backup account stored')
        self.assertFalse(ConsoleSession.objects.exists())
