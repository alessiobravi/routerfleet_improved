from unittest import mock

from django.db import connection
from django.test import TestCase

from import_tool.forms import CsvDataForm
from import_tool.models import CsvData, ImportTask
from router_manager.models import Router, RouterGroup
from routerfleet.testing import make_group, make_profile, make_sshkey, make_user

HEADER = 'name,username,password,ssh_key,address,port,router_type,backup_profile,router_group,monitoring\n'


def csv(*rows):
    return HEADER + ''.join(row + '\n' for row in rows)


class ValidationTests(TestCase):
    def errors(self, raw, **extra):
        form = CsvDataForm({'raw_csv_data': raw, **extra})
        form.is_valid()
        return ' '.join(str(e) for e in form.errors.get('__all__', []))

    def test_valid(self):
        make_profile(name='default')
        form = CsvDataForm({'raw_csv_data': csv('e1,admin,pw,,192.0.2.1,22,routeros,default,,true')})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['import_data'][0]['import_id'], 1)

    def test_header_must_match_exactly(self):
        self.assertIn('header does not match', self.errors('name,address\nx,y\n'))

    def test_line_rules(self):
        cases = {
            'e1,admin,,,192.0.2.1,22,routeros,,,true': "Either 'password' or 'ssh_key'",
            'e1,admin,pw,key,192.0.2.1,22,routeros,,,true': 'cannot be provided together',
            'e1,admin,pw,,192.0.2.1,22,cisco,,,true': "Invalid router_type 'cisco'",
            'e1,admin,pw,,192.0.2.1,22,routeros,,,yes': "Invalid value for 'monitoring'",
            'e1,admin,pw,,192.0.2.1,99999,routeros,,,true': "Invalid port '99999'",
            'e1,admin,pw,,192.0.2.1,22,routeros,nope,,true': "Backup Profile 'nope' does not exist",
            'e1,admin,,ghost,192.0.2.1,22,routeros,,,true': "SSH Key 'ghost' does not exist",
        }
        for row, message in cases.items():
            with self.subTest(row=row):
                self.assertIn(message, self.errors(csv(row)))

    def test_duplicate_name_names_the_line(self):
        self.assertIn('line 3', self.errors(csv('e1,a,pw,,192.0.2.1,22,routeros,,,true',
                                                'e1,a,pw,,192.0.2.2,22,routeros,,,true')))

    def test_blank_lines_are_skipped(self):
        form = CsvDataForm({'raw_csv_data': csv('e1,a,pw,,192.0.2.1,22,routeros,,,true', ',,,,,,,,,')})
        self.assertTrue(form.is_valid(), form.errors)

    def test_all_missing_groups_are_listed_at_once(self):
        message = self.errors(csv('e1,a,pw,,192.0.2.1,22,routeros,,Alpha,true',
                                  'e2,a,pw,,192.0.2.2,22,routeros,,Beta,true'))
        self.assertIn("'Alpha'", message)
        self.assertIn("'Beta'", message)

    def test_missing_groups_accepted_with_the_box_ticked(self):
        form = CsvDataForm({'raw_csv_data': csv('e1,a,pw,,192.0.2.1,22,routeros,,Alpha,true'),
                            'create_missing_groups': True})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['missing_groups'], ['Alpha'])


@mock.patch('import_tool.views.socket.gethostbyname', return_value='192.0.2.1')
@mock.patch('import_tool.views.test_authentication', return_value=(True, 'ok'))
class ImportFlowTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(50))

    def upload(self, raw, **extra):
        return self.client.post('/router/import_tool/csv/', {'raw_csv_data': raw, **extra})

    def test_three_steps(self, auth, _dns):
        make_group(name='Existing')
        self.upload(csv('e1,admin,s3cret,,192.0.2.1,22,routeros,,Existing,true',
                        'e2,admin,s3cret,,192.0.2.2,22,openwrt,,New,false'), create_missing_groups='on')
        self.assertTrue(RouterGroup.objects.filter(name='New').exists())
        data = CsvData.objects.get()
        # Step 1 stores, nothing is created yet
        self.assertFalse(Router.objects.exists())
        self.assertNotIn('s3cret', data.raw_csv_data)
        self.assertIn('********', data.raw_csv_data)
        self.assertNotIn('s3cret', str(data.import_data))

        # Step 2 creates one task per row, idempotently
        self.client.post(f'/router/import_tool/details/?uuid={data.uuid}', {'action': 'create_tasks'})
        self.client.post(f'/router/import_tool/details/?uuid={data.uuid}', {'action': 'create_tasks'})
        tasks = list(ImportTask.objects.order_by('import_id'))
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].password, 's3cret')
        with connection.cursor() as cursor:
            cursor.execute('SELECT password FROM import_tool_importtask WHERE id = %s', [tasks[0].id])
            self.assertTrue(cursor.fetchone()[0].startswith('gAAAAA'))

        # Step 3 runs each task
        for task in tasks:
            result = self.client.post(f'/router/import_tool/run_import_task/?uuid={task.uuid}').json()
            self.assertEqual(result['status'], 'success', result)
        e1 = Router.objects.get(name='e1')
        self.assertEqual(e1.password, 's3cret')
        self.assertIn(e1, RouterGroup.objects.get(name='Existing').routers.all())
        self.assertFalse(Router.objects.get(name='e2').monitoring)

    def test_reimport_does_not_duplicate_groups(self, auth, _dns):
        raw = csv('e1,a,pw,,192.0.2.1,22,routeros,,G,true')
        self.upload(raw, create_missing_groups='on')
        self.upload(raw, create_missing_groups='on')
        self.assertEqual(RouterGroup.objects.filter(name='G').count(), 1)

    def run_task(self, **fields):
        data = CsvData.objects.create(raw_csv_data='x', import_data=[])
        values = dict(csv_data=data, import_id=1, name='n1', username='u', password='p',
                      address='192.0.2.1', port=22, router_type='routeros')
        values.update(fields)
        task = ImportTask.objects.create(**values)
        result = self.client.post(f'/router/import_tool/run_import_task/?uuid={task.uuid}').json()
        task.refresh_from_db()
        return result, task

    def test_run_failures_are_recorded_on_the_task(self, auth, _dns):
        Router.objects.create(name='dupe', address='x', router_type='routeros')
        cases = [
            ({'name': 'dupe'}, 'already exists'),
            ({'ssh_key_name': 'ghost', 'password': ''}, 'SSH Key with name "ghost" not found'),
            ({'port': 0}, 'Invalid port number'),
            ({'router_type': 'cisco'}, 'Invalid router_type'),
            ({'backup_profile_name': 'ghost'}, 'Backup Profile with name "ghost" not found'),
            ({'router_group_name': 'ghost'}, 'Router Group with name "ghost" not found'),
            ({'password': ''}, 'You must provide a password or an SSH Key'),
        ]
        for fields, message in cases:
            with self.subTest(fields=fields):
                ImportTask.objects.all().delete()
                CsvData.objects.all().delete()
                result, task = self.run_task(**fields)
                self.assertEqual(result['status'], 'error')
                self.assertIn(message, result['error_message'])
                self.assertTrue(task.import_error)

    def test_authentication_failure(self, auth, _dns):
        auth.return_value = (False, 'Device is not OpenWRT')
        result, _ = self.run_task()
        self.assertEqual(result['error_message'], 'Could not authenticate: Device is not OpenWRT')

    def test_key_based_import(self, auth, _dns):
        key = make_sshkey(name='fleet-key')
        result, task = self.run_task(password='', ssh_key_name='fleet-key')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(Router.objects.get(name='n1').ssh_key, key)

    def test_finished_task_cannot_run_twice(self, auth, _dns):
        _, task = self.run_task()
        response = self.client.post(f'/router/import_tool/run_import_task/?uuid={task.uuid}')
        self.assertEqual(response.status_code, 404)

    def test_delete_errors_and_delete_all(self, auth, _dns):
        _, task = self.run_task(port=0)
        data = task.csv_data
        self.client.post(f'/router/import_tool/details/?uuid={data.uuid}', {'action': 'delete_errors'})
        self.assertFalse(ImportTask.objects.exists())
        self.client.post(f'/router/import_tool/details/?uuid={data.uuid}', {'action': 'delete'})
        self.assertFalse(CsvData.objects.exists())

    def test_detail_views(self, auth, _dns):
        _, task = self.run_task()
        for view in ('raw', 'processed', 'tasks'):
            response = self.client.get(f'/router/import_tool/details/?uuid={task.csv_data.uuid}&view={view}')
            self.assertEqual(response.context['import_view'], view)
