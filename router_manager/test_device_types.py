"""Device types as data: helpers, the shell session, the pages."""
import json
from unittest import mock

from django.test import SimpleTestCase, TestCase

from router_manager import device_types
from router_manager.models import DeviceType, Router
from routerfleet.testing import edit_type, make_router, make_user
from routerlib import device_session


class HelperTests(TestCase):
    def test_named_lines(self):
        self.assertEqual(device_types.parse_named_lines('configuration: show configuration\n'
                                                        'show configuration | display set\n'
                                                        'set: x\nset: y\n'),
                         [('configuration', 'show configuration'), ('configuration-2', 'show configuration | display set'),
                          ('set', 'x'), ('set-2', 'y')])
        self.assertEqual(device_types.parse_named_lines('/export terse'), [('configuration', '/export terse')])

    def test_inventory_sources(self):
        cisco = device_types.get('cisco-ios')
        output = ('Cisco IOS XE Software, Version 17.09.04a\ncisco C9300-24T (X86) processor with 1333 bytes of memory.\n'
                  'Processor board ID FOC1234X0AB\nNAME: "Chassis", DESCR: "C9300"\nPID: C9300-24T         , VID: V02\n'
                  'ROM: IOS-XE ROMMON\n')
        found = device_types.read_inventory(cisco, {}, output)
        self.assertEqual(found['os_version'], '17.09.04a')
        self.assertEqual(found['model_name'], 'C9300-24T')
        self.assertEqual(found['serial_number'], 'FOC1234X0AB')
        self.assertEqual(found['model_version'], 'C9300-24T')
        self.assertEqual(found['firmware_version'], 'IOS-XE ROMMON')

    def test_juniper_and_huawei_presets(self):
        junos = device_types.read_inventory(device_types.get('juniper-junos'), {},
                                            'Hostname: r1\nModel: mx204\nJunos: 23.4R2-S3.9\n'
                                            'Chassis                                JN1234567AFA      MX204\n')
        self.assertEqual((junos['model_name'], junos['os_version'], junos['serial_number']),
                         ('mx204', '23.4R2-S3.9', 'JN1234567AFA'))
        vrp = device_types.read_inventory(device_types.get('huawei-vrp'), {},
                                          'Huawei Versatile Routing Platform Software\n'
                                          'VRP (R) software, Version 8.220 (NE40E V800R022C00SPC500)\n'
                                          'HUAWEI NE40E-X8A uptime is 10 days\nESN of master: 2102351931P0K1000123\n')
        # The release, not the VRP number many releases share
        self.assertEqual(vrp['os_version'], 'V800R022C00SPC500')
        self.assertEqual(vrp['model_name'], 'NE40E-X8A')
        self.assertEqual(vrp['serial_number'], '2102351931P0K1000123')

    def test_validation(self):
        self.assertEqual(device_types.validate_inventory_fields({'os_version': ['key:version', 're:(\\d+)']}), [])
        problems = device_types.validate_inventory_fields({'os_version': ['nope'], 'colour': ['key:x'],
                                                           'serial_number': ['re:([']})
        self.assertEqual(len(problems), 3)

    def test_noise_lines_are_dropped(self):
        cisco = device_types.get('cisco-ios')
        text = 'Building configuration...\n! Last configuration change at 10:00\nhostname r1\n'
        self.assertEqual(device_types.clean_text(cisco, text), 'hostname r1\n')

    def test_links_never_carry_scripts(self):
        self.assertEqual(device_types.parse_links('Winbox: winbox://{address}\nBad: javascript://alert(1)\n'
                                                  'Also bad: data://x\nno scheme'),
                         [('Winbox', 'winbox://{address}')])
        self.assertTrue(device_types.validate_links('Web: https://{address}:{secret}/'))
        router = make_router(router_type='routeros', address='192.0.2.5', username='ad"min')
        self.assertEqual(device_types.links_for(router), [('Winbox', 'winbox://192.0.2.5')])

    def test_a_missing_preset_comes_back(self):
        DeviceType.objects.filter(slug='openwrt').delete()
        device_types.clear_cache()
        self.assertEqual(device_types.get('openwrt').name, 'OpenWrt')
        self.assertIsNone(device_types.get('no-such-type'))

    def test_branded_is_gone(self):
        self.assertFalse(DeviceType.objects.filter(slug='routeros-branded').exists())
        self.assertNotIn('routeros-branded', [slug for slug, _ in device_types.choices()])


class FakeChannel:
    """An interactive shell: answers commands like a Cisco CLI with paging."""

    def __init__(self, enable_password='en-secret'):
        self.sent = []
        self.queue = [b'\r\nWelcome\r\nr1>']
        self.enable_password = enable_password
        self.privileged = False

    def settimeout(self, _):
        pass

    def recv(self, _):
        if not self.queue:
            raise TimeoutError
        return self.queue.pop(0)

    def send(self, data):
        self.sent.append(data)
        prompt = b'r1#' if self.privileged else b'r1>'
        line = data.strip()
        if data == ' ':
            self.queue.append(b'\r\nlast line\r\n' + prompt)
        elif line == 'enable':
            self.queue.append(b'enable\r\nPassword: ')
        elif line == self.enable_password:
            self.privileged = True
            self.queue.append(b'\r\nr1#')
        elif line == 'show running-config':
            self.queue.append(b'show running-config\r\nhostname r1\r\n --More-- ')
        elif line == 'reload':
            self.queue.append(b'reload\r\nProceed with reload? [confirm]')
        elif line == 'save':
            self.queue.append(b'save\r\nAre you sure to continue?[Y/N]:')
        elif line == 'y' and self.sent[-2].startswith('save'):
            self.queue.append(b'y\r\nSave the configuration as vrpcfg.zip? [Y/N]:')
        elif line == 'y' and self.sent[-2].startswith('y'):
            self.queue.append(b'y\r\nSaved.\r\n' + prompt)
        elif line == 'y':
            self.queue.append(b'y\r\nReloading\r\n' + prompt)
        elif line == 'nag':
            self.queue.append(b'nag\r\nReally? [y/n]:')
        elif line == 'n':
            self.queue.append(b'n\r\nReally? [y/n]:')
        else:
            self.queue.append(data.encode() + b'\r\n\x1b[0m' + prompt)

    def close(self):
        pass


class FakeClient:
    def __init__(self, channel):
        self.channel = channel
        self.closed = False

    def invoke_shell(self, **_):
        return self.channel

    def get_transport(self):
        return None

    def close(self):
        self.closed = True


class ShellSessionTests(SimpleTestCase):
    def cisco(self):
        return DeviceType(name='Cisco', session_mode='shell', prompt_pattern=r'(?m)^[\w.-]+[>#]\s*$',
                          session_setup='terminal length 0', enable_command='enable')

    def test_enable_paging_and_output(self):
        channel = FakeChannel()
        session = device_session.ShellSession(FakeClient(channel), self.cisco(), enable_password='en-secret')
        self.assertIn('terminal length 0\n', channel.sent)
        _, stdout, _ = session.exec_command('show running-config')
        output = stdout.read().decode()
        self.assertIn('hostname r1', output)
        self.assertIn('last line', output, 'the pager was answered')
        self.assertNotIn('More', output)
        self.assertNotIn('r1#', output, 'the prompt is not part of the output')
        self.assertEqual(stdout.channel.recv_exit_status(), 0)

    def test_questions_are_answered_after_the_mark(self):
        channel = FakeChannel()
        session = device_session.ShellSession(FakeClient(channel), self.cisco())
        _, stdout, _ = session.exec_command('reload <<< y')
        self.assertIn('reload\n', channel.sent, 'the mark and the answer are not sent as the command')
        self.assertIn('y\n', channel.sent)
        self.assertIn('Reloading', stdout.read().decode())
        # Two questions in a row, the same answer to both
        channel.sent.clear()
        session.exec_command('save <<< y')
        self.assertEqual(channel.sent, ['save\n', 'y\n', 'y\n'])

    def test_a_device_that_keeps_asking_is_given_up(self):
        channel = FakeChannel()
        session = device_session.ShellSession(FakeClient(channel), self.cisco())
        with self.assertRaises(device_session.SessionError):
            session.exec_command('nag <<< n')
        self.assertEqual(channel.sent.count('n\n'), device_session.MAX_QUESTIONS)

    def test_without_the_mark_a_question_is_not_answered(self):
        channel = FakeChannel()
        session = device_session.ShellSession(FakeClient(channel), self.cisco())
        with mock.patch('routerlib.device_session.time.monotonic', side_effect=[0, 0, 0, 100]), \
                self.assertRaises(device_session.SessionError):
            session.exec_command('reload')
        self.assertNotIn('y\n', channel.sent)

    def test_exec_types_get_the_answer_on_their_input(self):
        client = mock.Mock()
        stdin = mock.Mock()
        client.exec_command.return_value = (stdin, mock.Mock(), mock.Mock())
        device_session.ExecSession(client).exec_command('/system reboot <<< y')
        client.exec_command.assert_called_once_with('/system reboot')
        stdin.write.assert_called_once_with('y\n' * device_session.MAX_QUESTIONS)

    def test_a_wrong_enable_password_is_refused(self):
        channel = FakeChannel(enable_password='right')
        with self.assertRaises(device_session.SessionError):
            device_session.ShellSession(FakeClient(channel), self.cisco(), enable_password='wrong')

    def test_no_prompt_is_a_timeout(self):
        channel = FakeChannel()
        channel.queue = []
        with mock.patch('routerlib.device_session.time.monotonic', side_effect=[0, 0, 100]), \
                self.assertRaises(device_session.SessionError):
            device_session.ShellSession(FakeClient(channel), self.cisco())

    def test_exec_types_use_channels(self):
        client = mock.Mock()
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client):
            session = device_session.connect(DeviceType(session_mode='exec'), 'a', 22, 'u', 'p')
        session.exec_command('uname -a')
        client.exec_command.assert_called_once_with('uname -a')


class PageTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(50))

    def form(self, **overrides):
        data = {'name': 'Fortinet FortiOS', 'slug': 'fortinet-fortios', 'enabled': 'on', 'session_mode': 'shell',
                'prompt_pattern': r'(?m)^\S+ [#$] $', 'session_setup': 'config system console\nset output standard\nend',
                'test_pattern': 'FortiGate', 'inventory_os_version': r're:Version: \S+ v([\d.]+)',
                'inventory_model_name': r're:Version: (\S+)', 'text_extension': 'conf', 'firmware_verify': 'none',
                'vendor_version_field': 'os_version', 'command_connection_test': 'get system status',
                'command_inventory': 'get system status', 'command_backup_text': 'configuration: show full-configuration'}
        data.update(overrides)
        return data

    def test_the_wizard_creates_a_type_and_its_commands(self):
        from fleet_commander.models import CommandVariant
        response = self.client.post('/system/device-types/new/', self.form())
        device_type = DeviceType.objects.get(slug='fortinet-fortios')
        self.assertRedirects(response, f'/system/device-types/edit/?uuid={device_type.uuid}', fetch_redirect_response=False)
        self.assertEqual(device_type.inventory_fields['os_version'], [r're:Version: \S+ v([\d.]+)'])
        variants = dict(CommandVariant.objects.filter(router_type='fortinet-fortios').values_list('command__system_key', 'payload'))
        self.assertEqual(variants['backup_text'], 'configuration: show full-configuration')
        self.assertEqual(variants['backup_create'], '')
        self.assertIn(('fortinet-fortios', 'Fortinet FortiOS'), device_types.choices())

    def test_invalid_definitions_are_refused(self):
        for overrides in ({'slug': 'Bad Slug'}, {'slug': 'routeros'}, {'test_pattern': '(['},
                          {'prompt_pattern': ''}, {'management_links': 'x: javascript:alert(1)'},
                          {'vendor_url': 'http://insecure/'}, {'firmware_upload_path': '/tmp/$(reboot)'},
                          {'command_backup_text': 'x: {{ backup_name }}'}, {'inventory_cpu': 'whatever'}):
            with self.subTest(overrides=overrides):
                self.client.post('/system/device-types/new/', self.form(**overrides))
                self.assertFalse(DeviceType.objects.filter(name='Fortinet FortiOS').exists())

    def test_edit_restore_and_delete(self):
        routeros = device_types.get('routeros')
        page = self.client.get(f'/system/device-types/edit/?uuid={routeros.uuid}')
        self.assertContains(page, 'Restore preset')
        self.assertContains(page, '/fleet_commander/system/variant/')
        data = self.form(name='RouterOS', test_pattern='platform: Acme')
        data.pop('slug')
        self.client.post(f'/system/device-types/edit/?uuid={routeros.uuid}', data)
        routeros.refresh_from_db()
        self.assertEqual(routeros.test_pattern, 'platform: Acme')
        self.client.post(f'/system/device-types/edit/?uuid={routeros.uuid}', {'action': 'restore'})
        routeros.refresh_from_db()
        self.assertEqual(routeros.test_pattern, 'platform: MikroTik')
        make_router(router_type='routeros')
        self.client.post(f'/system/device-types/edit/?uuid={routeros.uuid}', {'action': 'delete', 'confirmation': 'delete'})
        self.assertTrue(DeviceType.objects.filter(slug='routeros').exists(), 'in use: not deleted')
        huawei = device_types.get('huawei-vrp')
        self.client.post(f'/system/device-types/edit/?uuid={huawei.uuid}', {'action': 'delete', 'confirmation': 'delete'})
        self.assertFalse(DeviceType.objects.filter(slug='huawei-vrp').exists())

    def test_only_administrators(self):
        self.client.force_login(make_user(40))
        for path in ('/system/device-types/', '/system/device-types/new/'):
            self.assertContains(self.client.get(path), 'Access Denied')
        self.assertEqual(self.client.post('/system/device-types/try/', self.form()).status_code, 403)

    def test_try_runs_the_form_against_a_device(self):
        from routerfleet.testing import FakeSSHClient
        client = FakeSSHClient({'get system status': 'Version: FortiGate-60F v7.2.8,build1639\n',
                                'show full-configuration': 'config system global\nend\n'})
        data = self.form(session_mode='exec', try_address='192.0.2.50', try_port='22', try_username='admin',
                         try_password='pw')
        with mock.patch('console.network.check', return_value='192.0.2.50'), \
                mock.patch('routerlib.device_session.connect_to_ssh', return_value=client):
            result = self.client.post('/system/device-types/try/', data).json()
        self.assertTrue(result['ok'], result)
        self.assertIn('confirmed', result['steps'][0]['message'])
        fields = dict(result['steps'][1]['fields'])
        self.assertEqual(fields['OS version'], '7.2.8')
        self.assertEqual(result['steps'][2]['title'], 'Text backup "configuration"')
        self.assertFalse(DeviceType.objects.filter(slug='fortinet-fortios').exists(), 'nothing stored')

    def test_try_refuses_routerfleet_itself(self):
        from console.network import Refused
        with mock.patch('console.network.check', side_effect=Refused('that address belongs to RouterFleet itself')):
            result = self.client.post('/system/device-types/try/', self.form(try_address='127.0.0.1')).json()
        self.assertFalse(result['ok'])
        self.assertIn('RouterFleet itself', result['message'])

    def test_device_list_uses_the_type_links(self):
        from user_manager.models import UserAcl
        make_router(router_type='routeros', name='mt1', username='admin')
        UserAcl.objects.update(address_link='both')
        page = self.client.get('/router/list/')
        self.assertContains(page, 'href="winbox://192.0.2.10"')

    def test_enable_password_is_kept_and_never_shown(self):
        router = make_router(router_type='cisco-ios', enable_password='en-secret', name='c1')
        self.assertNotContains(self.client.get(f'/router/manage/?uuid={router.uuid}'), 'en-secret')
        router.refresh_from_db()
        self.assertEqual(router.enable_password, 'en-secret')


class MorePageTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(50))

    def test_an_invalid_edit_shows_the_reasons(self):
        openwrt = device_types.get('openwrt')
        page = self.client.post(f'/system/device-types/edit/?uuid={openwrt.uuid}',
                                {'name': 'OpenWrt', 'session_mode': 'exec', 'test_pattern': '([',
                                 'text_extension': 'txt', 'vendor_version_field': 'os_version'})
        self.assertEqual(page.status_code, 200)
        openwrt.refresh_from_db()
        self.assertEqual(openwrt.test_pattern, 'OpenWrt', 'nothing saved')

    def test_a_type_with_firmware_is_not_deleted(self):
        from django.core.files.base import ContentFile
        from firmware.models import FirmwareImage
        from routerfleet.testing import temporary_media_root
        huawei = device_types.get('huawei-vrp')
        with temporary_media_root():
            image = FirmwareImage(device_type=huawei, version='8.1', filename='v.cc', size=1, sha256='0' * 64)
            image.file.save('v.cc', ContentFile(b'x'), save=False)
            image.save()
            self.client.post(f'/system/device-types/edit/?uuid={huawei.uuid}', {'action': 'delete', 'confirmation': 'delete'})
        self.assertTrue(DeviceType.objects.filter(slug='huawei-vrp').exists())

    def test_the_list_and_the_monitoring_type(self):
        page = self.client.get('/system/device-types/')
        for name in ('Cisco IOS / IOS-XE', 'Juniper Junos', 'Huawei VRP', 'Monitoring only'):
            self.assertContains(page, name)
        monitoring = device_types.get('monitoring')
        self.assertNotContains(self.client.get(f'/system/device-types/edit/?uuid={monitoring.uuid}'), 'id="try-button"')

    def test_vendor_check_needs_its_list(self):
        data = {'name': 'X', 'slug': 'x-os', 'session_mode': 'exec', 'text_extension': 'txt', 'check_vendor': 'on',
                'vendor_version_field': 'os_version', 'command_connection_test': 'uname', 'command_inventory': 'uname'}
        self.client.post('/system/device-types/new/', data)
        self.assertFalse(DeviceType.objects.filter(slug='x-os').exists())
        data.update(vendor_url='https://example.com/releases.json', vendor_keys='latest.stable',
                    vendor_confirm_url='https://example.com/{version}/{bogus}')
        self.client.post('/system/device-types/new/', data)
        self.assertFalse(DeviceType.objects.filter(slug='x-os').exists())
        data['vendor_confirm_url'] = 'https://example.com/{version}/{target}'
        self.client.post('/system/device-types/new/', data)
        self.assertTrue(DeviceType.objects.filter(slug='x-os').exists())

    def test_try_needs_an_address(self):
        result = self.client.post('/system/device-types/try/', {'name': 'X', 'slug': 'try-x', 'session_mode': 'exec',
                                                                'text_extension': 'txt', 'command_connection_test': 'uname',
                                                                'command_inventory': 'uname'}).json()
        self.assertFalse(result['ok'])
        self.assertIn('address', result['message'])
        self.assertEqual(self.client.get('/system/device-types/try/').status_code, 405)

    def test_nested_vendor_keys(self):
        from routerlib import router_functions
        self.assertEqual(router_functions._dig({'latest': {'stable': '2.4'}}, 'latest.stable'), '2.4')
        self.assertEqual(router_functions._dig({'latest': ['x']}, 'latest'), '')
        self.assertEqual(router_functions._dig({'a': 1}, 'a.b'), '')


class NamedTextPageTests(TestCase):
    def setUp(self):
        from backup_data.models import RouterBackup
        self.client.force_login(make_user(20))
        router = make_router(router_type='juniper-junos', name='j1')
        self.first = RouterBackup.objects.create(router=router, schedule_type='daily', success=True,
                                                 backup_text='system { host-name a; }\n', backup_text_name='configuration',
                                                 extra_texts=[{'name': 'set', 'text': 'set system host-name a\n'}],
                                                 backup_text_filename='b1.conf')
        self.second = RouterBackup.objects.create(router=router, schedule_type='daily', success=True,
                                                  backup_text='system { host-name a; }\n', backup_text_name='configuration',
                                                  extra_texts=[{'name': 'set', 'text': 'set system host-name b\n'}],
                                                  backup_text_filename='b2.conf')

    def test_each_named_text_is_compared(self):
        base = f'/backup/compare/?uuid={self.first.uuid}&compare_uuid={self.second.uuid}'
        page = self.client.get(base)
        self.assertEqual(page.context['text_names'], ['configuration', 'set'])
        self.assertEqual(page.context['diff_str'], '')
        diff = self.client.get(base + '&text=set').context['diff_str']
        self.assertIn('+set system host-name b', diff)
        self.assertEqual(self.client.get(base + '&text=nope').context['text_chosen'], 'configuration')

    def test_each_named_text_is_downloaded(self):
        response = self.client.get(f'/backup/download/?uuid={self.second.uuid}&type=text&name=set')
        self.assertEqual(response.content, b'set system host-name b\n')
        self.assertIn('filename=b2-set.conf', response['Content-Disposition'])
        first = self.client.get(f'/backup/download/?uuid={self.second.uuid}&type=text')
        self.assertIn('filename=b2.conf', first['Content-Disposition'])

    def test_the_details_page_shows_them_all(self):
        page = self.client.get(f'/backup/backup_details/?uuid={self.second.uuid}')
        self.assertContains(page, 'Text Backup: set')
        self.assertContains(page, 'set system host-name b')
        self.assertTrue(self.second.config_change_detected)


class TryFailureTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(50))

    def try_with(self, error):
        data = {'name': 'X', 'slug': 'try-y', 'session_mode': 'exec', 'text_extension': 'txt',
                'command_connection_test': 'uname', 'command_inventory': 'uname', 'try_address': '192.0.2.9',
                'try_port': '22', 'try_username': 'u', 'try_password': 'p'}
        with mock.patch('console.network.check', return_value='192.0.2.9'), \
                mock.patch('routerlib.device_session.connect_to_ssh', side_effect=error), \
                self.assertLogs('router_manager.device_type_views', level='WARNING'):
            return self.client.post('/system/device-types/try/', data).json()

    def test_failures_are_described_never_echoed(self):
        import paramiko
        from routerlib import host_keys
        cases = ((paramiko.AuthenticationException('secret detail'), 'refused the username or password'),
                 (host_keys.HostKeyChanged('secret detail'), 'different host key'),
                 (TimeoutError('secret detail'), 'did not answer in time'),
                 (OSError('secret detail'), 'Could not connect over SSH'),
                 (ValueError('secret detail'), 'The try failed'))
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                result = self.try_with(error)
                self.assertIn(expected, result['message'])
                self.assertNotIn('secret detail', result['message'])


class UpdateMarkTests(TestCase):
    def test_devices_with_an_update_waiting_are_marked(self):
        from router_manager.models import RouterInformation
        self.client.force_login(make_user(10))
        behind = make_router(router_type='routeros', name='behind')
        current = make_router(router_type='routeros', name='current')
        unknown = make_router(router_type='cisco-ios', name='no-check')
        RouterInformation.objects.create(router=behind, os_version='7.16', available_version='7.18')
        RouterInformation.objects.create(router=current, os_version='7.18', available_version='7.18')
        RouterInformation.objects.create(router=unknown, os_version='17.9', available_version='')
        page = self.client.get('/router/list/').content.decode()
        self.assertEqual(page.count('rf-up-os"'), 1)
        mark = page.index('rf-up-os"')
        self.assertLess(page.rindex('behind', 0, mark), mark)
        self.assertIn('7.18 is available, the device runs 7.16', page)



class ExportWordsByVersionTests(TestCase):
    """RouterOS 6 and 7 name the passwords option the opposite way; the device
    type holds both sets, the migration fills them in for an existing type."""

    def test_the_migration_gives_an_unedited_routeros_type_its_routeros_6_words(self):
        import importlib
        from django.apps import apps
        from router_manager.models import DeviceType
        from router_manager import device_types
        routeros = device_types.get('routeros')
        DeviceType.objects.filter(pk=routeros.pk).update(legacy_before_version='', legacy_option_hide_sensitive='')
        edited = DeviceType.objects.create(slug='routeros-edited', name='RouterOS (edited)', option_sensitive='my-word')
        module = importlib.import_module('router_manager.migrations.0032_device_type_export_words_by_version')
        module.routeros_6_words(apps, None)
        routeros.refresh_from_db()
        self.assertEqual((routeros.legacy_before_version, routeros.legacy_option_hide_sensitive), ('7', 'hide-sensitive'))
        self.assertEqual(routeros.option_sensitive, 'show-sensitive', 'RouterOS 7 exports as before')
        edited.refresh_from_db()
        self.assertEqual(edited.legacy_before_version, '')

    def test_the_form_accepts_words_and_versions_only(self):
        from router_manager.device_type_views import read_form
        from router_manager import device_types
        routeros = device_types.get('routeros')
        base = {'name': routeros.name, 'session_mode': 'exec', 'text_extension': 'rsc'}
        problems, _ = read_form({**base, 'option_sensitive': 'show-sensitive', 'legacy_before_version': '7',
                                 'legacy_option_hide_sensitive': 'hide-sensitive'}, routeros)
        self.assertEqual(problems, [])
        problems, _ = read_form({**base, 'legacy_option_hide_sensitive': 'hide-sensitive; /system reset'}, routeros)
        self.assertTrue(any('Export options' in p for p in problems), problems)
        problems, _ = read_form({**base, 'legacy_before_version': 'seven'}, routeros)
        self.assertTrue(any('version number' in p for p in problems), problems)


class HuaweiAndClassicIosFirmwareTests(TestCase):
    """Firmware procedures for Huawei VRP (shipped type, filled by a migration
    where still unset) and classic Cisco IOS (a preset), and versions compared
    the way these vendors number them."""

    def reset_huawei(self):
        from fleet_commander.models import Command, CommandVariant
        from router_manager.models import DeviceType
        huawei = device_types.get('huawei-vrp')
        old = [r're:VRP \(R\) software, Version ([\d.]+(?: \([^)]+\))?)']
        DeviceType.objects.filter(pk=huawei.pk).update(firmware_upload_path='', firmware_verify='none',
                                                       inventory_fields={**huawei.inventory_fields, 'os_version': old})
        CommandVariant.objects.filter(router_type='huawei-vrp', command__system_key__in=[
            'firmware_verify', 'firmware_install']).update(payload='')
        return huawei

    def run_migration(self):
        import importlib
        from django.apps import apps
        importlib.import_module('fleet_commander.migrations.0021_huawei_firmware').fill(apps, None)

    def test_the_migration_fills_an_unedited_huawei_type(self):
        from fleet_commander import system_commands
        huawei = self.reset_huawei()
        self.run_migration()
        huawei.refresh_from_db()
        self.assertEqual((huawei.firmware_upload_path, huawei.firmware_verify), ('{{ firmware_name }}', 'size'))
        self.assertIn('V\\d{3}R\\d{3}', huawei.inventory_fields['os_version'][0])
        self.assertEqual(system_commands.payload_of('firmware_install', 'huawei-vrp'),
                         'startup system-software {{ firmware_name }}\nreboot fast <<< y')
        self.assertEqual(system_commands.payload_of('firmware_verify', 'huawei-vrp'), 'dir {{ firmware_name }}')

    def test_the_migration_leaves_what_was_edited(self):
        from fleet_commander.models import CommandVariant
        from fleet_commander import system_commands
        from router_manager.models import DeviceType
        huawei = self.reset_huawei()
        DeviceType.objects.filter(pk=huawei.pk).update(firmware_upload_path='cfcard:/{{ firmware_name }}',
                                                       inventory_fields={'os_version': ['re:mine (\\S+)']})
        CommandVariant.objects.filter(router_type='huawei-vrp', command__system_key='firmware_install').update(
            payload='my own procedure')
        self.run_migration()
        huawei.refresh_from_db()
        self.assertEqual(huawei.firmware_upload_path, 'cfcard:/{{ firmware_name }}')
        self.assertEqual(huawei.inventory_fields['os_version'], ['re:mine (\\S+)'])
        self.assertEqual(system_commands.payload_of('firmware_install', 'huawei-vrp'), 'my own procedure')

    def test_the_classic_ios_preset(self):
        import re
        preset = device_types.PRESETS['cisco-ios-classic']
        pattern = re.compile(preset['test_pattern'])
        self.assertTrue(pattern.search('Cisco IOS Software, C2951 Software (C2951-UNIVERSALK9-M), Version 15.5(2)T1'))
        self.assertFalse(pattern.search('Cisco IOS XE Software, Version 17.09.04a'))
        self.assertFalse(pattern.search('Cisco IOS Software [Denali], Catalyst L3 Switch Software, Version Denali 16.2.1'))
        install = device_types.PRESET_COMMANDS['cisco-ios-classic']['firmware_install']
        self.assertTrue(install.endswith('write memory\nreload <<< y'))
        self.assertIn('boot system flash:{{ firmware_name }}', install)

    def test_versions_compare_the_way_these_vendors_number_them(self):
        from router_manager.templatetags.router_tags import compare_versions
        self.assertEqual(compare_versions('V200R010C00SPC600', 'V200R010C00SPC700'), -1)
        self.assertEqual(compare_versions('V200R010C00', 'V200R010C00SPC600'), -1)
        self.assertEqual(compare_versions('15.5(2)T1', '15.5(3)M'), -1)
        self.assertEqual(compare_versions('15.2(4)M7', '15.2(4)M7'), 0)
        self.assertEqual(compare_versions('7.21beta3', '7.21rc1'), -1)
        self.assertEqual(compare_versions('7.21rc1', '7.21'), -1)
        self.assertEqual(compare_versions('7.16', '7.16.0'), 0)
        self.assertEqual(compare_versions('7.16.2 (stable)', '7.16.1'), 1)
        self.assertEqual(compare_versions('OpenWrt 24.10.3 r28714-00d1fe2b72', '24.10.4'), -1)
        self.assertIsNone(compare_versions('Unknown', '7.16'))

    def test_a_size_with_thousands_separators_is_recognised(self):
        from unittest import mock
        from firmware import rollout
        target = mock.Mock()
        target.rollout.image.size = 52434816
        target.router.device_type.firmware_verify = 'size'
        with mock.patch('fleet_commander.system_commands.lines', return_value=['dir x.cc']), \
                mock.patch.object(rollout, 'firmware_values', return_value={}), \
                mock.patch.object(rollout, '_session'), \
                mock.patch.object(rollout, '_run', return_value='  0  -rw-  52,434,816  Nov 24 2026 10:00:00  x.cc'), \
                mock.patch.object(rollout, '_move') as moved:
            rollout.step_verifying(target)
        moved.assert_called_once()
        self.assertEqual(moved.call_args[0][1], 'installing')

    def test_sizes_as_cisco_routeros_and_others_write_them(self):
        from unittest import mock
        from firmware import rollout
        for output in ('1  -rw-  52434816  Nov 24 2026 10:00:00 +00:00  c2900.bin',   # Cisco dir
                       '52434816', '52 434 816 bytes', '52.434.816'):
            target = mock.Mock()
            target.rollout.image.size = 52434816
            target.router.device_type.firmware_verify = 'size'
            with mock.patch('fleet_commander.system_commands.lines', return_value=['x']), \
                    mock.patch.object(rollout, 'firmware_values', return_value={}), \
                    mock.patch.object(rollout, '_session'), \
                    mock.patch.object(rollout, '_run', return_value=output), \
                    mock.patch.object(rollout, '_move') as moved:
                rollout.step_verifying(target)
            self.assertEqual(moved.call_args[0][1], 'installing', output)
        target.rollout.image.size = 52434817
        with mock.patch('fleet_commander.system_commands.lines', return_value=['x']), \
                mock.patch.object(rollout, 'firmware_values', return_value={}), \
                mock.patch.object(rollout, '_session'), \
                mock.patch.object(rollout, '_run', return_value='52434816'), \
                self.assertRaises(rollout.StepFailed):
            rollout.step_verifying(target)
