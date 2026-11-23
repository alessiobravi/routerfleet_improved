"""System commands: what RouterFleet sends to devices, as protected Fleet
Commander commands."""
from django.db.models import ProtectedError
from django.test import TestCase

from audit_log.models import ChangeRecord
from fleet_commander import system_commands
from fleet_commander.models import Command, CommandJob, CommandVariant
from routerfleet.testing import make_router, make_user


class DefinitionTests(TestCase):
    def test_every_system_command_exists_with_a_variant_per_device_type(self):
        for key in system_commands.SYSTEM_KEYS:
            command = Command.objects.get(system_key=key)
            self.assertEqual(sorted(command.variants.values_list('router_type', flat=True)),
                             sorted(system_commands.device_types()), key)

    def test_the_migration_matches_the_defaults(self):
        # The migration holds a frozen copy: a default changed in the code
        # without a migration of its own would not reach existing installations
        for key in system_commands.SYSTEM_KEYS:
            for variant in CommandVariant.objects.filter(command__system_key=key):
                self.assertEqual(variant.payload, system_commands.default_payload(key, variant.router_type),
                                 f'{key} {variant.router_type}')

    def test_the_defaults(self):
        lines = system_commands.lines('backup_create', 'routeros', backup_name='b', export_parameters=' terse')
        self.assertEqual(lines, [], 'RouterOS takes no binary backup')
        self.assertEqual(system_commands.lines('backup_text', 'routeros', export_parameters=' terse'),
                         ['configuration: /export  terse'])
        self.assertEqual(system_commands.lines('backup_text', 'juniper-junos'),
                         ['configuration: show configuration', 'set: show configuration | display set'])
        self.assertEqual(system_commands.lines('connection_test', 'openwrt'), ['ubus call system board'])
        self.assertEqual(system_commands.lines('backup_download', 'ubiquiti-airos', backup_name='b'), ['/tmp/system.cfg'])

    def test_an_unknown_or_missing_placeholder_is_refused(self):
        self.assertTrue(system_commands.validate_payload('backup_cleanup', 'routeros', 'rm {{ backup_name }}'))
        self.assertTrue(system_commands.validate_payload('inventory', 'routeros', '   '))
        self.assertEqual(system_commands.validate_payload('backup_remove', 'openwrt', ''), [])
        CommandVariant.objects.filter(command__system_key='backup_remove', router_type='openwrt') \
            .update(payload='rm /tmp/{{ backup_name }}.tar.gz')
        with self.assertRaises(system_commands.SystemCommandError):
            system_commands.lines('backup_remove', 'openwrt')

    def test_parse_values(self):
        values = system_commands.parse_values('  version: 7.16.2 (stable)\nVERSION_ID="23.05.3"\n'
                                              'board.name=NanoStation\ncpu model\t: MIPS 74Kc\nno separator\n')
        self.assertEqual(values['version'], '7.16.2 (stable)')
        self.assertEqual(values['VERSION_ID'], '23.05.3')
        self.assertEqual(values['board.name'], 'NanoStation')
        self.assertEqual(values['cpu model'], 'MIPS 74Kc')


class ProtectionTests(TestCase):
    def setUp(self):
        self.command = Command.objects.get(system_key='backup_create')
        self.client.force_login(make_user(50))

    def test_can_not_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.command.delete()
        with self.assertRaises(ProtectedError):
            self.command.variants.first().delete()
        self.client.post(f'/fleet_commander/command/manage/?uuid={self.command.uuid}',
                         {'action': 'delete', 'confirmation': 'delete'})
        self.assertTrue(Command.objects.filter(pk=self.command.pk).exists())

    def test_can_not_be_edited_disabled_scheduled_or_run(self):
        self.client.post(f'/fleet_commander/command/manage/?uuid={self.command.uuid}',
                         {'name': 'x', 'enabled': '', 'max_retry': 1, 'retry_interval': 1,
                          'verify_timeout': 1, 'verify_interval': 1})
        self.command.refresh_from_db()
        self.assertTrue(self.command.enabled)
        self.assertFalse(self.command.can_execute)
        self.assertEqual(self.client.get(f'/fleet_commander/command/execute/?command_uuid={self.command.uuid}').status_code, 404)
        self.assertRedirects(self.client.get(f'/fleet_commander/schedule/manage/?command_uuid={self.command.uuid}'),
                             f'/fleet_commander/command/details/?uuid={self.command.uuid}', fetch_redirect_response=False)
        router = make_router()
        self.client.post(f'/fleet_commander/router/execute/?uuid={router.uuid}',
                         {'router': str(router.uuid), 'command': str(self.command.uuid), 'confirmed': '1'})
        self.assertFalse(CommandJob.objects.exists())
        self.assertNotContains(self.client.get(f'/fleet_commander/router/execute/?uuid={router.uuid}'), self.command.name)

    def test_shown_in_their_own_section(self):
        page = self.client.get('/fleet_commander/')
        self.assertContains(page, 'System commands')
        self.assertContains(page, 'System: backup - create')


class EditTests(TestCase):
    def setUp(self):
        self.variant = CommandVariant.objects.get(command__system_key='backup_cleanup', router_type='openwrt')
        self.url = f'/fleet_commander/system/variant/?uuid={self.variant.uuid}'
        self.client.force_login(make_user(50))

    def review(self, payload='rm -f /tmp/routerfleet-backup-*', action='review'):
        return self.client.post(self.url, {'action': action, 'payload': payload})

    def apply(self, page, answer=None, payload=None):
        if answer is None:
            answer = page.context['first'] + page.context['second']
        return self.client.post(self.url, {'action': 'apply', 'answer': str(answer),
                                           'payload': payload if payload is not None else page.context['payload']})

    def test_only_administrators(self):
        self.client.force_login(make_user(40))
        self.assertContains(self.client.get(self.url), 'Access Denied')
        self.assertContains(self.client.post(self.url, {'action': 'apply', 'payload': 'x', 'answer': '1'}), 'Access Denied')

    def test_review_shows_the_change_and_a_red_confirmation_with_a_sum(self):
        page = self.review()
        self.assertContains(page, '+rm -f /tmp/routerfleet-backup-*')
        self.assertContains(page, 'btn btn-danger btn-lg')
        self.assertContains(page, f"{page.context['first']} + {page.context['second']} = ?")
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.payload, 'rm /tmp/routerfleet-backup-*', 'a review changes nothing')

    def test_the_right_sum_applies_and_is_recorded(self):
        self.apply(self.review())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.payload, 'rm -f /tmp/routerfleet-backup-*')
        self.assertTrue(ChangeRecord.objects.filter(model_label='fleet_commander.CommandVariant').exists())

    def test_a_wrong_sum_changes_nothing_and_is_not_retried(self):
        page = self.review()
        right = page.context['first'] + page.context['second']
        self.apply(page, answer=right + 1)
        self.apply(page, answer=right)   # the challenge was used up by the wrong answer
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.payload, 'rm /tmp/routerfleet-backup-*')

    def test_the_applied_payload_must_be_the_reviewed_one(self):
        page = self.review()
        self.apply(page, payload='rm -rf /')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.payload, 'rm /tmp/routerfleet-backup-*')

    def test_without_a_review_nothing_applies(self):
        self.client.post(self.url, {'action': 'apply', 'payload': 'rm -rf /', 'answer': '0'})
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.payload, 'rm /tmp/routerfleet-backup-*')

    def test_invalid_payload_is_refused_at_review(self):
        page = self.review('rm {{ nonsense }}')
        self.assertContains(page, 'Unknown placeholder')
        self.assertNotIn('first', page.context)

    def test_restore_default(self):
        CommandVariant.objects.filter(pk=self.variant.pk).update(payload='true')
        page = self.review(action='restore')
        self.assertTrue(page.context['restoring'])
        self.apply(page)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.payload, 'rm /tmp/routerfleet-backup-*')

    def test_an_edited_command_is_what_a_backup_sends(self):
        self.apply(self.review('rm -f /tmp/routerfleet-backup-* && sync'))
        self.assertEqual(system_commands.lines('backup_cleanup', 'openwrt'), ['rm -f /tmp/routerfleet-backup-* && sync'])
