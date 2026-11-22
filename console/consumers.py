"""The console's WebSocket: a terminal in the browser bridged to SSH or Telnet.

Opening it takes, in order: a signed-in session (the session cookie), a
request from this site (same origin), and a one-time ticket from the console
form - sent as the first message, never in the URL - that belongs to this
user, has not expired and was never used. The user's level is checked again,
and again every CHECK_SECONDS while the session lasts: signing out, or losing
the level, closes it.

Credentials: the backup account is decrypted here and never reaches the
browser; another account's password arrives once in the first message and goes
straight into the SSH login - it is not stored and not logged. Over Telnet no
stored credential is ever sent: the user logs in at the device's own prompt.

Nothing that is typed or shown is logged or stored; only how the session went.
"""
import asyncio
import json
import logging
import threading
import time
from urllib.parse import urlsplit

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.utils import timezone

from . import network

logger = logging.getLogger(__name__)

FIRST_MESSAGE_SECONDS = 15
# Signing out, or the sign-in session expiring, closes a console within this
# many seconds; a lost browser is found by uvicorn's WebSocket ping (init.sh)
CHECK_SECONDS = 5
MAX_FRAME = 64 * 1024
MAX_PASSWORD = 1024

# Close codes the page explains
CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_FAILED = 4500


def connect_to_ssh(*args, **kwargs):
    # Imported lazily so tests can replace it
    from routerlib.functions import connect_to_ssh as connect
    return connect(*args, **kwargs)


class TelnetFilter:
    """The minimal Telnet the console needs: answers the device's option
    negotiation (accepting echo and suppress-go-ahead from the device, refusing
    everything else) and hands the rest on as terminal data."""
    IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
    ECHO, SGA = 1, 3

    def __init__(self):
        self.pending = b''

    def feed(self, data):
        data = self.pending + data
        self.pending = b''
        out, replies, i = bytearray(), bytearray(), 0
        while i < len(data):
            byte = data[i]
            if byte != self.IAC:
                out.append(byte)
                i += 1
                continue
            if i + 1 >= len(data):
                self.pending = data[i:]
                break
            command = data[i + 1]
            if command == self.IAC:
                out.append(self.IAC)
                i += 2
            elif command in (self.DO, self.DONT, self.WILL, self.WONT):
                if i + 2 >= len(data):
                    self.pending = data[i:]
                    break
                option = data[i + 2]
                if command == self.WILL:
                    replies += bytes((self.IAC, self.DO if option in (self.ECHO, self.SGA) else self.DONT, option))
                elif command == self.DO:
                    replies += bytes((self.IAC, self.WILL if option == self.SGA else self.WONT, option))
                i += 3
            elif command == self.SB:
                end = data.find(bytes((self.IAC, self.SE)), i + 2)
                if end < 0:
                    self.pending = data[i:]
                    break
                i = end + 2
            else:
                i += 2
        return bytes(out), bytes(replies)

    @staticmethod
    def escape(data):
        return data.replace(b'\xff', b'\xff\xff')


class ConsoleConsumer(AsyncWebsocketConsumer):
    session = None
    device = None          # paramiko channel, or None
    ssh = None
    telnet_writer = None
    closing = False

    # --- opening ---------------------------------------------------------------

    def same_origin(self):
        headers = dict(self.scope.get('headers') or [])
        origin = headers.get(b'origin', b'').decode('latin-1')
        host = headers.get(b'host', b'').decode('latin-1')
        return bool(origin and host) and urlsplit(origin).netloc.lower() == host.lower()

    async def connect(self):
        user = self.scope.get('user')
        if not self.same_origin():
            await self.close(code=CLOSE_FORBIDDEN)
            return
        if not user or not user.is_authenticated:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return
        await self.accept()
        self.opened = time.monotonic()
        self.last_input = time.monotonic()
        self.loop = asyncio.get_running_loop()
        self.first_message_timer = asyncio.create_task(self.expect_first_message())

    async def expect_first_message(self):
        await asyncio.sleep(FIRST_MESSAGE_SECONDS)
        if not self.session:
            await self.status('No ticket was presented in time.')
            await self.close(code=CLOSE_FORBIDDEN)

    async def status(self, text, level='info'):
        await self.send(text_data=json.dumps({'type': 'status', 'level': level, 'text': text}))

    @database_sync_to_async
    def claim(self, ticket):
        """The session for this ticket, claimed once - or None."""
        from user_manager.models import UserAcl
        from .models import ConsoleSession, hash_ticket
        user = self.scope['user']
        if not UserAcl.objects.filter(user=user, user_level__gte=settings.CONSOLE_LEVEL).exists():
            return None, 'Your user level does not allow a console.'
        now = timezone.now()
        claimed = ConsoleSession.objects.filter(
            ticket_hash=hash_ticket(ticket), user=user, connected_at__isnull=True,
            ticket_expires__gt=now).update(connected_at=now, client_address=self.client_address())
        if claimed != 1:
            return None, 'This console link is not valid any more. Open the console again.'
        session = ConsoleSession.objects.select_related('router', 'router__ssh_key').get(ticket_hash=hash_ticket(ticket))
        router = session.router
        if router is None or router.router_type == 'monitoring':
            return session, 'This device can not be reached by a console.'
        if session.transport == 'telnet' and not router.console_telnet:
            return session, 'Telnet is not allowed for this device.'
        stale = now - timezone.timedelta(seconds=settings.CONSOLE_MAX_SECONDS)
        open_sessions = ConsoleSession.objects.filter(user=user, ended_at__isnull=True, connected_at__gt=stale) \
            .exclude(pk=session.pk)
        if open_sessions.filter(router=router).exists():
            return session, 'You already have a console open on this device.'
        if open_sessions.count() >= settings.CONSOLE_SESSIONS_PER_USER:
            return session, f'At most {settings.CONSOLE_SESSIONS_PER_USER} consoles can be open at once.'
        return session, None

    def client_address(self):
        client = self.scope.get('client') or ('', 0)
        return (client[0] or '')[:45]

    async def receive(self, text_data=None, bytes_data=None):
        data = bytes_data if bytes_data is not None else (text_data or '').encode()
        if len(data) > MAX_FRAME:
            await self.close(code=CLOSE_FORBIDDEN)
            return
        if not self.session:
            await self.start(data)
            return
        if text_data is not None:
            try:
                message = json.loads(text_data)
            except ValueError:
                return
            if message.get('type') == 'resize':
                self.resize(message.get('cols'), message.get('rows'))
                return
            if message.get('type') == 'input':
                data = str(message.get('data', '')).encode('utf-8')
            else:
                return
        self.last_input = time.monotonic()
        await self.to_device(data)

    async def start(self, data):
        try:
            first = json.loads(data.decode('utf-8'))
            ticket = str(first['ticket'])
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            await self.close(code=CLOSE_FORBIDDEN)
            return
        session, problem = await self.claim(ticket)
        if session is None:
            await self.status(problem, 'error')
            await self.close(code=CLOSE_FORBIDDEN)
            return
        self.session = session
        self.first_message_timer.cancel()
        if problem:
            await self.status(problem, 'error')
            await self.finish(problem)
            await self.close(code=CLOSE_FORBIDDEN)
            return
        cols, rows = self.size(first.get('cols'), first.get('rows'))
        password = first.get('password')
        password = str(password)[:MAX_PASSWORD] if password is not None else None
        first = data = None   # nothing of the first message is kept
        try:
            if session.transport == 'ssh':
                await self.open_ssh(cols, rows, password)
            else:
                await self.open_telnet()
        except Exception as error:   # noqa: BLE001 - the reason is shown, never a traceback
            reason = self.explain(error)
            await self.status(f'Could not connect: {reason}', 'error')
            await self.finish(f'could not connect: {reason}')
            await self.close(code=CLOSE_FAILED)
            return
        password = None
        await self.status(f'Connected to {session.router_name} over {session.get_transport_display()}.', 'success')
        self.watchdog = asyncio.create_task(self.watch())

    @staticmethod
    def explain(error):
        text = str(error) or error.__class__.__name__
        # The host-key refusal already says what happened and what to do
        return text.splitlines()[0][:160]

    @staticmethod
    def size(cols, rows):
        try:
            return max(20, min(int(cols), 400)), max(5, min(int(rows), 200))
        except (TypeError, ValueError):
            return 120, 32

    # --- SSH -------------------------------------------------------------------

    async def open_ssh(self, cols, rows, password):
        router = self.session.router
        await asyncio.to_thread(network.check, router.address, router.port)
        if self.session.account == 'backup':
            username, secret, key = router.username, router.password, router.ssh_key
        else:
            username, secret, key = self.session.username, password or '', None
        await self.status(f'Connecting to {router.address}:{router.port} as {username}...')
        self.ssh = await asyncio.to_thread(connect_to_ssh, router.address, router.port, username, secret, key)
        secret = password = None
        channel = await asyncio.to_thread(self.ssh.invoke_shell, term='xterm-256color', width=cols, height=rows)
        self.device = channel
        threading.Thread(target=self.read_ssh, daemon=True).start()

    def read_ssh(self):
        """Device to browser, on a thread of its own: paramiko blocks."""
        try:
            while not self.closing:
                data = self.device.recv(32768)
                if not data:
                    break
                asyncio.run_coroutine_threadsafe(self.from_device(data), self.loop)
        except Exception:   # noqa: BLE001 - a closed channel ends the session
            pass
        asyncio.run_coroutine_threadsafe(self.end('the device closed the connection'), self.loop)

    def resize(self, cols, rows):
        cols, rows = self.size(cols, rows)
        if self.device is not None:
            try:
                self.device.resize_pty(width=cols, height=rows)
            except Exception:   # noqa: BLE001
                pass

    # --- Telnet ----------------------------------------------------------------

    async def open_telnet(self):
        router = self.session.router
        await self.status(f'Connecting to {router.address}:{router.telnet_port} over Telnet - unencrypted. '
                          'Log in at the device prompt.', 'warning')
        # The checked address is the one connected to
        address = await asyncio.to_thread(network.check, router.address, router.telnet_port)
        reader, self.telnet_writer = await asyncio.wait_for(
            asyncio.open_connection(address, router.telnet_port), timeout=10)
        self.telnet = TelnetFilter()
        self.telnet_reader = asyncio.create_task(self.read_telnet(reader))

    async def read_telnet(self, reader):
        try:
            while not self.closing:
                data = await reader.read(32768)
                if not data:
                    break
                text, replies = self.telnet.feed(data)
                if replies:
                    self.telnet_writer.write(replies)
                if text:
                    await self.from_device(text)
        except Exception:   # noqa: BLE001
            pass
        await self.end('the device closed the connection')

    # --- the bridge ------------------------------------------------------------

    async def to_device(self, data):
        if self.session:
            self.session.bytes_to_device += len(data)
        if self.device is not None:
            await asyncio.to_thread(self.device.sendall, data)
        elif self.telnet_writer is not None:
            self.telnet_writer.write(TelnetFilter.escape(data))
            await self.telnet_writer.drain()

    async def from_device(self, data):
        if self.closing:
            return
        self.session.bytes_from_device += len(data)
        await self.send(bytes_data=data)

    async def watch(self):
        """Idle and length limits; the user still signed in and allowed."""
        while not self.closing:
            await asyncio.sleep(min(CHECK_SECONDS, settings.CONSOLE_IDLE_SECONDS))
            now = time.monotonic()
            if now - self.last_input > settings.CONSOLE_IDLE_SECONDS:
                await self.end(f'no input for {settings.CONSOLE_IDLE_SECONDS // 60} minutes')
            elif now - self.opened > settings.CONSOLE_MAX_SECONDS:
                await self.end(f'the {settings.CONSOLE_MAX_SECONDS // 3600}-hour limit was reached')
            elif not await self.still_allowed():
                await self.end('signed out, or no longer allowed a console')

    @database_sync_to_async
    def still_allowed(self):
        from django.contrib.sessions.backends.db import SessionStore
        from user_manager.models import UserAcl
        session = self.scope.get('session')
        key = getattr(session, 'session_key', None)
        user = self.scope.get('user')
        if not key or not SessionStore().exists(key) or not user.is_active:
            return False
        return UserAcl.objects.filter(user=user, user_level__gte=settings.CONSOLE_LEVEL).exists()

    # --- closing ---------------------------------------------------------------

    async def end(self, reason):
        if self.closing:
            return
        self.closing = True
        await self.status(f'Session ended: {reason}.', 'warning')
        await self.finish(reason)
        await self.close()

    @database_sync_to_async
    def finish(self, reason):
        from .models import ConsoleSession
        if not self.session:
            return
        # update(), not save(): only how the session went is written
        ConsoleSession.objects.filter(pk=self.session.pk, ended_at__isnull=True).update(
            ended_at=timezone.now(), end_reason=reason[:120],
            bytes_to_device=self.session.bytes_to_device, bytes_from_device=self.session.bytes_from_device)

    async def disconnect(self, code):
        if getattr(self, 'first_message_timer', None):
            self.first_message_timer.cancel()
        if getattr(self, 'watchdog', None):
            self.watchdog.cancel()
        if not self.closing:
            self.closing = True
            await self.finish('the browser closed the console')
        for close in (getattr(self.device, 'close', None), getattr(self.ssh, 'close', None),
                      getattr(self.telnet_writer, 'close', None)):
            if close:
                try:
                    close()
                except Exception:   # noqa: BLE001
                    pass
