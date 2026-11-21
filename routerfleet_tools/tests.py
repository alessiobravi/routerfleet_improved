from unittest import mock

from django.template import Context, Template
from django.test import TestCase, override_settings

from routerfleet.testing import make_user
from routerfleet_tools.models import WebadminSettings


class UpdateCheckTests(TestCase):
    def test_off_by_default_and_no_request_leaves(self):
        with mock.patch('routerfleet_tools.views.requests.get') as get:
            data = self.client.get('/cron/check_updates/').json()
        get.assert_not_called()
        self.assertEqual(data, {'update_available': False})
        # The scheduler heartbeat is still recorded
        self.assertIsNotNone(WebadminSettings.objects.get().cron_last_run)

    @override_settings(UPDATE_CHECK_URL='https://updates.example.org/check')
    def test_configured_endpoint_is_asked_with_a_timeout(self):
        response = mock.Mock(json=lambda: {'update_available': True, 'current_version': '0.9.0'})
        with mock.patch('routerfleet_tools.views.requests.get', return_value=response) as get:
            data = self.client.get('/cron/check_updates/').json()
        url = get.call_args.args[0]
        from django.conf import settings
        self.assertEqual(url, f'https://updates.example.org/check?app=routerfleet&version={settings.ROUTERFLEET_RELEASE}')
        self.assertEqual(get.call_args.kwargs['timeout'], 10)
        self.assertTrue(data['update_available'])
        self.assertEqual(WebadminSettings.objects.get().latest_version, 9000)

    @override_settings(UPDATE_CHECK_URL='https://updates.example.org/check?token=x')
    def test_existing_query_string_is_extended(self):
        with mock.patch('routerfleet_tools.views.requests.get',
                        return_value=mock.Mock(json=lambda: {})) as get:
            self.client.get('/cron/check_updates/')
        self.assertIn('?token=x&app=routerfleet', get.call_args.args[0])

    @override_settings(UPDATE_CHECK_URL='https://updates.example.org/check')
    def test_unreachable_endpoint_reports_no_update(self):
        with mock.patch('routerfleet_tools.views.requests.get', side_effect=OSError('down')):
            data = self.client.get('/cron/check_updates/').json()
        self.assertEqual(data, {'update_available': False})


class TemplateTagTests(TestCase):
    def render(self, text):
        return Template('{% load custom_tags %}' + text).render(Context())

    def test_version_tag(self):
        self.render('{% tag_webadmin_version as v %}')
        settings = WebadminSettings.objects.get()
        self.assertFalse(settings.update_available)

    @override_settings(UPDATE_CHANGELOG_URL='https://x/changelog')
    def test_changelog_url_reaches_the_page(self):
        self.client.force_login(make_user(10))
        WebadminSettings.objects.create(name='webadmin_settings', update_available=True, latest_version=99999)
        response = self.client.get('/')
        self.assertContains(response, 'https://x/changelog')

    def test_project_links(self):
        with self.settings(PROJECT_REPOSITORY_URL='R', PROJECT_SUPPORT_URL='S'):
            self.assertEqual(self.render('{% tag_project_repository_url %}|{% tag_project_support_url %}'), 'R|S')

    def test_no_upstream_update_server_anywhere_in_the_page(self):
        self.client.force_login(make_user(10))
        self.assertNotContains(self.client.get('/'), 'eth0.com.br')


class VersioningTests(TestCase):
    def test_numbers(self):
        from routerfleet.versioning import from_number, parse, to_number
        self.assertEqual(parse('0.8.0'), (0, 8, 0))
        self.assertEqual(to_number('0.8.0'), 8000)
        self.assertEqual(to_number('1.2.3'), 1_002_003)
        self.assertEqual(from_number(8001), '0.8.1')
        for release in ('0.8.0', '0.9.12', '2.0.999'):
            self.assertEqual(from_number(to_number(release)), release)

    def test_never_below_the_old_scheme(self):
        # Installations upgraded from 0.7516 and earlier must see the number rise
        from routerfleet.versioning import to_number
        self.assertGreater(to_number('0.8.0'), 7516)

    def test_bump(self):
        from routerfleet.versioning import bump
        self.assertEqual(bump('0.8.0', 'patch'), '0.8.1')
        self.assertEqual(bump('0.8.4', 'minor'), '0.9.0')
        self.assertEqual(bump('0.9.3', 'major'), '1.0.0')
        with self.assertRaises(ValueError):
            bump('0.8.0', 'tiny')

    def test_invalid(self):
        from routerfleet.versioning import parse
        for value in ('0.7516', 'v0.8.0', '0.8', '0.1000.0', ''):
            with self.assertRaises(ValueError, msg=value):
                parse(value)

    def test_settings_derive_the_number(self):
        from django.conf import settings
        from routerfleet.versioning import to_number
        self.assertEqual(settings.ROUTERFLEET_VERSION, to_number(settings.ROUTERFLEET_RELEASE))

    def test_release_script(self):
        import pathlib, subprocess, sys
        script = pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'version.py'
        run = lambda *a: subprocess.run([sys.executable, '-I', str(script), *a], capture_output=True, text=True)
        from django.conf import settings
        self.assertEqual(run().stdout.strip(), settings.ROUTERFLEET_RELEASE)
        self.assertEqual(run('check').returncode, 0)
        self.assertEqual(run('newer-than', 'v0.7516', 'refs/tags/v0.0.1').returncode, 0)
        self.assertNotEqual(run('newer-than', f'v{settings.ROUTERFLEET_RELEASE}').returncode, 0)

    def test_sidebar_shows_the_release(self):
        from django.conf import settings
        self.client.force_login(make_user(10))
        self.assertContains(self.client.get('/'), f'<b>Version</b> {settings.ROUTERFLEET_RELEASE}')

    def test_stale_update_flag_is_cleared_when_no_check_is_configured(self):
        WebadminSettings.objects.create(name='webadmin_settings', update_available=True, latest_version=9999999)
        self.client.get('/cron/check_updates/')
        self.assertFalse(WebadminSettings.objects.get().update_available)


class ImagePruneTests(TestCase):
    """scripts/prune_images.py decides what may be deleted from the registry."""

    def script(self):
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            'prune_images', pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'prune_images.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_multi_platform_parts_of_a_release_are_protected(self):
        versions = [
            {'id': 1, 'name': 'sha256:index-110', 'tags': ['1.1.0', 'latest']},
            {'id': 2, 'name': 'sha256:amd64-110', 'tags': []},
            {'id': 3, 'name': 'sha256:arm64-110', 'tags': []},
            {'id': 4, 'name': 'sha256:single-100', 'tags': ['1.0.0']},
            {'id': 5, 'name': 'sha256:leftover', 'tags': []},
            {'id': 6, 'name': 'sha256:old-scheme', 'tags': ['0.7516']},
            {'id': 7, 'name': 'sha256:untagged-index', 'tags': []},
            {'id': 8, 'name': 'sha256:its-part', 'tags': []},
        ]
        children = {'1.1.0': ['sha256:amd64-110', 'sha256:arm64-110'], 'latest': [], '1.0.0': []}
        kept, delete = self.script().plan(versions, lambda tag: children[tag])
        self.assertEqual({v['id'] for v in kept}, {1, 4})
        # 2 and 3 are untagged but belong to the 1.1.0 release
        self.assertEqual({v['id'] for v in delete}, {5, 6, 7, 8})

    def test_releases_below_the_floor_go_with_their_parts(self):
        versions = [
            {'id': 1, 'name': 'sha256:new', 'tags': ['0.10.0', 'latest']},
            {'id': 2, 'name': 'sha256:withdrawn-index', 'tags': ['0.9.3']},
            {'id': 3, 'name': 'sha256:withdrawn-part', 'tags': []},
        ]
        children = {'0.10.0': [], 'latest': [], '0.9.3': ['sha256:withdrawn-part']}
        kept, delete = self.script().plan(versions, lambda tag: children[tag])
        self.assertEqual({v['id'] for v in kept}, {1})
        self.assertEqual({v['id'] for v in delete}, {2, 3})

    def test_floor(self):
        from routerfleet.versioning import RELEASE_FLOOR, is_retired, to_number
        self.assertTrue(is_retired('0.9.3'))
        self.assertFalse(is_retired(RELEASE_FLOOR))
        self.assertFalse(is_retired('latest'))
        from django.conf import settings
        self.assertGreaterEqual(to_number(settings.ROUTERFLEET_RELEASE), to_number(RELEASE_FLOOR))

    def test_release_pattern(self):
        pattern = self.script().RELEASE_TAG
        for tag in ('latest', '0.9.0', '12.34.567'):
            self.assertTrue(pattern.match(tag), tag)
        for tag in ('0.7516', 'v0.9.0', 'sha-abc', '0.9', ''):
            self.assertFalse(pattern.match(tag), tag)

    def test_nothing_to_delete(self):
        versions = [{'id': 1, 'name': 'sha256:a', 'tags': ['1.0.0']}]
        self.assertEqual(self.script().plan(versions, lambda tag: [])[1], [])


class CveReportTests(TestCase):
    def script(self):
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            'cve_report', pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'cve_report.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_lists_what_remains_per_image(self):
        clean = {'ArtifactName': 'ghcr.io/x/routerfleet:1.0.0', 'Results': [{'Vulnerabilities': []}]}
        db = {'ArtifactName': 'ghcr.io/x/routerfleet-postgres:1.0.0', 'Results': [{'Vulnerabilities': [
            {'VulnerabilityID': 'CVE-1', 'Severity': 'HIGH', 'PkgName': 'libfoo', 'InstalledVersion': '1'},
            {'VulnerabilityID': 'CVE-2', 'Severity': 'LOW', 'PkgName': 'libbar', 'InstalledVersion': '2',
             'FixedVersion': '3'},
        ]}]}
        text = self.script().section('0.76.2', [clean, db])
        self.assertIn('**ghcr.io/x/routerfleet:1.0.0** — none', text)
        self.assertIn('1 high, 1 low (1 without a published fix)', text)
        self.assertIn('CVE-1 high — libfoo (no fix yet)', text)
        self.assertIn('CVE-2 low — libbar (fixed in 3)', text)
        self.assertLess(text.index('CVE-1'), text.index('CVE-2'))

    def test_one_line_per_vulnerability_and_long_lists_fold(self):
        vulns = [{'VulnerabilityID': 'CVE-K', 'Severity': 'HIGH', 'PkgName': p} for p in ('libkrb5-3', 'libk5crypto3')]
        vulns += [{'VulnerabilityID': f'CVE-{i:02}', 'Severity': 'LOW', 'PkgName': 'x'} for i in range(12)]
        text = self.script().section('0.76.2', [{'ArtifactName': 'db', 'Results': [{'Vulnerabilities': vulns}]}])
        self.assertEqual(text.count('CVE-K'), 1)
        self.assertIn('CVE-K high — libk5crypto3, libkrb5-3 (no fix yet)', text)
        self.assertIn('<details><summary>13 vulnerabilities</summary>', text)
