"""A database left by the original RouterFleet upgrades in place.

The migration history of this project is a strict superset of the original
project's and of RouterFleet Extended's, so an installation of either is
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
        tables = connection.introspection.table_names()
        self.assertNotIn('audit_log_changerecord', tables)

        # --- written as the original project wrote it
        Router = old.get_model('router_manager', 'Router')
        router = Router.objects.create(name='edge-01', address='10.0.0.1', router_type='routeros',
                                       password='SuperSecret123!')
        Router.objects.create(name='edge-02', address='10.0.0.2', router_type='routeros', password='')

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
        Variant = old.get_model('fleet_commander', 'CommandVariant')
        own_update = Command.objects.create(name='Our update')
        own_variant = Variant.objects.create(command_id=own_update.id, router_type='routeros',
                                             payload=USER_UPDATE_PAYLOAD)

        SSHKey = old.get_model('router_manager', 'SSHKey')
        key = SSHKey.objects.create(name='fleet-key', public_key='ssh-ed25519 AAAA',
                                    private_key='FAKE-TEST-PRIVATE-KEY-MATERIAL')
        Channel = old.get_model('message_center', 'MessageChannel')
        channel = Channel.objects.create(name='tg', channel_type='telegram', destination='42',
                                         token='FAKE-TEST-BOT-TOKEN')
        Channel.objects.create(name='topic', channel_type='ntfy', destination='t', token=None)
        Integration = old.get_model('integration_manager', 'ExternalIntegration')
        integration = Integration.objects.create(name='wireguard_webadmin', integration_type='wireguard_webadmin',
                                                 integration_url='https://wg.example', token='FAKE-TEST-API-KEY')

        # --- the upgrade
        with self.assertLogs('routerlib.encryption', level='WARNING') as logs:
            self.migrate(self.latest())
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

        # The secrets that were stored in cleartext until 0.8.2
        from integration_manager.models import ExternalIntegration
        from message_center.models import MessageChannel
        from router_manager.models import SSHKey as NewKey
        for table, column, pk, plain, model in (
                ('router_manager_sshkey', 'private_key', key.id, 'FAKE-TEST-PRIVATE-KEY-MATERIAL', NewKey),
                ('message_center_messagechannel', 'token', channel.id, 'FAKE-TEST-BOT-TOKEN', MessageChannel),
                ('integration_manager_externalintegration', 'token', integration.id, 'FAKE-TEST-API-KEY',
                 ExternalIntegration)):
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT {column} FROM {table} WHERE id = %s', [pk])
                stored = cursor.fetchone()[0]
            self.assertTrue(stored.startswith('gAAAAA'), table)
            self.assertNotIn(plain, stored)
            self.assertEqual(getattr(model.objects.get(pk=pk), column), plain)
        self.assertIsNone(MessageChannel.objects.get(name='topic').token)

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
