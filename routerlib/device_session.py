"""A connection to a device, as its device type says to talk to it.

'exec' types (RouterOS, OpenWrt, Linux) get one SSH channel per command - what
RouterFleet always did. 'shell' types (Cisco, Juniper, Huawei) need a terminal:
one interactive shell, the prompt waited for after every command, paging turned
off once after login, and an optional enable step.

Both look the same to the code that uses them: exec_command(command) returns
(stdin, stdout, stderr) whose stdout has read() and channel.recv_exit_status(),
as paramiko's does, and get_transport() serves SCP. The host key is checked as
for every connection (routerlib/host_keys.py).
"""
import re
import time

from routerlib.functions import connect_to_ssh

ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r')
MORE = re.compile(r'(--More--|---- More ----|-- More --|<--- More --->)\s*$')
PASSWORD_PROMPT = re.compile(r'(?i)password:\s*$')
DEFAULT_PROMPT = r'(?m)^[\w.\-@/:()<>\[\]~]{1,63}[>#%$\]]\s*$'


class SessionError(Exception):
    pass


class _Channel:
    def __init__(self, exit_status):
        self._exit_status = exit_status

    def recv_exit_status(self):
        return self._exit_status


class _Stream:
    def __init__(self, data=b'', exit_status=0):
        self._data = data
        self.channel = _Channel(exit_status)

    def read(self):
        return self._data


class ShellSession:
    """An interactive shell that behaves like exec_command."""

    def __init__(self, client, device_type, enable_password=None, timeout=60):
        self.client = client
        self.timeout = timeout
        pattern = device_type.prompt_pattern or DEFAULT_PROMPT
        try:
            self.prompt = re.compile(pattern)
        except re.error as error:
            raise SessionError(f'The prompt pattern of {device_type.name} is not valid: {error}') from None
        self.channel = client.invoke_shell(term='vt100', width=511, height=10000)
        self.channel.settimeout(1.0)
        self.last_prompt = self._read_until_prompt().rstrip().rsplit('\n', 1)[-1].strip()
        if device_type.enable_command and enable_password:
            self._enable(device_type.enable_command, enable_password)
        for line in (device_type.session_setup or '').splitlines():
            if line.strip():
                self._send_and_wait(line.strip())

    def _recv(self):
        try:
            data = self.channel.recv(65535)
        except Exception:  # socket.timeout: nothing yet
            return None
        if data == b'':
            raise SessionError('The device closed the session')
        return data

    def _read_until(self, done, timeout=None):
        """Everything received until done(text) says so; pager prompts are
        answered with a space on the way."""
        deadline = time.monotonic() + (timeout or self.timeout)
        buffer = ''
        while time.monotonic() < deadline:
            data = self._recv()
            if data is None:
                if done(buffer):
                    return buffer
                continue
            buffer += ANSI.sub('', data.decode('utf-8', errors='replace'))
            if MORE.search(buffer):
                buffer = MORE.sub('', buffer)
                self.channel.send(' ')
                continue
            if done(buffer):
                return buffer
        raise SessionError('No prompt from the device within %d seconds' % (timeout or self.timeout))

    def _at_prompt(self, text):
        last = text.rstrip('\n').rsplit('\n', 1)[-1]
        return bool(self.prompt.search(last))

    def _read_until_prompt(self, timeout=None):
        return self._read_until(self._at_prompt, timeout)

    def _enable(self, command, password):
        self.channel.send(command + '\n')
        text = self._read_until(lambda t: bool(PASSWORD_PROMPT.search(t)) or self._at_prompt(t))
        if PASSWORD_PROMPT.search(text):
            self.channel.send(password + '\n')
            text = self._read_until(lambda t: bool(PASSWORD_PROMPT.search(t)) or self._at_prompt(t))
            last = text.rstrip().rsplit('\n', 1)[-1].strip()
            # Asked again, refused, or back at the same unprivileged prompt
            if PASSWORD_PROMPT.search(last) or re.search(r'(?i)denied|bad secret|invalid', text) \
                    or (self.last_prompt and last == self.last_prompt):
                raise SessionError('The enable password was not accepted')

    def _send_and_wait(self, command):
        self.channel.send(command + '\n')
        text = self._read_until_prompt()
        lines = text.split('\n')
        # The echo of the command first, the prompt last
        if lines and command.strip() and command.strip() in lines[0]:
            lines = lines[1:]
        if lines and self._at_prompt(lines[-1]):
            lines = lines[:-1]
        return '\n'.join(lines).strip('\n') + ('\n' if lines else '')

    def exec_command(self, command):
        output = self._send_and_wait(command)
        return None, _Stream(output.encode('utf-8')), _Stream(b'')

    def get_transport(self):
        return self.client.get_transport()

    def close(self):
        try:
            self.channel.close()
        finally:
            self.client.close()


class ExecSession:
    """One SSH channel per command: paramiko itself."""

    def __init__(self, client):
        self.client = client

    def exec_command(self, command):
        return self.client.exec_command(command)

    def get_transport(self):
        return self.client.get_transport()

    def close(self):
        self.client.close()


def connect(device_type, address, port, username, password, ssh_key=None, enable_password=None):
    """A session to a device of this type. Raises on a connection, host key or
    login failure, or when a shell type shows no prompt."""
    client = connect_to_ssh(address, port, username, password, ssh_key)
    if device_type is None or device_type.session_mode != 'shell':
        return ExecSession(client)
    try:
        return ShellSession(client, device_type, enable_password=enable_password)
    except Exception:
        client.close()
        raise


def connect_router(router):
    return connect(router.device_type, router.address, router.port, router.username, router.password,
                   router.ssh_key, router.enable_password)
