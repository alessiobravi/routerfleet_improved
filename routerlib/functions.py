from io import StringIO

import paramiko

from routerlib import host_keys


def get_router_backup_file_extension(router_type):
    """The file extensions of a device type's text and binary backups."""
    from router_manager import device_types
    device_type = device_types.get(router_type)
    if device_type is None:
        return {'text': 'txt', 'binary': 'bin'}
    return {'text': device_type.text_extension or 'txt', 'binary': device_type.binary_extension or None}


def gen_backup_name(router_backup):
    return f'routerfleet-backup-{router_backup.id}-{router_backup.schedule_type}-{router_backup.created.strftime("%Y-%m-%d_%H-%M")}'


def load_private_key_from_string(key_str):
    key_types = [
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        paramiko.Ed25519Key,
    ]
    for key_type in key_types:
        try:
            key_file_obj = StringIO(key_str)
            return key_type.from_private_key(key_file_obj)
        except paramiko.ssh_exception.SSHException:
            continue
    return None


def connect_to_ssh(address, port, username, password, sshkey=None):
    ssh_client = paramiko.SSHClient()
    # The device must present the host key it presented the first time
    stored = host_keys.prepare(ssh_client, address, port)
    try:
        if sshkey:
            private_key = load_private_key_from_string(sshkey.private_key)
            ssh_client.connect(address, port=port, username=username, pkey=private_key, look_for_keys=False, timeout=10, banner_timeout=10, auth_timeout=15, allow_agent=False)
        else:
            ssh_client.connect(address, port=port, username=username, password=password, look_for_keys=False, timeout=10, banner_timeout=10, auth_timeout=15, allow_agent=False)
    except paramiko.BadHostKeyException as error:
        # A different key than the stored one (of any type): refused before authentication
        ssh_client.close()
        raise host_keys.HostKeyChanged(host_keys.changed_message(stored, error.key)) from None
    except Exception:
        ssh_client.close()
        raise
    host_keys.remember(ssh_client, address, port, stored)
    return ssh_client


def test_authentication(router_type, address, port, username, password, sshkey=None, enable_password=None):
    """Connects as the device type says, runs its connection test and checks
    the output against the type's recognition pattern."""
    from fleet_commander import system_commands
    from router_manager import device_types
    from routerlib import device_session
    device_type = device_types.get(router_type)
    if device_type is None or not device_type.managed:
        return False, 'This device type is not reached over SSH'
    session = None
    try:
        session = device_session.connect(device_type, address, port, username, password, sshkey, enable_password)
        output = ''
        for line in system_commands.lines('connection_test', router_type):
            # The system command: edited only by an administrator after an explicit
            # confirmation, with no placeholder an outside value could fill
            stdin, stdout, stderr = session.exec_command(line)  # nosec B601
            output += stdout.read().decode('utf-8', errors='replace')
        return device_types.test_result(device_type, output)
    except Exception as e:
        return False, str(e)
    finally:
        if session is not None:
            session.close()
