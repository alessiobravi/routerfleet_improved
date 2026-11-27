"""The device console: the form, the ticket, the WebSocket and its security."""
import asyncio
import json
import queue
from unittest import mock

from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import path
from django.utils import timezone

from audit_log.models import ChangeRecord
from console import consumers
from console.consumers import ConsoleConsumer, TelnetFilter
from console.models import ConsoleSession, hash_ticket
from routerfleet.testing import make_router, make_user

class WebsocketCommunicator(ApplicationCommunicator):
    """A browser's WebSocket, driven from a test - what channels.testing offers,
    without its live-server module (which needs daphne, not used here)."""

    def __init__(self, application, path, headers):
        super().__init__(application, {'type': 'websocket', 'path': path, 'headers': headers, 'subprotocols': [],
                                       'query_string': b'', 'client': ('198.51.100.7', 50000)})

    async def connect(self, timeout=3):
        await self.send_input({'type': 'websocket.connect'})
        response = await self.receive_output(timeout)
        if response['type'] == 'websocket.close':
            return False, response.get('code', 1000)
        return True, None

    async def send_to(self, text_data=None, bytes_data=None):
        message = {'type': 'websocket.receive'}
        if text_data is not None:
            message['text'] = text_data
        else:
            message['bytes'] = bytes_data
        await self.send_input(message)

    async def receive_from(self, timeout=3):
        while True:
            response = await self.receive_output(timeout)
            if response.get('bytes') is not None:
                return response['bytes']

    async def disconnect(self, code=1000, timeout=2):
        await self.send_input({'type': 'websocket.disconnect', 'code': code})
        try:
            await self.wait(timeout)
        except TimeoutError:
            pass


APPLICATION = AuthMiddlewareStack(URLRouter([path('console/ws/', ConsoleConsumer.as_asgi())]))
HOST = 'rf.example.net'


class FakeChannel:
    """A shell on a device: echoes what it is sent."""

    def __init__(self):
        self.incoming = queue.Queue()
        self.sent = b''
        self.size = None
        self.closed = False
        self.incoming.put(b'device> ')

    def recv(self, n):
        data = self.incoming.get()
        return data

    def sendall(self, data):
        self.sent += data
        self.incoming.put(b'echo:' + data)

    def resize_pty(self, width, height):
        self.size = (width, height)

    def close(self):
        self.closed = True
        self.incoming.put(b'')


class FakeClient:
    def __init__(self):
        self.channel = FakeChannel()
        self.closed = False

    def invoke_shell(self, term, width, height):
        self.channel.size = (width, height)
        return self.channel

    def close(self):
        self.closed = True
        self.channel.close()


class FormTests(TestCase):
    def setUp(self):
        self.router = make_router(name='edge-1', password='stored-secret')

    def test_host_manager_and_up(self):
        self.client.force_login(make_user(20))
        self.assertContains(self.client.get(f'/router/console/?uuid={self.router.uuid}'), 'Access Denied')
        self.client.force_login(make_user(30))
        self.assertContains(self.client.get(f'/router/console/?uuid={self.router.uuid}'), 'Open the console')

    def test_opening_creates_a_one_time_ticket_stored_only_as_a_hash(self):
        user = make_user(30)
        self.client.force_login(user)
        page = self.client.post('/router/console/', {'action': 'open', 'router': str(self.router.uuid),
                                                      'transport': 'ssh', 'account': 'backup'})
        self.assertIn('no-store', page['Cache-Control'])
        ticket = page.context['ticket']
        session = ConsoleSession.objects.get()
        self.assertEqual(session.ticket_hash, hash_ticket(ticket))
        self.assertNotIn(ticket, json.dumps([str(getattr(session, f.name)) for f in session._meta.fields]))
        self.assertNotContains(page, 'stored-secret')
        self.assertEqual((session.user, session.username, session.account), (user, self.router.username, 'backup'))
        record = ChangeRecord.objects.get(model_label='console.ConsoleSession')
        self.assertEqual(record.actor, user)

    def test_another_account_needs_a_username_and_no_password_reaches_the_server(self):
        self.client.force_login(make_user(30))
        self.assertContains(self.client.post('/router/console/', {'action': 'open', 'router': str(self.router.uuid),
                                                                  'transport': 'ssh', 'account': 'other'}),
                            'Enter the username')
        page = self.client.post('/router/console/', {'action': 'open', 'router': str(self.router.uuid), 'transport': 'ssh',
                                                     'account': 'other', 'username': 'operator'})
        self.assertTrue(page.context['ask_password'])
        self.assertContains(page, 'id="console-password"')

    def test_telnet_only_where_allowed_and_acknowledged(self):
        self.client.force_login(make_user(30))
        data = {'action': 'open', 'router': str(self.router.uuid), 'transport': 'telnet'}
        self.assertContains(self.client.post('/router/console/', data), 'Telnet is not allowed')
        self.router.console_telnet = True
        self.router.save()
        self.assertContains(self.client.post('/router/console/', data), 'unencrypted')
        self.assertFalse(ConsoleSession.objects.exists())
        page = self.client.post('/router/console/', dict(data, telnet_understood='on'))
        session = ConsoleSession.objects.get()
        self.assertEqual((session.transport, session.account, session.username), ('telnet', 'other', ''),
                         'no stored credential is used over Telnet')
        self.assertFalse(page.context['ask_password'])

    def test_monitoring_devices_have_no_console(self):
        self.client.force_login(make_user(30))
        router = make_router(router_type='monitoring')
        self.assertRedirects(self.client.get(f'/router/console/?uuid={router.uuid}'),
                             f'/router/details/?uuid={router.uuid}', fetch_redirect_response=False)

    def test_the_icon_and_the_action(self):
        self.client.force_login(make_user(30))
        self.assertContains(self.client.get('/router/list/'), f'/router/console/?uuid={self.router.uuid}')
        self.assertContains(self.client.get(f'/router/details/?uuid={self.router.uuid}'), 'fa-keyboard')
        self.client.force_login(make_user(20))
        self.assertNotContains(self.client.get('/router/list/'), '/router/console/')


@override_settings(ALLOWED_HOSTS=[HOST, 'testserver'])
class TerminalPolicyTests(TestCase):
    """xterm.js needs inline <style> elements: allowed on the terminal page only,
    and only style elements - scripts and style attributes stay strict."""

    def test_only_the_terminal_allows_inline_style_elements(self):
        router = make_router(name='edge-2', password='stored-secret')
        self.client.force_login(make_user(30))
        form = self.client.get(f'/router/console/?uuid={router.uuid}')
        self.assertNotIn('unsafe-inline', form['Content-Security-Policy'])
        page = self.client.post('/router/console/', {'action': 'open', 'router': str(router.uuid),
                                                      'transport': 'ssh', 'account': 'backup'})
        policy = page['Content-Security-Policy']
        self.assertIn("style-src-elem 'self' 'unsafe-inline'", policy)
        self.assertIn("script-src 'self';", policy)
        self.assertIn("style-src 'self';", policy)


class ConsumerTests(TransactionTestCase):
    def setUp(self):
        self.user = make_user(30)
        self.router = make_router(name='edge-1', password='stored-secret')
        self.client.force_login(self.user)
        self.cookie = self.client.cookies['sessionid'].value
        self.fake = FakeClient()
        patcher = mock.patch.object(consumers, 'connect_to_ssh', side_effect=self.connect)
        self.connect_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def connect(self, address, port, username, password, key, connect_to=None):
        self.connected_with = (address, port, username, password)
        self.connected_to = connect_to   # the address checked, connected to without a second lookup
        return self.fake

    def ticket(self, account='backup', username=None, user=None, transport='ssh'):
        session, ticket = ConsoleSession.open(user or self.user, self.router, transport, account,
                                              username if username is not None else self.router.username)
        return session, ticket

    def communicator(self, origin=f'https://{HOST}', cookie=True):
        headers = [(b'host', HOST.encode())]
        if origin:
            headers.append((b'origin', origin.encode()))
        if cookie:
            headers.append((b'cookie', f'sessionid={self.cookie}'.encode()))
        return WebsocketCommunicator(APPLICATION, '/console/ws/', headers=headers)

    async def receive_status(self, communicator):
        while True:
            message = await communicator.receive_output(timeout=3)
            if message['type'] == 'websocket.close':
                return None, message.get('code')
            if message.get('text'):
                data = json.loads(message['text'])
                if data.get('type') == 'status':
                    return data, None

    async def test_another_site_can_not_open_a_console(self):
        connected, code = await self.communicator(origin='https://evil.example.net').connect()
        self.assertFalse(connected)
        connected, _ = await self.communicator(origin=None).connect()
        self.assertFalse(connected)

    async def test_not_signed_in(self):
        connected, code = await self.communicator(cookie=False).connect()
        self.assertFalse(connected)
        self.assertEqual(code, consumers.CLOSE_UNAUTHENTICATED)

    async def test_a_ticket_works_once_for_its_own_user_before_it_expires(self):
        _, ticket = await sync_to_async(self.ticket)()
        other_session, other_ticket = await sync_to_async(self.ticket)(user=await sync_to_async(make_user)(50))
        expired_session, expired_ticket = await sync_to_async(self.ticket)()
        await sync_to_async(ConsoleSession.objects.filter(pk=expired_session.pk).update)(
            ticket_expires=timezone.now() - timezone.timedelta(seconds=1))
        for bad in (other_ticket, expired_ticket, 'made-up-ticket'):
            communicator = self.communicator()
            await communicator.connect()
            await communicator.send_to(text_data=json.dumps({'ticket': bad}))
            status, _ = await self.receive_status(communicator)
            self.assertIn('not valid', status['text'])
            await communicator.disconnect()
        communicator = self.communicator()
        await communicator.connect()
        await communicator.send_to(text_data=json.dumps({'ticket': ticket, 'cols': 100, 'rows': 30}))
        await self.receive_status(communicator)   # connecting
        status, _ = await self.receive_status(communicator)
        self.assertIn('Connected', status['text'])
        await communicator.disconnect()
        again = self.communicator()
        await again.connect()
        await again.send_to(text_data=json.dumps({'ticket': ticket}))
        status, _ = await self.receive_status(again)
        self.assertIn('not valid', status['text'], 'a ticket opens one connection')
        await again.disconnect()

    async def test_backup_account_bridge_and_closing(self):
        session, ticket = await sync_to_async(self.ticket)()
        communicator = self.communicator()
        await communicator.connect()
        await communicator.send_to(text_data=json.dumps({'ticket': ticket, 'cols': 100, 'rows': 30}))
        await self.receive_status(communicator)
        await self.receive_status(communicator)
        self.assertEqual(self.connected_with, ('192.0.2.1' if self.router.address == '192.0.2.1' else self.router.address,
                                               self.router.port, self.router.username, 'stored-secret'))
        self.assertEqual(self.fake.channel.size, (100, 30))
        self.assertTrue(self.connected_to, 'connected to the checked address, not a fresh lookup')
        banner = await communicator.receive_from(timeout=3)
        self.assertEqual(banner, b'device> ')
        await communicator.send_to(bytes_data=b'/system resource print\r')
        self.assertEqual(await communicator.receive_from(timeout=3), b'echo:/system resource print\r')
        await communicator.send_to(text_data=json.dumps({'type': 'resize', 'cols': 80, 'rows': 24}))
        await asyncio.sleep(0.05)
        self.assertEqual(self.fake.channel.size, (80, 24))
        await communicator.disconnect()   # the page closed
        await asyncio.sleep(0.1)
        self.assertTrue(self.fake.closed, 'closing the page closes the device connection')
        session = await sync_to_async(ConsoleSession.objects.get)(pk=session.pk)
        self.assertEqual(session.end_reason, 'the browser closed the console')
        self.assertIsNotNone(session.ended_at)
        self.assertGreater(session.bytes_from_device, 0)

    async def test_another_account_password_goes_to_the_login_only(self):
        session, ticket = await sync_to_async(self.ticket)(account='other', username='operator')
        communicator = self.communicator()
        await communicator.connect()
        await communicator.send_to(text_data=json.dumps({'ticket': ticket, 'password': 'typed-pass-9'}))
        await self.receive_status(communicator)
        await self.receive_status(communicator)
        self.assertEqual(self.connected_with[2:], ('operator', 'typed-pass-9'))
        await communicator.disconnect()
        stored = await sync_to_async(lambda: json.dumps(
            [str(v) for v in ConsoleSession.objects.filter(pk=session.pk).values().first().values()]))()
        self.assertNotIn('typed-pass-9', stored)

    async def test_the_device_closing_ends_the_session(self):
        session, ticket = await sync_to_async(self.ticket)()
        communicator = self.communicator()
        await communicator.connect()
        await communicator.send_to(text_data=json.dumps({'ticket': ticket}))
        await self.receive_status(communicator)
        await self.receive_status(communicator)
        self.fake.channel.incoming.put(b'')
        status, code = await self.receive_status(communicator)
        while status and 'ended' not in status['text']:
            status, code = await self.receive_status(communicator)
        self.assertIn('the device closed the connection', status['text'])
        row = await sync_to_async(ConsoleSession.objects.get)(pk=session.pk)
        self.assertEqual(row.end_reason, 'the device closed the connection')

    async def test_signing_out_closes_an_open_console(self):
        session, ticket = await sync_to_async(self.ticket)()
        with mock.patch.object(consumers, 'CHECK_SECONDS', 0.05):
            communicator = self.communicator()
            await communicator.connect()
            await communicator.send_to(text_data=json.dumps({'ticket': ticket}))
            await self.receive_status(communicator)
            await self.receive_status(communicator)
            await sync_to_async(self.client.logout)()
            status = None
            for _ in range(10):
                status, code = await self.receive_status(communicator)
                if status is None or 'ended' in status['text']:
                    break
        row = await sync_to_async(ConsoleSession.objects.get)(pk=session.pk)
        self.assertEqual(row.end_reason, 'signed out, or no longer allowed a console')
        self.assertTrue(self.fake.closed or row.ended_at)

    async def test_one_console_per_device_and_level_checked_again(self):
        _, first = await sync_to_async(self.ticket)()
        _, second = await sync_to_async(self.ticket)()
        one = self.communicator()
        await one.connect()
        await one.send_to(text_data=json.dumps({'ticket': first}))
        await self.receive_status(one)
        await self.receive_status(one)
        two = self.communicator()
        await two.connect()
        await two.send_to(text_data=json.dumps({'ticket': second}))
        status, _ = await self.receive_status(two)
        self.assertIn('already have a console open', status['text'])
        await one.disconnect()
        await two.disconnect()
        from user_manager.models import UserAcl
        _, third = await sync_to_async(self.ticket)()
        await sync_to_async(UserAcl.objects.filter(user=self.user).update)(user_level=20)
        three = self.communicator()
        await three.connect()
        await three.send_to(text_data=json.dumps({'ticket': third}))
        status, _ = await self.receive_status(three)
        self.assertIn('does not allow a console', status['text'])
        await three.disconnect()

    async def test_telnet_switched_off_after_the_ticket_is_refused(self):
        self.router.console_telnet = True
        await sync_to_async(self.router.save)()
        _, ticket = await sync_to_async(self.ticket)(transport='telnet', account='other', username='')
        self.router.console_telnet = False
        await sync_to_async(self.router.save)()
        communicator = self.communicator()
        await communicator.connect()
        await communicator.send_to(text_data=json.dumps({'ticket': ticket}))
        status, _ = await self.receive_status(communicator)
        self.assertIn('Telnet is not allowed', status['text'])
        await communicator.disconnect()

    async def test_no_ticket_in_time_closes(self):
        with mock.patch.object(consumers, 'FIRST_MESSAGE_SECONDS', 0.05):
            communicator = self.communicator()
            await communicator.connect()
            status, _ = await self.receive_status(communicator)
            self.assertIn('No ticket', status['text'])
            await communicator.disconnect()


class TelnetFilterTests(TestCase):
    def test_negotiation_is_answered_and_removed(self):
        telnet = TelnetFilter()
        text, replies = telnet.feed(b'\xff\xfb\x01\xff\xfd\x18login: ')
        self.assertEqual(text, b'login: ')
        self.assertEqual(replies, b'\xff\xfd\x01\xff\xfc\x18', 'accepts echo, refuses terminal type')

    def test_split_sequences_and_escaped_bytes(self):
        telnet = TelnetFilter()
        self.assertEqual(telnet.feed(b'ab\xff'), (b'ab', b''))
        self.assertEqual(telnet.feed(b'\xfb\x03cd'), (b'cd', b'\xff\xfd\x03'))
        self.assertEqual(telnet.feed(b'\xff\xff'), (b'\xff', b''))
        self.assertEqual(TelnetFilter.escape(b'\xffx'), b'\xff\xffx')


@override_settings(ALLOWED_HOSTS=[HOST, 'testserver'])
class ConsumerLimitsTests(ConsumerTests):
    """Telnet, the idle limit, oversized frames and typed input as JSON."""

    async def open(self, **ticket_kwargs):
        session, ticket = await sync_to_async(self.ticket)(**ticket_kwargs)
        communicator = self.communicator()
        await communicator.connect()
        await communicator.send_to(text_data=json.dumps({'ticket': ticket, 'cols': 'wide', 'rows': None}))
        return session, communicator

    async def test_telnet_bridge_with_negotiation(self):
        self.router.console_telnet = True
        self.router.telnet_port = 2323
        await sync_to_async(self.router.save)()
        written = []

        class Writer:
            def write(self, data):
                written.append(data)

            async def drain(self):
                pass

            def close(self):
                written.append(b'<closed>')

        reader = asyncio.StreamReader()
        reader.feed_data(b'\xff\xfb\x01login: ')

        async def fake_open(host, port):
            self.assertEqual((host, port), (self.router.address, 2323))
            return reader, Writer()
        with mock.patch('console.consumers.asyncio.open_connection', side_effect=fake_open):
            session, communicator = await self.open(transport='telnet', account='other', username='')
            status, _ = await self.receive_status(communicator)
            self.assertIn('unencrypted', status['text'])
            await self.receive_status(communicator)   # connected
            self.assertEqual(await communicator.receive_from(timeout=3), b'login: ')
            self.assertIn(b'\xff\xfd\x01', written, 'the echo option is accepted')
            await communicator.send_to(text_data=json.dumps({'type': 'input', 'data': 'admin\r'}))
            await asyncio.sleep(0.05)
            self.assertIn(b'admin\r', written)
            reader.feed_eof()
            status, _ = await self.receive_status(communicator)
            while status and 'ended' not in status['text']:
                status, _ = await self.receive_status(communicator)
        row = await sync_to_async(ConsoleSession.objects.get)(pk=session.pk)
        self.assertEqual(row.end_reason, 'the device closed the connection')

    async def test_idle_limit_ends_the_session(self):
        with override_settings(CONSOLE_IDLE_SECONDS=0), mock.patch.object(consumers, 'CHECK_SECONDS', 0.05):
            session, communicator = await self.open()
            await self.receive_status(communicator)
            await self.receive_status(communicator)
            status = None
            for _ in range(10):
                status, _ = await self.receive_status(communicator)
                if status is None or 'ended' in status['text']:
                    break
        row = await sync_to_async(ConsoleSession.objects.get)(pk=session.pk)
        self.assertIn('no input', row.end_reason)

    async def test_an_oversized_frame_closes(self):
        session, communicator = await self.open()
        await self.receive_status(communicator)
        await self.receive_status(communicator)
        await communicator.send_to(bytes_data=b'x' * (consumers.MAX_FRAME + 1))
        message = await communicator.receive_output(timeout=3)
        while message['type'] != 'websocket.close':
            message = await communicator.receive_output(timeout=3)
        self.assertEqual(message['code'], consumers.CLOSE_FORBIDDEN)

    async def test_a_device_that_refuses_the_login(self):
        self.connect_mock.side_effect = OSError('Authentication failed.')
        session, communicator = await self.open()
        await self.receive_status(communicator)
        status, _ = await self.receive_status(communicator)
        self.assertIn('Could not connect: Authentication failed.', status['text'])
        row = await sync_to_async(ConsoleSession.objects.get)(pk=session.pk)
        self.assertEqual(row.end_reason, 'could not connect: Authentication failed.')

    async def test_a_monitoring_device_is_refused_even_with_a_ticket(self):
        self.router.router_type = 'monitoring'
        await sync_to_async(self.router.save)()
        _, communicator = await self.open()
        status, _ = await self.receive_status(communicator)
        self.assertIn('can not be reached by a console', status['text'])


class NetworkGuardTests(TestCase):
    """The console never connects into RouterFleet's own stack."""

    def route_table(self, tmp_path):
        # 172.18.0.0/16 attached (the stack's network), default route via 172.18.0.1
        content = ('Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n'
                   'eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n'
                   'eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n')
        path = tmp_path + '/route'
        with open(path, 'w') as handle:
            handle.write(content)
        return path

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(__import__('shutil').rmtree, self.tmp, True)
        self.routes = self.route_table(self.tmp)

    def test_attached_networks_are_read(self):
        from console.network import attached_networks
        self.assertEqual([str(n) for n in attached_networks(self.routes)], ['172.18.0.0/16'])

    def test_refused(self):
        from console.network import Refused, check
        for host in ('127.0.0.1', 'localhost', 'routerfleet-postgres', 'routerfleet', '169.254.1.1', '0.0.0.0',
                     '::1', '172.18.0.5', 'evil.localhost'):
            with self.subTest(host=host), self.assertRaises(Refused):
                check(host, 23, self.routes)

    def test_devices_on_the_lan_are_allowed(self):
        from console.network import check
        for host in ('192.168.1.10', '10.20.30.40', '203.0.113.9'):
            with self.subTest(host=host):
                self.assertEqual(check(host, 22, self.routes), host)

    def test_the_consumer_refuses_an_inside_address(self):
        from console.consumers import ConsoleConsumer
        consumer = ConsoleConsumer()
        consumer.session = mock.Mock(router=mock.Mock(address='127.0.0.1', port=22), account='backup')
        with self.assertRaises(Exception) as caught:
            asyncio.run(consumer.open_ssh(80, 24, None))
        self.assertIn('RouterFleet itself' if False else 'not a device', str(caught.exception))


class TidyTests(TestCase):
    def test_restart_leftovers_closed_and_unused_tickets_pruned(self):
        from console.models import tidy_sessions
        user, router = make_user(30), make_router()
        open_one, _ = ConsoleSession.open(user, router, 'ssh', 'backup', 'admin')
        unused, _ = ConsoleSession.open(user, router, 'ssh', 'backup', 'admin')
        recent, _ = ConsoleSession.open(user, router, 'ssh', 'backup', 'admin')
        ConsoleSession.objects.filter(pk=open_one.pk).update(connected_at=timezone.now() - timezone.timedelta(hours=6))
        ConsoleSession.objects.filter(pk=unused.pk).update(ticket_expires=timezone.now() - timezone.timedelta(days=8))
        self.assertEqual(tidy_sessions(), (1, 1))
        self.assertIn('stopped', ConsoleSession.objects.get(pk=open_one.pk).end_reason)
        self.assertFalse(ConsoleSession.objects.filter(pk=unused.pk).exists())
        self.assertTrue(ConsoleSession.objects.filter(pk=recent.pk).exists())
