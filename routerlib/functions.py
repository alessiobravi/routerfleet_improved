from io import StringIO

import re
import paramiko

from routerlib import host_keys


def get_router_features(router_type):
    if router_type in ['openwrt', 'routeros', 'routeros-branded', 'ubiquiti-airos']:
        return ['backup', 'reverse_monitoring', 'ssh', 'ssh_key']
    else:
        return []


def get_router_backup_file_extension(router_type):
    if router_type == 'routeros' or router_type == 'routeros-branded':
        return {'text': 'rsc', 'binary': 'backup'}
    elif router_type == 'openwrt':
        return {'text': 'txt', 'binary': 'tar.gz'}
    elif router_type == 'ubiquiti-airos':
        return {'text': 'cfg', 'binary': None}
    else:
        return {'text': 'txt', 'binary': 'bin'}


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


def test_authentication(router_type, address, port, username, password, sshkey=None):
    router_features = get_router_features(router_type)
    if 'ssh' not in router_features:
        return False, 'Router type not supported'
    return test_ssh_authentication(router_type, address, port, username, password, sshkey)


def test_ssh_authentication(router_type, address, port, username, password, sshkey=None):
    from fleet_commander import system_commands
    try:
        if router_type not in system_commands.DEVICE_TYPES:
            return False, 'Unsupported device type'
        ssh_client = connect_to_ssh(address, port, username, password, sshkey)
        # The commands are the system command "connection test"
        output = ''
        for line in system_commands.lines('connection_test', router_type):
            # The system command: edited only by an administrator after an explicit
            # confirmation, with no placeholder an outside value could fill
            stdin, stdout, stderr = ssh_client.exec_command(line)  # nosec B601
            output += stdout.read().decode('utf-8', errors='replace')
        if router_type == 'routeros' or router_type == 'routeros-branded':
            if router_type == 'routeros':
                if 'platform: MikroTik' in output:
                    result = True, 'Success: MikroTik device confirmed'
                else:
                    result = False, 'Device is not MikroTik'
            else:
                if 'platform: MikroTik' in output:
                    result = False, 'Device is not branded. Please select Mikrotik (RouterOS)'
                elif 'platform: ' in output:
                    result = True, 'Success: MikroTik branded device confirmed'
                else:
                    result = False, 'Device is not MikroTik'

        elif router_type == 'openwrt':
            if 'OpenWrt' in output:
                result = True, 'Success: OpenWRT device confirmed'
            else:
                result = False, 'Device is not OpenWRT'

        elif router_type == 'ubiquiti-airos':
            match = re.search(r'(?m)^\s*([A-Z]{2}\.v\d\S*)', output)
            if match:
                result = True, f'Success: Ubiquiti airOS device confirmed ({match.group(1)})'
            else:
                result = False, 'Device is not airOS'

        else:
            result = False, 'Unsupported device type'

        ssh_client.close()
        return result
    except Exception as e:
        return False, str(e)

