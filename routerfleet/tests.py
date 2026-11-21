"""Who may reach what.

Every page is tested from three sides: an anonymous visitor, a user one level
below the page's gate, and a user exactly at it. The table below is the
permission model of the application; docs/users.md describes the same table.
"""
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import get_resolver

from audit_log.models import ChangeRecord
from backup_data.models import RouterBackup
from fleet_commander.models import Command, CommandJob, CommandSchedule, CommandTask, CommandVariant
from import_tool.models import CsvData, ImportTask
from message_center.models import MessageChannel
from routerfleet.testing import make_group, make_profile, make_router, make_sshkey, make_user
from user_manager.models import UserAcl

DENIED_TEMPLATE = 'access_denied.html'


class Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.profile = make_profile(name='default')
        cls.router = make_router(backup_profile=cls.profile)
        cls.group = make_group(routers=[cls.router])
        cls.sshkey = make_sshkey()
        cls.backup = RouterBackup.objects.create(router=cls.router, schedule_type='daily', success=True,
                                                 backup_text='/ip address\n')
        cls.backup2 = RouterBackup.objects.create(router=cls.router, schedule_type='daily', success=True,
                                                  backup_text='/ip address\n/ip route\n')
        cls.command = Command.objects.create(name='Show')
        cls.variant = CommandVariant.objects.create(command=cls.command, router_type='routeros', payload='/x')
        cls.schedule = CommandSchedule.objects.create(command=cls.command)
        cls.job = CommandJob.objects.create(command=cls.command)
        cls.task = CommandTask.objects.create(job=cls.job, router=cls.router, command_variant=cls.variant)
        cls.channel = MessageChannel.objects.create(name='ops', channel_type='ntfy', destination='t')
        cls.csv = CsvData.objects.create(raw_csv_data='x', import_data=[])
        cls.import_task = ImportTask.objects.create(csv_data=cls.csv, name='n', username='u', address='a',
                                                    router_type='routeros', import_id=1)
        cls.change = ChangeRecord.objects.create(action='changed', model_label='router_manager.Router')
        cls.admin_acl = UserAcl.objects.get(user=make_user(50))

    def gated(self):
        """(path, minimum level, how a refusal looks: 'page' or 'json')."""
        r, b, c = self.router.uuid, self.backup.uuid, self.command.uuid
        return [
            (f'/backup/backup_details/?uuid={b}', 20, 'page'),
            (f'/backup/compare/?uuid={b}&compare_uuid={self.backup2.uuid}', 20, 'page'),
            (f'/backup/config_change/?uuid={self.backup2.uuid}', 20, 'json'),
            (f'/backup/download/?uuid={b}&type=text', 20, 'page'),
            (f'/router/create_instant_backup/?uuid={r}', 20, 'page'),
            ('/router/manage_groups/multiple/', 20, 'page'),
            ('/fleet_commander/', 20, 'page'),
            (f'/fleet_commander/command/details/?uuid={c}', 20, 'page'),
            ('/fleet_commander/job/list/', 20, 'page'),
            (f'/fleet_commander/job/details/?uuid={self.job.uuid}', 20, 'page'),
            (f'/fleet_commander/task/details/?uuid={self.task.uuid}', 20, 'page'),
            (f'/fleet_commander/task/abort/?uuid={self.task.uuid}', 20, 'page'),
            (f'/fleet_commander/job/abort/?uuid={self.job.uuid}', 20, 'page'),
            ('/message_center/channel_list/', 20, 'page'),
            ('/message_center/message_history/', 20, 'page'),

            (f'/router/manage/?uuid={r}', 30, 'page'),
            (f'/backup/delete/?uuid={b}', 30, 'page'),
            ('/router/edit/multiple/', 30, 'page'),

            (f'/backup/manage_profile/?uuid={self.profile.uuid}', 40, 'page'),
            (f'/router/manage_group/?uuid={self.group.uuid}', 40, 'page'),
            (f'/router/manage_sshkey/?uuid={self.sshkey.uuid}', 40, 'page'),
            (f'/fleet_commander/command/manage/?uuid={c}', 40, 'page'),
            (f'/fleet_commander/command/execute/?command_uuid={c}', 40, 'page'),
            ('/fleet_commander/command/execute_multiple/', 40, 'page'),
            (f'/fleet_commander/variant/manage/?uuid={self.variant.uuid}', 40, 'page'),
            (f'/fleet_commander/schedule/manage/?uuid={self.schedule.uuid}', 40, 'page'),
            ('/fleet_commander/schedule/defaults/', 40, 'page'),
            ('/message_center/manage_settings/', 40, 'page'),
            (f'/message_center/manage_channel/?uuid={self.channel.uuid}', 40, 'page'),

            ('/user/list/', 50, 'page'),
            (f'/user/manage/?uuid={self.admin_acl.uuid}', 50, 'page'),
            ('/audit_log/login_history/', 50, 'page'),
            ('/audit_log/change_log/', 50, 'page'),
            (f'/audit_log/change_detail/?uuid={self.change.uuid}', 50, 'json'),
            ('/router/import_tool/', 50, 'page'),
            ('/router/import_tool/csv/', 50, 'page'),
            (f'/router/import_tool/details/?uuid={self.csv.uuid}', 50, 'page'),
            (f'/router/import_tool/run_import_task/?uuid={self.import_task.uuid}', 50, 'page'),
            ('/wireguard_webadmin/manage/', 50, 'page'),
        ]

    def gated_posts(self):
        """JSON endpoints that only answer POST."""
        return [
            ('/router/create_instant_backup/multiple/', 20),
            ('/router/update_information/multiple/', 30),
        ]

    # Pages behind a sign-in and nothing else
    login_only = [
        '/', '/status/', '/router_status_data/', '/backup_statistics_data/',
        '/router/list/', '/router/group_list/', '/router/ssh_keys/',
        '/backup/profile_list/', '/backup/backup_list/', '/backup/backup_list/?type=queue',
        '/backup/backup_list/?type=errors', '/wireguard_webadmin/', '/user/settings/',
    ]

    def login_only_with_objects(self):
        return [f'/router/details/?uuid={self.router.uuid}',
                f'/router/availability/?uuid={self.router.uuid}']


class AnonymousTests(Fixture):
    def test_every_page_sends_an_anonymous_visitor_to_sign_in(self):
        paths = ([p for p, _, _ in self.gated()] + self.login_only + self.login_only_with_objects()
                 + [p for p, _ in self.gated_posts()])
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response['Location'].startswith('/accounts/login/'), response['Location'])


class LevelGateTests(Fixture):
    def assert_refused(self, response, kind, path):
        if kind == 'json':
            self.assertEqual(response.status_code, 403, path)
        else:
            self.assertTemplateUsed(response, DENIED_TEMPLATE, msg_prefix=path)

    def assert_allowed(self, response, path):
        self.assertNotEqual(response.status_code, 403, path)
        self.assertNotIn(DENIED_TEMPLATE, [t.name for t in response.templates], path)

    def test_one_level_below_is_refused(self):
        for path, level, kind in self.gated():
            with self.subTest(path=path):
                self.client.force_login(make_user(level - 10))
                self.assert_refused(self.client.get(path), kind, path)

    def test_a_user_without_any_level_is_refused(self):
        self.client.force_login(make_user(None))
        for path, _, kind in self.gated():
            with self.subTest(path=path):
                self.assert_refused(self.client.get(path), kind, path)

    @mock.patch('import_tool.views.test_authentication', return_value=(False, 'mocked'))
    @mock.patch('import_tool.views.socket.gethostbyname', return_value='192.0.2.1')
    def test_exactly_at_the_level_is_allowed(self, *_):
        for path, level, _ in self.gated():
            with self.subTest(path=path):
                self.client.force_login(make_user(level))
                self.assert_allowed(self.client.get(path), path)

    def test_json_posts(self):
        for path, level in self.gated_posts():
            with self.subTest(path=path):
                self.client.force_login(make_user(level - 10))
                self.assertEqual(self.client.post(path, {'routers[]': [str(self.router.uuid)]}).status_code, 403)
                self.client.force_login(make_user(level))
                self.assertEqual(self.client.post(path, {'routers[]': [str(self.router.uuid)]}).status_code, 200)
                self.assertEqual(self.client.get(path).status_code, 405)

    def test_login_only_pages_need_no_level(self):
        self.client.force_login(make_user(None))
        for path in self.login_only + self.login_only_with_objects():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertNotIn(DENIED_TEMPLATE, [t.name for t in response.templates])


class CronEndpointTests(TestCase):
    """The scheduler's endpoints: no authentication, but never through a proxy."""

    def cron_paths(self):
        return [pattern.pattern._route for pattern in get_resolver().url_patterns
                if str(pattern.pattern).startswith('cron/')]

    def test_all_cron_routes_are_covered(self):
        self.assertEqual(len(self.cron_paths()), 11)

    @mock.patch('routerlib.router_functions.connect_to_ssh', side_effect=OSError('no network in tests'))
    def test_direct_call_runs(self, _):
        for route in self.cron_paths():
            with self.subTest(route=route):
                response = self.client.get('/' + route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'application/json')

    def test_proxied_call_is_refused(self):
        for route in self.cron_paths():
            with self.subTest(route=route):
                response = self.client.get('/' + route, HTTP_X_REAL_IP='198.51.100.4')
                self.assertEqual(response.status_code, 404)


class DebugEndpointTests(TestCase):
    def test_run_backups_does_nothing_outside_debug(self):
        RouterBackup.objects.create(router=make_router(), schedule_type='instant')
        with mock.patch('backup.views.perform_backup') as perform:
            response = self.client.get('/debug/run_backups/')
        perform.assert_not_called()
        self.assertEqual(response.json()['error'], 'Debug mode is not enabled')

    def test_test_messages_is_not_found_outside_debug(self):
        self.client.force_login(make_user(50))
        self.assertEqual(self.client.get('/debug/test_messages/').status_code, 404)

    @override_settings(DEBUG=True)
    def test_test_messages_in_debug(self):
        self.client.force_login(make_user(50))
        response = self.client.get('/debug/test_messages/')
        self.assertEqual(response.json()['message'], 'No router_uuid or backup_id provided.')


class AdminTests(TestCase):
    def test_admin_url_is_present_until_the_container_comments_it_out(self):
        # entrypoint.sh comments the admin route out of urls.py at every start;
        # in a checkout it is still there, which is what this documents
        routes = [str(p.pattern) for p in get_resolver().url_patterns]
        self.assertIn('admin/', routes)


class MalformedIdentifierTests(Fixture):
    def test_every_page_answers_404_for_a_malformed_id(self):
        import re
        self.client.force_login(make_user(50))
        paths = [p for p, _, _ in self.gated()] + self.login_only_with_objects()
        checked = 0
        for path in paths:
            if 'uuid=' not in path:
                continue
            bad = re.sub(r'((?:^|[?&])(?:uuid|compare_uuid|command_uuid)=)[^&]+', r'\1not-a-uuid', path, count=1)
            with self.subTest(path=bad):
                self.assertEqual(self.client.get(bad).status_code, 404)
                checked += 1
        self.assertGreater(checked, 25)

    def test_other_validation_errors_are_not_hidden(self):
        from django.core.exceptions import ValidationError
        from routerfleet.middleware import is_malformed_uuid
        self.assertFalse(is_malformed_uuid(ValidationError('Enter a valid value.', code='invalid')))
        self.assertFalse(is_malformed_uuid(ValidationError({'name': ['This field is required.']})))
        self.assertTrue(is_malformed_uuid(ValidationError('“x” is not a valid UUID.', code='invalid')))


class NoThirdPartyAssetsTests(TestCase):
    """Pages load nothing from another host: fonts, scripts and styles are
    served by the installation itself."""

    def template_files(self):
        import pathlib
        return sorted(pathlib.Path(__file__).resolve().parent.parent.joinpath('templates').rglob('*.html'))

    def test_templates_reference_no_external_host(self):
        import re
        for path in self.template_files():
            with self.subTest(template=path.name):
                text = path.read_text(encoding='utf-8')
                self.assertEqual(re.findall(r'''(?:src|href)=["']https?://[^"']+''', text), [])
                self.assertNotIn('fonts.googleapis', text)

    def test_every_static_file_a_template_loads_exists(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent / 'static_files'
        for path in self.template_files():
            for ref in re.findall(r'''(?:src|href)=["']/static/([^"'?#]+)''', path.read_text(encoding='utf-8')):
                with self.subTest(template=path.name, ref=ref):
                    self.assertTrue((root / ref).is_file(), ref)

    def test_bundled_font_and_chart_library_carry_their_licenses(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / 'static_files'
        font = root / 'fonts' / 'source-sans-pro'
        self.assertIn('SIL Open Font License', (font / 'LICENSE.md').read_text(encoding='utf-8'))
        css = (font / 'source-sans-pro.css').read_text(encoding='utf-8')
        for face in ('Light', 'Regular', 'It', 'Bold'):
            self.assertIn(f'SourceSansPro-{face}.woff2', css)
            self.assertEqual((font / f'SourceSansPro-{face}.woff2').read_bytes()[:4], b'wOF2')
        chart = root / 'vendor' / 'chart.js-4.6.1'
        self.assertIn('MIT', (chart / 'LICENSE.md').read_text(encoding='utf-8'))
        self.assertIn('Chart.js v4.6.1', (chart / 'chart.umd.min.js').read_text(encoding='utf-8')[:200])


class SecurityHeaderTests(TestCase):
    def test_django_sends_its_security_headers(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['X-Frame-Options'], 'DENY')
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        self.assertEqual(response['Cross-Origin-Opener-Policy'], 'same-origin')


class BrowserLibraryManifestTests(TestCase):
    """third_party/browser-libraries lists what static_files really serves, so
    that the vulnerability scan checks the versions that are deployed."""

    HEADERS = {
        # npm name: (file, pattern that reads the version from its header)
        'jquery': ('AdminLTE-3.2.0/plugins/jquery/jquery.min.js', r'jQuery v([0-9.]+)'),
        'bootstrap': ('AdminLTE-3.2.0/plugins/bootstrap/js/bootstrap.bundle.min.js', r'Bootstrap v([0-9.]+)'),
        'admin-lte': ('AdminLTE-3.2.0/dist/js/adminlte.js', r'AdminLTE v([0-9.]+)'),
        'datatables.net': ('AdminLTE-3.2.0/plugins/datatables/jquery.dataTables.min.js', r'\.version="([0-9.]+)"'),
        'datatables.net-responsive': ('AdminLTE-3.2.0/plugins/datatables-responsive/js/dataTables.responsive.min.js',
                                      r'version="([0-9.]+)"'),
        'overlayscrollbars': ('AdminLTE-3.2.0/plugins/overlayScrollbars/js/jquery.overlayScrollbars.min.js',
                              r'Version: ([0-9.]+)'),
        '@fortawesome/fontawesome-free': ('AdminLTE-3.2.0/plugins/fontawesome-free/css/all.min.css',
                                          r'Font Awesome Free ([0-9.]+)'),
        'chosen-js': ('plugins/chosen/chosen.jquery.min.js', r'v([0-9.]+)'),
        'chart.js': ('vendor/chart.js-4.6.1/chart.umd.min.js', r'Chart\.js v([0-9.]+)'),
    }
    # diff2html's bundle has no version header; it is identified by its digest
    DIFF2HTML_SHA256 = {'3.4.56': 'a2110a09cee157bd'}

    def setUp(self):
        import json
        import pathlib
        self.root = pathlib.Path(__file__).resolve().parent.parent
        lock = json.loads((self.root / 'third_party/browser-libraries/package-lock.json').read_text())
        self.locked = {k.removeprefix('node_modules/'): v['version']
                       for k, v in lock['packages'].items() if k}

    def test_versions_in_the_files_match_the_manifest(self):
        import re
        for name, (path, pattern) in self.HEADERS.items():
            with self.subTest(library=name):
                text = (self.root / 'static_files' / path).read_text(encoding='utf-8', errors='replace')
                found = re.search(pattern, text)
                self.assertIsNotNone(found, f'no version header in {path}')
                self.assertEqual(found.group(1), self.locked[name])

    def test_diff2html_is_the_listed_release(self):
        import hashlib
        digest = hashlib.sha256((self.root / 'static_files/plugins/diff2html/diff2html.min.js').read_bytes())
        self.assertEqual(digest.hexdigest()[:16], self.DIFF2HTML_SHA256[self.locked['diff2html']])

    def test_package_json_and_lock_agree(self):
        import json
        package = json.loads((self.root / 'third_party/browser-libraries/package.json').read_text())
        self.assertEqual(package['dependencies'], self.locked)

    def test_no_demo_pages_or_unused_bundles_are_served(self):
        static = self.root / 'static_files'
        html = [p for p in static.rglob('*.html') if p.name != 'index.html' or p.parent != static]
        self.assertEqual(html, [], 'demo pages would be served from the installation\'s own origin')
        self.assertEqual(list(static.rglob('*.zip')), [])
