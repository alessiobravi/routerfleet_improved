"""Reading what a device sends back: bounded, and never stalled.

A command's output is read before its exit status (waiting for the exit
first can hang on a large output - the device waits to be read), at most
`limit` bytes (more is an error, not a memory blow-up), and the channel gets a
timeout so a device that stops sending - or fills its error stream while its
output is read - can not hold a worker forever.
"""
OUTPUT_MAX_BYTES = 64 * 1024 * 1024
CHANNEL_TIMEOUT = 600


def read_limited(stream, limit):
    chunks, total = [], 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            return b''.join(chunks)
        total += len(chunk)
        if total > limit:
            raise ValueError(f'the device sent more than {max(limit // (1024 * 1024), 1)} MB')
        chunks.append(chunk)


def with_timeout(stream, seconds=CHANNEL_TIMEOUT):
    channel = getattr(stream, 'channel', None)
    if channel is not None and hasattr(channel, 'settimeout'):
        try:
            channel.settimeout(seconds)
        except Exception:   # noqa: BLE001 - a session type without one keeps its own
            pass
    return stream


def read_output(stdout, limit=OUTPUT_MAX_BYTES):
    """A command's standard output, bounded, as text."""
    return read_limited(with_timeout(stdout), limit).decode('utf-8', errors='replace')


def run(session, command, limit=OUTPUT_MAX_BYTES):
    """(exit code, output, errors) of one command: output and errors read
    first, bounded, then the exit status."""
    _, stdout, stderr = session.exec_command(command)
    out = read_limited(with_timeout(stdout), limit).decode('utf-8', errors='replace')
    err = read_limited(with_timeout(stderr), 1024 * 1024).decode('utf-8', errors='replace')
    status = stdout.channel.recv_exit_status() if hasattr(stdout, 'channel') else 0
    return status, out, err
