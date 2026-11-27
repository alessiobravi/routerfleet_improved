"""Forms and helpers the main tests reach only in part: the CSV import, the
backup profile and the system commands of Fleet Commander."""
from django.test import TestCase

from backup.forms import BackupProfileForm
from backup.models import BackupProfile
from fleet_commander import system_commands
from fleet_commander.models import CommandVariant
from import_tool.forms import CsvDataForm
from routerfleet.testing import make_group, make_profile, make_sshkey

HEADER = 'name,username,password,ssh_key,address,port,router_type,backup_profile,router_group,monitoring\n'


def csv(*rows):
    return HEADER + ''.join(row + '\n' for row in rows)


class CsvImportEdgeTests(TestCase):
    """The CSV form: extra columns, bad addresses and names used on several lines."""

    def form(self, raw, **extra):
        form = CsvDataForm({'raw_csv_data': raw, **extra})
        form.is_valid()
        return form

    def errors(self, raw, **extra):
        return ' '.join(str(e) for e in self.form(raw, **extra).errors.get('__all__', []))

    def test_empty_input_is_only_required(self):
        form = self.form('')
        self.assertIn('raw_csv_data', form.errors)
        self.assertNotIn('__all__', form.errors)

    def test_a_line_with_an_extra_column(self):
        self.assertIn('Unexpected fields', self.errors(csv('e1,a,pw,,192.0.2.1,22,routeros,,,true,surplus')))

    def test_an_address_that_is_neither_ip_nor_host_name(self):
        self.assertIn("Invalid address 'not_a_host'", self.errors(csv('e1,a,pw,,not_a_host,22,routeros,,,true')))

    def test_host_names_are_accepted(self):
        form = self.form(csv('e1,a,pw,,router.example.net,22,routeros,,,true'))
        self.assertTrue(form.is_valid(), form.errors)

    def test_shared_key_profile_and_group_are_checked_once(self):
        make_sshkey(name='fleet-key')
        make_profile(name='nightly')
        make_group(name='Edge')
        form = self.form(csv('e1,a,,fleet-key,192.0.2.1,22,routeros,nightly,Edge,true',
                             'e2,a,,fleet-key,192.0.2.2,22,routeros,nightly,Edge,false'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data['import_data']), 2)
        self.assertEqual(form.cleaned_data['missing_groups'], [])

    def test_missing_groups_are_created_when_asked(self):
        form = self.form(csv('e1,a,pw,,192.0.2.1,22,routeros,,Core,true'), create_missing_groups='on')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['missing_groups'], ['Core'])


class BackupProfileFormTests(TestCase):
    """The backup profile form: the default name, daily days and a backup type."""

    def data(self, instance=None, **changes):
        form = BackupProfileForm(instance=instance or BackupProfile(name='nightly'))
        data = {name: form[name].value() for name in form.fields}
        data.update(changes)
        return {k: v for k, v in data.items() if v is not None and v is not False}

    def test_a_new_profile_with_daily_days(self):
        form = BackupProfileForm(self.data(daily_backup=True, daily_day_monday=True))
        self.assertTrue(form.is_valid(), form.errors)

    def test_daily_needs_a_day(self):
        data = self.data(daily_backup=True)
        for day in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'):
            data.pop(f'daily_day_{day}', None)
        form = BackupProfileForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('at least one day', str(form.errors['__all__']))

    def test_some_backup_type_is_needed(self):
        data = self.data()
        for field in ('daily_backup', 'weekly_backup', 'monthly_backup'):
            data.pop(field, None)
        form = BackupProfileForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('at least one backup type', str(form.errors['__all__']))

    def test_the_default_profile_keeps_its_name(self):
        default = make_profile(name='default')
        form = BackupProfileForm(self.data(instance=default, name='renamed'), instance=default)
        self.assertFalse(form.is_valid())
        self.assertIn('cannot change the default profile name', str(form.errors['__all__']))

    def test_another_saved_profile_can_be_renamed(self):
        profile = make_profile(name='weekly-set')
        form = BackupProfileForm(self.data(instance=profile, name='weekly-renamed'), instance=profile)
        self.assertTrue(form.is_valid(), form.errors)


class SystemCommandEdgeTests(TestCase):
    """System commands: allowed placeholders, a missing variant, blank lines and value parsing."""

    def test_an_allowed_placeholder_passes(self):
        self.assertEqual(system_commands.validate_payload('backup_remove', 'routeros', 'rm {{ backup_name }}'), [])

    def test_a_placeholder_where_none_is_allowed(self):
        problems = system_commands.validate_payload('backup_cleanup', 'routeros', 'rm {{ backup_name }}')
        self.assertIn('none can be used here', problems[0])

    def test_a_required_command_can_not_be_empty(self):
        problems = system_commands.validate_payload('connection_test', 'routeros', '  ')
        self.assertIn('can not be empty', problems[0])

    def test_a_missing_variant_falls_back_to_the_default(self):
        with self.assertLogs('fleet_commander.system_commands', 'WARNING'):
            self.assertEqual(system_commands.payload_of('connection_test', 'no-such-type'), '')

    def test_blank_lines_are_skipped_and_a_missing_value_refused(self):
        variant = CommandVariant.objects.get(command__system_key='backup_remove', router_type='routeros')
        variant.payload = 'first {{ backup_name }}\n\n   \nsecond'
        variant.save()
        self.assertEqual(system_commands.lines('backup_remove', 'routeros', backup_name='b1'),
                         ['first b1', 'second'])
        with self.assertRaisesRegex(system_commands.SystemCommandError, 'no value for'):
            system_commands.lines('backup_remove', 'routeros')

    def test_parse_values(self):
        output = ('[admin@edge] > ignored\r\n'
                  'model: RB5009\n'
                  'serial-number=ABC123\n'
                  'model: second ignored\n'
                  'cpu model : "ARM64"\n'
                  'free text without separator\n'
                  ':starts with a colon\n'
                  'two words: dropped\n')
        self.assertEqual(system_commands.parse_values(output),
                         {'model': 'RB5009', 'serial-number': 'ABC123', 'cpu model': 'ARM64'})
