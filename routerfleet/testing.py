"""Shared helpers for the test suite.

Nothing here is imported by the application itself. It provides factories for
the objects most tests need, and stand-ins for the network: a fake SSH client
and a fake SCP client, so no test ever opens a connection to a device.
"""
import io
import itertools
import os
import shutil
import tempfile
from contextlib import contextmanager
from unittest import mock

from django.contrib.auth.models import User
from django.test import override_settings

from backup.models import BackupProfile
from router_manager.models import Router, RouterGroup, RouterInformation, RouterStatus, SSHKey
from user_manager.models import UserAcl

_counter = itertools.count(1)


def unique(prefix):
    return f'{prefix}-{next(_counter)}'


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def make_user(level=50, username=None, password=None):
    """A user holding a permission level. level=None makes a user without a
    UserAcl row at all.

    Without a password the account gets an unusable one, which skips the
    deliberately slow password hashing - tests sign in with force_login.
    """
    user = User.objects.create_user(username=username or unique('user'), password=password)
    if level is not None:
        UserAcl.objects.create(user=user, user_level=level)
    return user


def make_profile(name=None, **fields):
    return BackupProfile.objects.create(name=name or unique('profile'), **fields)


def make_router(name=None, router_type='routeros', password='router-secret', status_online=True,
                with_status=True, **fields):
    fields.setdefault('address', '192.0.2.10')
    router = Router.objects.create(name=name or unique('router'), router_type=router_type,
                                   password=password, **fields)
    if with_status:
        RouterStatus.objects.create(router=router, status_online=status_online)
    return router


def make_group(name=None, routers=(), **fields):
    group = RouterGroup.objects.create(name=name or unique('group'), **fields)
    if routers:
        group.routers.add(*routers)
    return group


def make_information(router, **fields):
    return RouterInformation.objects.create(router=router, **fields)


def make_sshkey(name=None):
    return SSHKey.objects.create(name=name or unique('key'), public_key='ssh-ed25519 AAAA test',
                                 private_key='FAKE-TEST-PRIVATE-KEY-MATERIAL')


# ---------------------------------------------------------------------------
# Fake SSH
# ---------------------------------------------------------------------------

class _Channel:
    def __init__(self, exit_code):
        self._exit_code = exit_code

    def recv_exit_status(self):
        return self._exit_code


class _Stream(io.BytesIO):
    def __init__(self, text, exit_code=0):
        super().__init__(text.encode('utf-8') if isinstance(text, str) else text)
        self.channel = _Channel(exit_code)


class FakeSSHClient:
    """Answers commands from a table instead of a device.

    responses maps a command to (exit_code, stdout, stderr), to a plain stdout
    string, to an Exception (raised, as a dropped connection would), or to a
    callable receiving the command. A command not in the table answers with
    exit code 0 and no output, unless strict=True.
    """

    def __init__(self, responses=None, default=(0, '', ''), strict=False):
        self.responses = dict(responses or {})
        self.default = default
        self.strict = strict
        self.commands = []
        self.closed = False

    def exec_command(self, command):
        self.commands.append(command)
        answer = self.responses.get(command, None if self.strict else self.default)
        if answer is None:
            raise AssertionError(f'Unexpected command sent to the fake device: {command!r}')
        if callable(answer) and not isinstance(answer, tuple):
            answer = answer(command)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, str):
            answer = (0, answer, '')
        exit_code, stdout, stderr = answer
        return io.BytesIO(), _Stream(stdout, exit_code), _Stream(stderr, exit_code)

    def get_transport(self):
        return mock.sentinel.transport

    def close(self):
        self.closed = True


class FakeSCPClient:
    """Writes files from a table to where the code asked them to be fetched."""

    files = {}
    fetched = []

    def __init__(self, transport, socket_timeout=None):
        pass

    def get(self, remote_path, local_path):
        if remote_path not in self.files:
            raise IOError(f'No such file on the fake device: {remote_path}')
        FakeSCPClient.fetched.append(remote_path)
        content = self.files[remote_path]
        with open(local_path, 'wb') as fh:
            fh.write(content.encode('utf-8') if isinstance(content, str) else content)


@contextmanager
def fake_ssh(target, client):
    """Patch connect_to_ssh where `target` imported it, handing out `client`."""
    with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client) as patched:
        yield patched


@contextmanager
def temporary_media_root():
    path = tempfile.mkdtemp(prefix='routerfleet-test-media-')
    try:
        with override_settings(MEDIA_ROOT=path):
            yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def cron_get(client, path, **extra):
    """A request to a /cron/ endpoint the way the scheduler container sends it."""
    return client.get(path, **extra)


def files_in(path):
    found = []
    for root, _, names in os.walk(path):
        found.extend(os.path.join(root, name) for name in names)
    return found


def edit_type(slug, **fields):
    """Changes fields of a device type in a test. The type is fetched (and a
    shipped preset created) first, so the change can not miss a row that a
    database reset removed."""
    from router_manager import device_types
    device_types.clear_cache()
    device_type = device_types.get(slug)
    for name, value in fields.items():
        setattr(device_type, name, value)
    device_type.save()
    return device_type
