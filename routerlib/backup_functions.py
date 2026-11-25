import datetime
import re
from django.utils import timezone
from backup_data.models import RouterBackup
import os
import shutil
import tempfile
from scp import SCPClient

from routerlib.safe_scp import SafeSCPClient
from django.core.files.base import ContentFile

from message_center.functions import notify_backup_fail
from routerlib.functions import gen_backup_name, get_router_backup_file_extension
from typing import Optional, Tuple


def append_task_console_output(router_backup: RouterBackup, text: str, *, new_line: bool = True,) -> None:
    if new_line:
        text += "\n"

    timestamp = timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S")
    if router_backup.task_console_output is None:
        router_backup.task_console_output = ""
    router_backup.task_console_output += f'[{timestamp}] {text}'
    router_backup.save(update_fields=["task_console_output"])


def run_scp_get(scp_client: SCPClient, remote_path: str, local_path: str, router_backup: Optional["RouterBackup"] = None,) -> None:
    if router_backup is not None:
        if router_backup.task_console_output is None:
            router_backup.task_console_output = ""
        append_task_console_output(router_backup, f"$ scp get {remote_path} -> {local_path}\n")

    try:
        scp_client.get(remote_path, local_path)

        if router_backup is not None:
            append_task_console_output(router_backup, "[scp_ok]\n\n")

    except Exception as e:
        if router_backup is not None:
            append_task_console_output(router_backup, f"[scp_error] {str(e)}\n\n")
        raise


OUTPUT_MAX_BYTES = 64 * 1024 * 1024   # what one command may print (a configuration export)


def read_limited(stream, limit):
    """All a stream gives, up to limit bytes - more is an error, not a memory
    blow-up."""
    chunks, total = [], 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            return b''.join(chunks)
        total += len(chunk)
        if total > limit:
            raise ValueError(f'the device sent more than {limit // (1024 * 1024)} MB')
        chunks.append(chunk)


def decode_text(raw, router_backup=None):
    """UTF-8, else Latin-1 - what a configuration file is written in (airOS)."""
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        if router_backup is not None:
            append_task_console_output(router_backup, '[decode=latin-1 fallback]')
        return raw.decode('latin-1', errors='replace')


def output_limit():
    from django.conf import settings
    return int(getattr(settings, 'BACKUP_OUTPUT_MAX_BYTES', OUTPUT_MAX_BYTES))


def run_ssh_command(ssh_client, command: str, router_backup: Optional["RouterBackup"] = None, *, skip_stdout: bool = False,
                    text_fallback: bool = False) -> Tuple[int, str, str]:
    # 1) Log command BEFORE executing, and persist it (so it survives failures)
    if router_backup is not None:
        if router_backup.task_console_output is None:
            router_backup.task_console_output = ""
        append_task_console_output(router_backup, f"$ {command}\n")

    # 2) Execute
    stdin, stdout, stderr = ssh_client.exec_command(command)

    # 3) Read the output first, then the exit status: waiting for the exit
    # first can hang on a large output - the device waits to be read
    from routerlib.device_io import with_timeout
    raw = read_limited(with_timeout(stdout), output_limit())
    stderr_text = read_limited(with_timeout(stderr), 1024 * 1024).decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    stdout_text = decode_text(raw, router_backup) if text_fallback else raw.decode("utf-8", errors="replace")

    # 4) Log outputs AFTER executing
    if router_backup is not None:
        if skip_stdout:
            if stdout_text:
                append_task_console_output(router_backup, "[stdout suppressed]\n")
        else:
            if stdout_text:
                append_task_console_output(router_backup, stdout_text.rstrip("\n") + "\n")

        if stderr_text:
            append_task_console_output(router_backup, "[stderr]\n" + stderr_text.rstrip("\n") + "\n")

        append_task_console_output(router_backup, f"[exit_code={exit_code}]\n\n")
        router_backup.save(update_fields=["task_console_output"])

    return exit_code, stdout_text, stderr_text


CAT_MARKER = 'RF-CAT-OK'


def _download_by_cat(session, device_type, remote_path, local_path, router_backup):
    """A file read over the command channel, byte for byte: True when it
    worked. Not for an interactive shell (a terminal changes binary data) or a
    device without cat (RouterOS) - those fall back to SCP."""
    import shlex
    from routerlib.safe_scp import max_bytes
    if getattr(device_type, 'session_mode', 'exec') == 'shell':
        return False
    # The marker goes to stderr only when a real shell ran cat and cat worked:
    # a device without one (RouterOS) can print its error with exit status 0,
    # and that must never be stored as the backup. stdout stays the file alone.
    command = f'cat -- {shlex.quote(remote_path)} && echo {CAT_MARKER} >&2'
    append_task_console_output(router_backup, f'$ {command}\n')
    try:
        _, stdout, stderr = session.exec_command(command)
        from routerlib.device_io import with_timeout
        data = read_limited(with_timeout(stdout), max_bytes())
        errors = read_limited(with_timeout(stderr), 64 * 1024)
        exit_code = stdout.channel.recv_exit_status()
    except Exception as error:   # noqa: BLE001 - SCP is tried next
        append_task_console_output(router_backup, f'[cat unavailable: {str(error)[:120]}] trying SCP\n\n')
        return False
    if exit_code != 0 or CAT_MARKER.encode() not in errors:
        append_task_console_output(router_backup, '[no shell with cat here] trying SCP\n\n')
        return False
    with open(local_path, 'wb') as handle:
        handle.write(data)
    append_task_console_output(router_backup, f'[cat_ok] {len(data)} bytes\n\n')
    return True


def perform_backup(router_backup: RouterBackup):
    if router_backup.success or router_backup.error:
        router_backup.router.routerstatus.backup_lock = None
        router_backup.router.routerstatus.save()
        return
    if not router_backup.router.backup_profile:
        router_backup.error = True
        router_backup.error_message = "No backup profile assigned"
        router_backup.save()
        router_backup.router.routerstatus.backup_lock = None
        router_backup.router.routerstatus.save()
        notify_backup_fail(router_backup)
        return
    if router_backup.retry_count > router_backup.router.backup_profile.max_retry:
        router_backup.error = True
        router_backup.save()
        router_backup.router.routerstatus.backup_lock = None
        router_backup.router.routerstatus.save()
        notify_backup_fail(router_backup)
        return

    if router_backup.backup_pending_retrieval:
        backup_success, error_message = retrieve_backup(router_backup)
        if error_message:
            handle_backup_failure(router_backup, error_message)
            return

        if backup_success:
            clean_up_backup_files(router_backup)
            if router_backup.schedule_time:
                start_time = router_backup.schedule_time
            else:
                start_time = router_backup.created
            router_backup.queue_length = (timezone.now() - start_time).seconds
            router_backup.finish_time = timezone.now()
            router_backup.backup_pending_retrieval = False
            router_backup.error_message = ''
            router_backup.success = True
            router_backup.save()
            # Keep only what changed, if the profile says so
            router_backup.drop_if_unchanged()
            from message_center.functions import notify_backup_success
            notify_backup_success(router_backup)
            router_backup.router.routerstatus.last_backup = timezone.now()
            router_backup.router.routerstatus.last_backup_failed = None
            router_backup.router.routerstatus.backup_lock = None
            router_backup.router.routerstatus.save()
        else:
            handle_backup_failure(router_backup, error_message)
    else:
        backup_success, backup_files, error_message = execute_backup(router_backup)
        if backup_success:
            router_backup.backup_pending_retrieval = True
            router_backup.error_message = ''
            router_backup.retry_count = 0
            router_backup.next_retry = timezone.now() + datetime.timedelta(seconds=router_backup.router.backup_profile.retrieve_interval)
            router_backup.save()
            # Nothing to download (text from command output only): finished now,
            # not one retrieve interval later
            from fleet_commander import system_commands
            if not system_commands.lines('backup_download', router_backup.router.router_type,
                                         **_backup_values(router_backup)):
                perform_backup(router_backup)
        else:
            handle_backup_failure(router_backup, error_message)
    return


def handle_backup_failure(router_backup: RouterBackup, error_message):
    router_backup.error_message = error_message
    router_backup.retry_count += 1
    router_backup.next_retry = timezone.now() + datetime.timedelta(minutes=router_backup.router.backup_profile.retry_interval)
    router_backup.save()
    append_task_console_output(router_backup, f"Backup failed: {error_message}")


def _version_key(version):
    """(6, 49, 21) of '6.49.21 (long-term)'; () when there is no number."""
    match = re.match(r'\s*(\d+(?:\.\d+)*)', version or '')
    return tuple(int(part) for part in match.group(1).split('.')) if match else ()


def export_words(device_type, os_version):
    """(include passwords, leave them out) - the device type's words, or its
    legacy ones for a device whose OS version is below legacy_before_version.
    A device whose version is not known yet gets the current words."""
    words = (device_type.option_sensitive, device_type.option_hide_sensitive)
    limit = _version_key(device_type.legacy_before_version)
    running = _version_key(os_version)
    if limit and running and running < limit:
        words = (device_type.legacy_option_sensitive, device_type.legacy_option_hide_sensitive)
    return words


def _backup_values(router_backup):
    """The placeholders the backup system commands may use. The export
    options are the device type's words for them (RouterOS 7: show-sensitive,
    terse; RouterOS 6: hide-sensitive when passwords are left out), chosen by
    the backup profile and the OS version the device last reported."""
    words = []
    router = router_backup.router
    profile = router.backup_profile
    device_type = router.device_type
    if profile and device_type:
        from router_manager.models import RouterInformation
        information = RouterInformation.objects.filter(router=router).only('os_version').first()
        include, leave_out = export_words(device_type, information.os_version if information else '')
        words.append(include if profile.parameter_sensitive else leave_out)
        if profile.parameter_terse:
            words.append(device_type.option_terse)
    parameters = ' '.join(word for word in words if word)
    return {'backup_name': gen_backup_name(router_backup), 'export_parameters': parameters}


def _named_texts(router_type, values):
    """[(name, command)] of the system command "backup - text"."""
    from fleet_commander import system_commands
    from router_manager import device_types
    return device_types.parse_named_lines('\n'.join(system_commands.lines('backup_text', router_type, **values)))


def execute_backup(router_backup: RouterBackup):
    """First phase: the text backups from command output ("backup - text"),
    then the files to download made on the device ("backup - create")."""
    from fleet_commander import system_commands
    from router_manager import device_types
    from routerlib import device_session
    error_message = ""
    router = router_backup.router
    device_type = router.device_type
    session = None
    if device_type is None or not device_type.managed:
        return False, [], f"Device type not supported: {router.get_router_type_display()}"
    values = _backup_values(router_backup)
    file_extension = get_router_backup_file_extension(router.router_type)
    try:
        texts = _named_texts(router.router_type, values)
        create = system_commands.lines('backup_create', router.router_type, **values)
        if not texts and not create:
            append_task_console_output(router_backup, '=== Nothing to create on the device: the files are downloaded as they are ===')
            return True, [], error_message
        session = device_session.connect_router(router)
        extras = []
        for number, (name, command) in enumerate(texts):
            exit_code, stdout_text, stderr_text = run_ssh_command(session, command, router_backup, skip_stdout=True,
                                                                  text_fallback=True)
            if exit_code:
                # The device refused the command: what it printed is its
                # error message, never a configuration to store
                answer = (stderr_text or stdout_text or '').strip().splitlines()
                return False, [], (f"Failed to execute backup: the device refused the text backup '{name}' "
                                   f"(exit code {exit_code}): {answer[0][:200] if answer else 'no message'}")
            text = device_types.clean_text(device_type, stdout_text.replace('\r\n', '\n'))
            if not text.strip():
                return False, [], f"Failed to execute backup: the text backup '{name}' is empty"
            if number == 0:
                router_backup.backup_text = text
                router_backup.backup_text_name = name
                router_backup.backup_text_filename = f'{values["backup_name"]}.{file_extension["text"]}'
            else:
                extras.append({'name': name, 'text': text})
        if texts:
            router_backup.extra_texts = extras
            router_backup.save()
        for command in create:
            exit_code, stdout_text, stderr_text = run_ssh_command(session, command, router_backup)
            if exit_code:
                answer = (stderr_text or stdout_text or '').strip().splitlines()
                return False, [], (f"Failed to execute backup: the device refused \"{command[:80]}\" "
                                   f"(exit code {exit_code}): {answer[0][:200] if answer else 'no message'}")
        return True, [], error_message
    except Exception as e:
        return False, [], f"Failed to execute backup: {str(e)}"
    finally:
        if session:
            session.close()


def _store_text(router_backup, local_path, file_extension, backup_name):
    from router_manager import device_types
    with open(local_path, 'rb') as handle:
        text = decode_text(handle.read(), router_backup)
    # Lines the device type marks as noise (timestamps) are not a change
    router_backup.backup_text = device_types.clean_text(router_backup.router.device_type, text)
    router_backup.backup_text_filename = f'{backup_name}.{file_extension["text"]}'


def retrieve_backup(router_backup: RouterBackup):
    """Second phase: the files of "backup - files to download" by SCP, then
    "backup - remove from device". A type that takes its text from commands
    and makes no files has nothing to download."""
    from fleet_commander import system_commands
    from routerlib import device_session
    append_task_console_output(router_backup, '=== Retrieving backup files ===')
    error_message = ""
    router = router_backup.router
    success = False
    session = None
    device_type = router.device_type
    if device_type is None or not device_type.managed:
        return success, f"Device type not supported: {router.get_router_type_display()}"
    values = _backup_values(router_backup)
    backup_name = values['backup_name']
    file_extension = get_router_backup_file_extension(router.router_type)
    # A directory of its own for the files on their way into the database: not a
    # name another process can predict in the shared /tmp, and removed in the
    # finally below whether the retrieval worked or not
    work = tempfile.mkdtemp(prefix='routerfleet-backup-')

    try:
        downloads = system_commands.lines('backup_download', router.router_type, **values)
        if not downloads:
            if router_backup.backup_text:
                return True, error_message
            return success, ('Failed to retrieve backup files: the device type takes no text backup and has no '
                             'files to download')
        text_taken = bool(router_backup.backup_text)
        binary_taken = False
        session = device_session.connect_router(router)
        scp_client = None
        for number, remote_path in enumerate(downloads):
            remote_path = remote_path.strip()
            is_text = not text_taken and remote_path.endswith('.' + file_extension['text'])
            if not is_text and binary_taken:
                # A backup keeps one binary file: a second would replace the first
                # and leave it behind in storage
                append_task_console_output(router_backup, f'[skipped] {remote_path}: a backup keeps one binary file\n\n')
                continue
            local_path = os.path.join(work, f'{number}-{os.path.basename(remote_path) or "file"}')
            # The file over the command channel (cat) where the device allows it;
            # SCP only where that is the only way - with one file, at this path
            if not _download_by_cat(session, device_type, remote_path, local_path, router_backup):
                if scp_client is None:
                    scp_client = SafeSCPClient(session.get_transport(), socket_timeout=300)
                run_scp_get(scp_client, remote_path, local_path, router_backup)
            if is_text:
                _store_text(router_backup, local_path, file_extension, backup_name)
                text_taken = True
            else:
                extension = file_extension['binary'] or 'bin'
                with open(local_path, 'rb') as backup_file:
                    router_backup.backup_binary.save(f"{backup_name}.{extension}", ContentFile(backup_file.read()))
                binary_taken = True
        router_backup.save()
        for command in system_commands.lines('backup_remove', router.router_type, **values):
            run_ssh_command(session, command, router_backup)
        success = True

    except Exception as e:
        return success, f"Failed to retrieve backup files: {str(e)}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if session:
            session.close()

    return success, error_message


def clean_up_backup_files(router_backup: RouterBackup):
    """After a successful backup: "backup - clean leftovers"."""
    from fleet_commander import system_commands
    from routerlib import device_session
    append_task_console_output(router_backup, '=== Cleaning up leftover backup files on the device ===')
    router = router_backup.router
    session = None
    try:
        if not router.is_managed:
            append_task_console_output(router_backup, f"Device type not supported: {router.get_router_type_display()}")
            return
        commands = system_commands.lines('backup_cleanup', router.router_type)
        if not commands:
            append_task_console_output(router_backup, 'Nothing to clean up on this device type')
            return
        session = device_session.connect_router(router)
        for command in commands:
            run_ssh_command(session, command, router_backup)
    except Exception as e:
        append_task_console_output(router_backup, f"Failed to clean up backup files: {str(e)}")
    finally:
        if session:
            session.close()
