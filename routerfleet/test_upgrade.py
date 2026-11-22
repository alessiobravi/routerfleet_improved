"""A database left by the original project upgrades in place.

The migration history of this project is a strict superset of the original
project's and of the later predecessor's, so an installation of either is
upgraded by pointing this version at its database. This test makes that claim
concrete: the database is taken back to the state the original project leaves
it in, filled through the models as they were then, and migrated forward.
"""
import datetime
import json

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

# The last migration of every app in the original project
ORIGINAL_STATE = [
    ('backup', '0009_backupprofile_parameter_sensitive_and_more'),
    ('backup_data', '0011_routerbackup_task_lock'),
    ('fleet_commander', '0004_commandjob_aborted_count'),
    ('import_tool', '0007_alter_importtask_router_type'),
    ('integration_manager', '0002_alter_externalintegration_wireguard_webadmin_default_user_level'),
    ('message_center', '0007_alter_notification_notification_type'),
    ('monitoring', '0001_initial'),
    ('router_manager', '0021_routerstatus_command_lock'),
    ('routerfleet_tools', '0003_webadminsettings_monitoring_last_run_and_more'),
    ('user_manager', '0002_alter_useracl_user_level'),
    # Did not exist in the original project
    ('audit_log', None),
]

USER_UPDATE_PAYLOAD = ('/system/package/update/set channel=testing\n'
                       '/system/package/update/download\n'
                       '/system/package/update/install\n'
                       ':log info "done"')


class UpgradeFromOriginalProjectTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        # (app, None) means 'not applied', which is a migrate target but not a
        # node a model state can be built from
        executor.loader.build_graph()
        return executor.loader.project_state([t for t in targets if t[1]]).apps

    def latest(self):
        executor = MigrationExecutor(connection)
        return executor.loader.graph.leaf_nodes()

    def test_upgrade(self):
        old = self.migrate(ORIGINAL_STATE)
        Profile = old.get_model('backup', 'BackupProfile')
        existing_profile = Profile.objects.create(name='existing-profile')
        tables = connection.introspection.table_names()
        self.assertNotIn('audit_log_changerecord', tables)

        # --- written as the original project wrote it
        Router = old.get_model('router_manager', 'Router')
        router = Router.objects.create(name='edge-01', address='10.0.0.1', router_type='routeros',
                                       password='SuperSecret123!')
        Router.objects.create(name='edge-02', address='10.0.0.2', router_type='routeros', password='')
        switched_off = Router.objects.create(name='edge-03', address='10.0.0.3', router_type='routeros',
                                             password='', enabled=False)
        branded = Router.objects.create(name='edge-04', address='10.0.0.4', router_type='routeros-branded',
                                        password='')

        Backup = old.get_model('backup_data', 'RouterBackup')
        base = timezone.now() - datetime.timedelta(days=3)
        plan = [('AAA', False), ('AAA', False), ('', False), ('BBB', True), ('BBB', False)]
        backup_ids = []
        for minutes, (text_hash, _) in enumerate(plan):
            backup = Backup.objects.create(router_id=router.id, schedule_type='daily',
                                           backup_text_hash=text_hash, config_change_detected=False)
            Backup.objects.filter(pk=backup.pk).update(created=base + datetime.timedelta(minutes=minutes))
            backup_ids.append(backup.pk)

        CsvData = old.get_model('import_tool', 'CsvData')
        csv = CsvData.objects.create(
            raw_csv_data='name,password\nnew-01,CsvPass789\n',
            import_data=[{'name': 'new-01', 'password': 'CsvPass789'}])
        ImportTask = old.get_model('import_tool', 'ImportTask')
        task = ImportTask.objects.create(csv_data_id=csv.id, import_id=1, name='new-01', username='admin',
                                         password='ImportPass456', address='10.0.1.1',
                                         router_type='routeros')

        Command = old.get_model('fleet_commander', 'Command')
        # A command of its own that happens to carry a system command's name
        clashing = Command.objects.create(name='System: connection test')
        Variant = old.get_model('fleet_commander', 'CommandVariant')
        own_update = Command.objects.create(name='Our update')
        own_variant = Variant.objects.create(command_id=own_update.id, router_type='routeros',
                                             payload=USER_UPDATE_PAYLOAD)
        # A branded variant beside a RouterOS one (folded away), and one of its own (kept, as RouterOS)
        Variant.objects.create(command_id=own_update.id, router_type='routeros-branded', payload='/branded copy')
        branded_only = Command.objects.create(name='Branded only')
        Variant.objects.create(command_id=branded_only.id, router_type='routeros-branded', payload='/system identity print')

        SSHKey = old.get_model('router_manager', 'SSHKey')
        key = SSHKey.objects.create(name='fleet-key', public_key='ssh-ed25519 AAAA',
                                    private_key='FAKE-TEST-PRIVATE-KEY-MATERIAL')
        Channel = old.get_model('message_center', 'MessageChannel')
        callmebot = Channel.objects.create(name='whatsapp-alerts', channel_type='callmebot', destination='+391234567',
                                           token='FAKE-CALLMEBOT-KEY', enabled=True)
        old_ntfy = Channel.objects.create(name='old-ntfy', channel_type='ntfy', destination='my-topic', enabled=True)
        channel = Channel.objects.create(name='tg', channel_type='telegram', destination='42',
                                         token='FAKE-TEST-BOT-TOKEN')
        Channel.objects.create(name='topic', channel_type='ntfy', destination='t', token=None)
        Integration = old.get_model('integration_manager', 'ExternalIntegration')
        integration = Integration.objects.create(name='wireguard_webadmin', integration_type='wireguard_webadmin',
                                                 integration_url='https://wg.example', token='FAKE-TEST-API-KEY')

        # --- the upgrade
        with self.assertLogs('routerlib.encryption', level='WARNING') as logs:
            self.migrate(self.latest())
        # An upgrade does not change how an installation stores its backups: a
        # profile that existed keeps every backup; only new ones keep changes
        from backup.models import BackupProfile as NewProfile
        self.assertTrue(NewProfile.objects.get(pk=existing_profile.pk).keep_unchanged)
        self.assertFalse(NewProfile.objects.create(name='created-after-upgrade').keep_unchanged)
        # A device that was already disabled: off since its last save, as far as
        # anyone can tell; enabled devices without a record stay as they are
        from router_manager.models import Router as NewRouter
        off = NewRouter.objects.get(pk=switched_off.pk)
        self.assertEqual(off.enabled_changed_at, off.updated)
        self.assertIsNone(NewRouter.objects.get(pk=router.pk).enabled_changed_at)
        # Device types: the shipped ones exist; "Branded" is folded into RouterOS
        from router_manager.models import DeviceType, Router as NewRouter
        from fleet_commander.models import CommandVariant as NewVariant
        self.assertEqual(set(DeviceType.objects.values_list('slug', flat=True)),
                         {'monitoring', 'routeros', 'openwrt', 'ubiquiti-airos', 'cisco-ios', 'juniper-junos',
                          'huawei-vrp'})
        self.assertEqual(NewRouter.objects.get(pk=branded.pk).router_type, 'routeros')
        self.assertFalse(NewVariant.objects.filter(router_type='routeros-branded').exists())
        self.assertEqual(NewVariant.objects.get(command_id=branded_only.id).router_type, 'routeros')
        self.assertEqual(NewVariant.objects.filter(command_id=own_update.id).count(), 1)
        # Unedited RouterOS backup commands: the text now comes from /export output
        def payload(key, router_type):
            return NewVariant.objects.get(command__system_key=key, router_type=router_type).payload
        self.assertEqual(payload('backup_text', 'routeros'), 'configuration: /export {{ export_parameters }}')
        self.assertEqual(payload('backup_create', 'routeros'), '/system backup save name={{ backup_name }}.backup')
        self.assertEqual(payload('backup_text', 'openwrt'), 'configuration: uci export')
        self.assertEqual(payload('backup_create', 'openwrt'), 'sysupgrade --create-backup /tmp/{{ backup_name }}.tar.gz')
        self.assertEqual(payload('firmware_install', 'routeros'), '/system reboot')
        self.assertTrue(payload('backup_text', 'juniper-junos').startswith('configuration: show configuration'))
        # CallMeBot is no longer supported: kept, with its history, but switched off;
        # an ntfy channel of before keeps the public server
        from message_center.models import MessageChannel as NewChannel
        kept = NewChannel.objects.get(pk=callmebot.pk)
        self.assertEqual((kept.channel_type, kept.enabled), ('callmebot', False))
        self.assertEqual(NewChannel.objects.get(pk=old_ntfy.pk).server_url, 'https://ntfy.sh')
        self.assertTrue(NewChannel.objects.get(pk=old_ntfy.pk).enabled)
        # The system commands arrive with the upgrade, all of them
        from fleet_commander.models import Command as NewCommand
        from fleet_commander.system_commands import SYSTEM_KEYS
        self.assertEqual(sorted(NewCommand.objects.exclude(system_key=None).values_list('system_key', flat=True)),
                         sorted(SYSTEM_KEYS))
        # ...and the installation's own command of the same name stays as it was
        self.assertIsNone(NewCommand.objects.get(pk=clashing.pk).system_key)
        self.assertEqual(NewCommand.objects.get(system_key='connection_test').name, 'System: connection test (2)')
        # The warning seen during the upgrade names the harmless cause
        self.assertIn('not encrypted yet', ' '.join(logs.output))

        from audit_log.models import ChangeRecord
        from backup_data.models import RouterBackup
        from fleet_commander.models import Command as NewCommand, CommandVariant
        from import_tool.models import CsvData as NewCsv, ImportTask as NewTask
        from router_manager.models import Router as NewRouter

        # Passwords: ciphertext at rest, the original through the ORM, empty left empty
        with connection.cursor() as cursor:
            cursor.execute('SELECT password FROM router_manager_router WHERE id = %s', [router.id])
            at_rest = cursor.fetchone()[0]
            cursor.execute('SELECT password FROM import_tool_importtask WHERE id = %s', [task.id])
            task_at_rest = cursor.fetchone()[0]
        self.assertTrue(at_rest.startswith('gAAAAA'))
        self.assertNotIn('SuperSecret', at_rest)
        self.assertEqual(NewRouter.objects.get(pk=router.id).password, 'SuperSecret123!')
        self.assertEqual(NewRouter.objects.get(name='edge-02').password, '')
        self.assertTrue(task_at_rest.startswith('gAAAAA'))
        self.assertEqual(NewTask.objects.get(pk=task.id).password, 'ImportPass456')

        # The secrets the earlier projects stored in cleartext
        from message_center.models import MessageChannel
        from router_manager.models import SSHKey as NewKey
        for table, column, pk, plain, model in (
                ('router_manager_sshkey', 'private_key', key.id, 'FAKE-TEST-PRIVATE-KEY-MATERIAL', NewKey),
                ('message_center_messagechannel', 'token', channel.id, 'FAKE-TEST-BOT-TOKEN', MessageChannel)):
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT {column} FROM {table} WHERE id = %s', [pk])
                stored = cursor.fetchone()[0]
            self.assertTrue(stored.startswith('gAAAAA'), table)
            self.assertNotIn(plain, stored)
            self.assertEqual(getattr(model.objects.get(pk=pk), column), plain)
        self.assertIsNone(MessageChannel.objects.get(name='topic').token)

        # The external integration was removed: its table, and the token in it,
        # are gone from the upgraded database
        self.assertNotIn('integration_manager_externalintegration', connection.introspection.table_names())
        self.assertTrue(integration.pk)

        # The stored CSV keeps no cleartext password
        new_csv = NewCsv.objects.get(pk=csv.id)
        self.assertNotIn('CsvPass789', new_csv.raw_csv_data)
        self.assertNotIn('CsvPass789', json.dumps(new_csv.import_data))

        # Change flags backfilled by the same rule the application applies
        flags = [RouterBackup.objects.get(pk=pk).config_change_detected for pk in backup_ids]
        self.assertEqual(flags, [expected for _, expected in plan])

        # A payload somebody wrote is kept, and only gains what verifying it needs
        variant = CommandVariant.objects.get(pk=own_variant.id)
        for line in USER_UPDATE_PAYLOAD.splitlines():
            self.assertIn(line, variant.payload)
        lines = variant.payload.splitlines()
        self.assertLess(lines.index(':put ("expected-version=" . [/system/package/update/get latest-version])'),
                        lines.index('/system/package/update/install'))
        self.assertEqual(variant.verify_expect, 'installed-version: {{ expected_version }}')
        self.assertEqual(NewCommand.objects.get(pk=own_update.id).verify_timeout, 600)

        # The audit log exists now, and the migration itself wrote nothing to it
        self.assertEqual(ChangeRecord.objects.count(), 0)

    def test_variant_with_its_own_verification_is_left_alone(self):
        old = self.migrate([('fleet_commander', '0005_command_verify_interval_command_verify_timeout_and_more')])
        Command = old.get_model('fleet_commander', 'Command')
        Variant = old.get_model('fleet_commander', 'CommandVariant')
        command = Command.objects.create(name='Custom')
        Variant.objects.create(command_id=command.id, router_type='routeros', payload=USER_UPDATE_PAYLOAD,
                               verify_payload='/my/check', verify_expect='my result')
        self.migrate(self.latest())
        from fleet_commander.models import CommandVariant
        variant = CommandVariant.objects.get(command_id=command.id)
        self.assertEqual((variant.payload, variant.verify_payload, variant.verify_expect),
                         (USER_UPDATE_PAYLOAD, '/my/check', 'my result'))


# The last migration of every app in the later predecessor project (see docs/upgrading.md)
LATER_PREDECESSOR_STATE = [
    ('audit_log', '0001_initial'),
    ('backup', '0009_backupprofile_parameter_sensitive_and_more'),
    ('backup_data', '0012_backfill_config_change_detected'),
    ('fleet_commander', '0011_commandschedule_last_missed'),
    ('import_tool', '0008_encrypt_import_passwords'),
    ('integration_manager', '0002_alter_externalintegration_wireguard_webadmin_default_user_level'),
    ('message_center', '0007_alter_notification_notification_type'),
    ('monitoring', '0001_initial'),
    ('router_manager', '0023_routerinformation_available_version_and_more'),
    ('routerfleet_tools', '0003_webadminsettings_monitoring_last_run_and_more'),
    ('user_manager', '0003_useracl_address_link'),
]


class UpgradeFromLaterPredecessorTests(TransactionTestCase):
    """A database left by the later predecessor project (see docs/upgrading.md) upgrades in place: what it had
    encrypted stays readable, what it stored in cleartext is encrypted, and its
    audit trail and schedules come through untouched."""

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state([t for t in targets if t[1]]).apps

    def test_upgrade(self):
        old = self.migrate(LATER_PREDECESSOR_STATE)
        Profile = old.get_model('backup', 'BackupProfile')
        existing_profile = Profile.objects.create(name='existing-profile')

        Router = old.get_model('router_manager', 'Router')
        router = Router.objects.create(name='edge-01', address='10.0.0.1', router_type='routeros',
                                       password='AlreadyEncrypted1')
        Group = old.get_model('router_manager', 'RouterGroup')
        group = Group.objects.create(name='Type: AP')
        group.routers.add(router)
        SSHKey = old.get_model('router_manager', 'SSHKey')
        key = SSHKey.objects.create(name='k', public_key='ssh-ed25519 AAAA',
                                    private_key='FAKE-TEST-PRIVATE-KEY-MATERIAL')
        Channel = old.get_model('message_center', 'MessageChannel')
        channel = Channel.objects.create(name='tg', channel_type='telegram', destination='42',
                                         token='FAKE-TEST-BOT-TOKEN')
        Command = old.get_model('fleet_commander', 'Command')
        Schedule = old.get_model('fleet_commander', 'CommandSchedule')
        command = Command.objects.create(name='Update')
        schedule = Schedule.objects.create(command_id=command.id, repeat_interval=10080)
        schedule.router_group.add(group)
        schedule.exclude_router.add(router)
        Change = old.get_model('audit_log', 'ChangeRecord')
        Change.objects.create(action='changed', model_label='router_manager.Router', object_repr='edge-01',
                              changes={'Port': {'old': 22, 'new': 2222}})
        Login = old.get_model('audit_log', 'LoginRecord')
        Login.objects.create(username='admin', successful=True)

        with self.assertLogs('routerlib.encryption', level='WARNING'):
            self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

        # An upgrade does not change how an installation stores its backups: a
        # profile that existed keeps every backup; only new ones keep changes
        from backup.models import BackupProfile as NewProfile
        self.assertTrue(NewProfile.objects.get(pk=existing_profile.pk).keep_unchanged)
        self.assertFalse(NewProfile.objects.create(name='created-after-upgrade').keep_unchanged)
        # The system commands arrive with the upgrade, all of them
        from fleet_commander.models import Command as NewCommand
        from fleet_commander.system_commands import SYSTEM_KEYS
        self.assertEqual(sorted(NewCommand.objects.exclude(system_key=None).values_list('system_key', flat=True)),
                         sorted(SYSTEM_KEYS))

        from audit_log.models import ChangeRecord, LoginRecord
        from fleet_commander.models import CommandSchedule
        from message_center.models import MessageChannel
        from router_manager.models import Router as NewRouter, SSHKey as NewKey

        # What Extended had encrypted is still readable
        self.assertEqual(NewRouter.objects.get(pk=router.id).password, 'AlreadyEncrypted1')
        # What it stored in cleartext is now encrypted and still readable
        for table, column, pk, plain, model in (
                ('router_manager_sshkey', 'private_key', key.id, 'FAKE-TEST-PRIVATE-KEY-MATERIAL', NewKey),
                ('message_center_messagechannel', 'token', channel.id, 'FAKE-TEST-BOT-TOKEN', MessageChannel)):
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT {column} FROM {table} WHERE id = %s', [pk])
                stored = cursor.fetchone()[0]
            self.assertTrue(stored.startswith('gAAAAA'), table)
            self.assertEqual(getattr(model.objects.get(pk=pk), column), plain)
        # The audit trail and the schedule's selection come through untouched
        self.assertEqual(ChangeRecord.objects.get().changes, {'Port': {'old': 22, 'new': 2222}})
        self.assertEqual(LoginRecord.objects.get().username, 'admin')
        upgraded = CommandSchedule.objects.get(pk=schedule.id)
        self.assertEqual(list(upgraded.exclude_router.values_list('name', flat=True)), ['edge-01'])
        self.assertEqual(upgraded.target_routers(), set())


class EditedBackupCommandsUpgradeTests(TransactionTestCase):
    """An installation whose backup system commands were edited keeps what
    they did: OpenWrt's first "create" line - whose output was the text
    backup - becomes the text command; edited RouterOS commands stay, and
    their downloaded .rsc stays the text backup."""

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps

    def test_edited_commands(self):
        before = [('fleet_commander', '0015_variant_device_type'), ('router_manager', '0031_device_type_presets'),
                  ('import_tool', '0009_import_device_type')]
        old = self.migrate(before)
        Variant = old.get_model('fleet_commander', 'CommandVariant')
        Command = old.get_model('fleet_commander', 'Command')
        # Going back does not undo data: the rows as they were before the text command
        Variant.objects.filter(command__system_key__in=('backup_text', 'firmware_verify', 'firmware_install')).delete()
        Command.objects.filter(system_key__in=('backup_text', 'firmware_verify', 'firmware_install')).delete()
        edited_routeros = '/system backup save name={{ backup_name }}.backup\n/export file={{ backup_name }}.rsc compact'
        # Written whether or not an earlier test's database reset left them
        for key, router_type, payload in (
                ('backup_download', 'routeros', '/{{ backup_name }}.rsc\n/{{ backup_name }}.backup'),
                ('backup_create', 'openwrt', 'uci export network\nsysupgrade --create-backup /tmp/{{ backup_name }}.tar.gz'),
                ('backup_create', 'routeros', edited_routeros)):
            command, _ = Command.objects.get_or_create(system_key=key, defaults={'name': f'System: {key}'})
            Variant.objects.update_or_create(command=command, router_type=router_type,
                                             defaults={'payload': payload, 'enabled': True})
        executor = MigrationExecutor(connection)
        self.migrate(executor.loader.graph.leaf_nodes())
        from fleet_commander.models import CommandVariant

        def payload(key, router_type):
            return CommandVariant.objects.get(command__system_key=key, router_type=router_type).payload
        self.assertEqual(payload('backup_text', 'openwrt'), 'configuration: uci export network')
        self.assertEqual(payload('backup_create', 'openwrt'), 'sysupgrade --create-backup /tmp/{{ backup_name }}.tar.gz')
        self.assertEqual(payload('backup_text', 'routeros'), '')
        self.assertEqual(payload('backup_create', 'routeros'), edited_routeros)
        self.assertEqual(payload('backup_download', 'routeros'), '/{{ backup_name }}.rsc\n/{{ backup_name }}.backup')
