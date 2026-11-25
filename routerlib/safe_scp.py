"""Downloads over SCP that a device can not turn against RouterFleet.

In SCP the sender describes what it sends - directories (D), files (C) and
their names - and a client that follows it writes where it is told. The scp
library checks a file's name against the folder it is in, but a directory
message moves that folder first, so a device (or whatever answers at a
device's address) could write any file of the container: "D… ../../x" then a
plain name.

A backup download asks for one file and takes exactly one file:

- no directory message;
- one file, and the device's name for it is not used - it goes to the path
  RouterFleet chose (and a name with "/" or ".." is refused anyway);
- at most MAX_BYTES (settings.BACKUP_DOWNLOAD_MAX_BYTES, 256 MB by default).

Anything else stops the download with an error; nothing is written outside
the chosen path.
"""
from django.conf import settings
from scp import SCPClient, SCPException

MAX_BYTES = 256 * 1024 * 1024


def max_bytes():
    return int(getattr(settings, 'BACKUP_DOWNLOAD_MAX_BYTES', MAX_BYTES))


class SafeSCPClient(SCPClient):
    """SCPClient for single-file downloads only."""

    def get(self, remote_path, local_path='', recursive=False, preserve_times=False):
        if recursive:
            raise SCPException('Recursive downloads are not allowed')
        self._files_received = 0
        return super().get(remote_path, local_path, recursive=False, preserve_times=preserve_times)

    def _refuse(self, reason):
        try:
            self.channel.send(b'\x01' + reason.encode('utf-8', 'replace'))
        finally:
            self.channel.close()
        raise SCPException(reason)

    def _set_time(self, cmd):
        # A time message belongs before the file; after it, the transfer is over
        if getattr(self, '_files_received', 0) >= 1:
            self._refuse('The device sent more after its file: refused')
        return super()._set_time(cmd)

    def _recv_pushd(self, cmd):
        self._refuse('The device sent a directory: refused, a backup download is one file')

    def _recv_file(self, cmd):
        parts = cmd.strip().split(b' ', 2)
        if len(parts) != 3:
            self._refuse('Bad file header from the device')
        name = parts[2]
        try:
            size = int(parts[1])
        except ValueError:
            self._refuse('Bad file size from the device')
        if getattr(self, '_files_received', 0) >= 1:
            self._refuse('The device sent more than one file: refused')
        if b'/' in name or b'\\' in name or name in (b'.', b'..') or b'\x00' in name:
            self._refuse('The device sent a file name with a path in it: refused')
        if size < 0 or size > max_bytes():
            self._refuse(f'The file is larger than the {max_bytes() // (1024 * 1024)} MB a backup download may be')
        self._files_received = getattr(self, '_files_received', 0) + 1
        return super()._recv_file(cmd)
