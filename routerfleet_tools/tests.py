from unittest import mock

from django.template import Context, Template
from django.test import TestCase, override_settings

from routerfleet.testing import make_user
from routerfleet_tools.models import WebadminSettings


class UpdateCheckTests(TestCase):
    """The update check: GitHub's latest release by default, an endpoint of the
    operator's own when one is set, nothing at all when it is turned off.
    No test reaches the network: requests is mocked throughout."""

    def github(self, tag):
        return mock.Mock(json=lambda: {'tag_name': tag}, raise_for_status=lambda: None)

    def check(self, response=None, side_effect=None):
        with mock.patch('routerfleet_tools.views.requests.get', return_value=response, side_effect=side_effect) as get:
            data = self.client.get('/cron/check_updates/').json()
        return data, get

    def test_on_by_default_and_asks_github(self):
        from django.conf import settings
        from routerfleet.versioning import bump
        newer = bump(settings.ROUTERFLEET_RELEASE, 'patch')
        data, get = self.check(self.github(f'v{newer}'))
        self.assertEqual(get.call_args.args[0], settings.UPDATE_RELEASES_API)
        self.assertEqual(get.call_args.kwargs['timeout'], 10)
        self.assertNotIn(settings.ROUTERFLEET_RELEASE, str(get.call_args), 'the request says nothing about the installation')
        self.assertTrue(data['update_available'])
        stored = WebadminSettings.objects.get()
        self.assertTrue(stored.update_available)
        self.assertIsNotNone(stored.cron_last_run)

    def test_the_same_release_is_up_to_date(self):
        from django.conf import settings
        data, _ = self.check(self.github(f'v{settings.ROUTERFLEET_RELEASE}'))
        self.assertFalse(data['update_available'])

    def test_asked_at_most_once_an_hour_also_when_the_source_is_down(self):
        _, get = self.check(side_effect=OSError('down'))
        get.assert_called_once()
        _, get = self.check(self.github('v99.0.0'))
        get.assert_not_called()

    def test_a_failure_keeps_the_previous_answer(self):
        from django.utils import timezone
        self.check(self.github('v99.0.0'))
        WebadminSettings.objects.update(last_checked=timezone.now() - timezone.timedelta(hours=2))
        data, _ = self.check(side_effect=OSError('down'))
        self.assertTrue(data['update_available'])

    @override_settings(UPDATE_CHECK=False)
    def test_off_sends_nothing(self):
        WebadminSettings.objects.create(name='webadmin_settings', update_available=True)
        data, get = self.check(self.github('v99.0.0'))
        get.assert_not_called()
        self.assertEqual(data, {'update_available': False})
        self.assertFalse(WebadminSettings.objects.get().update_available)

    @override_settings(UPDATE_CHECK_URL='https://updates.example.org/check')
    def test_configured_endpoint_is_asked_with_a_timeout(self):
        response = mock.Mock(json=lambda: {'update_available': True, 'current_version': '99.0.0'},
                             raise_for_status=lambda: None)
        data, get = self.check(response)
        from django.conf import settings
        self.assertEqual(get.call_args.args[0],
                         f'https://updates.example.org/check?app=routerfleet&version={settings.ROUTERFLEET_RELEASE}')
        self.assertEqual(get.call_args.kwargs['timeout'], 10)
        self.assertTrue(data['update_available'])
        self.assertEqual(WebadminSettings.objects.get().latest_version, 99000000)

    @override_settings(UPDATE_CHECK_URL='https://updates.example.org/check?token=x')
    def test_existing_query_string_is_extended(self):
        _, get = self.check(mock.Mock(json=lambda: {}, raise_for_status=lambda: None))
        self.assertIn('?token=x&app=routerfleet', get.call_args.args[0])


class UpdateIndicatorTests(TestCase):
    """The top bar's download icon, on every page: green on the latest
    release, yellow when there is an update, grey when off or not checked."""

    def setUp(self):
        self.client.force_login(make_user(10))

    def indicator(self, path='/status/'):
        import re
        page = self.client.get(path).content.decode()
        match = re.search(r'<a href="([^"]*)" class="nav-link" id="update-indicator" title="([^"]*)"[^>]*>\s*'
                          r'<i class="fas fa-cloud-download-alt ([a-z-]+)">', page)
        self.assertIsNotNone(match, 'the indicator is in the top bar')
        return match.groups()

    def test_not_checked_yet_then_up_to_date(self):
        self.assertEqual(self.indicator()[2], 'text-secondary')
        from django.utils import timezone
        WebadminSettings.objects.update_or_create(name='webadmin_settings', defaults={'last_checked': timezone.now()})
        href, title, color = self.indicator()
        self.assertEqual(color, 'text-success')
        self.assertIn('the latest release', title)

    def test_an_update_is_yellow_with_its_version(self):
        from django.utils import timezone
        WebadminSettings.objects.update_or_create(name='webadmin_settings', defaults={
            'last_checked': timezone.now(), 'update_available': True, 'latest_version': 99001002})
        href, title, color = self.indicator('/about/')
        self.assertEqual(color, 'text-warning')
        self.assertIn('99.1.2', title)
        self.assertTrue(href.endswith('/releases/latest'))

    @override_settings(UPDATE_CHECK=False)
    def test_off_is_grey(self):
        href, title, color = self.indicator()
        self.assertEqual((color, href), ('text-secondary', '/about/'))
        self.assertIn('update check off', title)

    def test_no_version_card_on_the_status_page(self):
        titles = [card['title'] for card in self.client.get('/status/').context['cards']]
        self.assertNotIn('RouterFleet Improved', titles)


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
        response = self.client.get('/about/')
        self.assertContains(response, 'https://x/changelog')

    def test_project_links(self):
        with self.settings(PROJECT_REPOSITORY_URL='R', PROJECT_SUPPORT_URL='S'):
            self.assertEqual(self.render('{% tag_project_repository_url %}|{% tag_project_support_url %}'), 'R|S')

    def test_no_upstream_update_server_anywhere_in_the_page(self):
        self.client.force_login(make_user(10))
        self.assertNotContains(self.client.get('/about/'), 'eth0' + '.com.br')  # split: the repo names no other project


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
        self.assertContains(self.client.get('/about/'), f'<b>Version</b> {settings.ROUTERFLEET_RELEASE}')

    @override_settings(UPDATE_CHECK=False)
    def test_stale_update_flag_is_cleared_when_the_check_is_off(self):
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
        kept, delete = self.script().plan(versions, lambda tag: children[tag], '1.1.0')
        self.assertEqual({v['id'] for v in kept}, {1})
        # 2 and 3 are untagged but belong to the 1.1.0 release; 1.0.0 is older
        self.assertEqual({v['id'] for v in delete}, {4, 5, 6, 7, 8})

    def test_older_releases_go_with_their_parts(self):
        versions = [
            {'id': 1, 'name': 'sha256:new', 'tags': ['0.11.3', 'latest']},
            {'id': 2, 'name': 'sha256:older-index', 'tags': ['0.11.2']},
            {'id': 3, 'name': 'sha256:older-part', 'tags': []},
        ]
        children = {'0.11.3': [], 'latest': [], '0.11.2': ['sha256:older-part']}
        kept, delete = self.script().plan(versions, lambda tag: children[tag], '0.11.3')
        self.assertEqual({v['id'] for v in kept}, {1})
        self.assertEqual({v['id'] for v in delete}, {2, 3})

    def test_only_the_newest_release_is_kept(self):
        from routerfleet.versioning import is_retired
        self.assertTrue(is_retired('0.11.2', '0.11.3'))
        self.assertTrue(is_retired('0.9.3', '0.11.3'))
        self.assertFalse(is_retired('0.11.3', '0.11.3'))
        self.assertFalse(is_retired('0.12.0', '0.11.3'), 'a newer one is never deleted')
        self.assertFalse(is_retired('latest', '0.11.3'))

    def test_retired_command(self):
        import subprocess
        import sys
        from django.conf import settings
        from routerfleet.versioning import bump
        def run(tag):
            return subprocess.run([sys.executable, '-I', 'scripts/version.py', 'retired', tag]).returncode
        self.assertEqual(run('v0.0.1'), 0)
        self.assertNotEqual(run(f'v{settings.ROUTERFLEET_RELEASE}'), 0)
        self.assertNotEqual(run('v' + bump(settings.ROUTERFLEET_RELEASE, 'minor')), 0)

    def test_release_pattern(self):
        pattern = self.script().RELEASE_TAG
        for tag in ('latest', '0.9.0', '12.34.567'):
            self.assertTrue(pattern.match(tag), tag)
        for tag in ('0.7516', 'v0.9.0', 'sha-abc', '0.9', ''):
            self.assertFalse(pattern.match(tag), tag)

    def test_nothing_to_delete(self):
        versions = [{'id': 1, 'name': 'sha256:a', 'tags': ['1.0.0']}]
        self.assertEqual(self.script().plan(versions, lambda tag: [], '1.0.0')[1], [])


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


class WorkflowPinningTests(TestCase):
    """Every action CI runs is pinned to a commit. A tag can be moved to other
    code by whoever controls the action's repository; a commit cannot. The
    comment after the hash says which release it is."""

    def test_every_action_is_pinned_to_a_commit(self):
        import pathlib
        import re
        workflows = list(pathlib.Path(__file__).resolve().parent.parent.glob('.github/workflows/*.yml'))
        self.assertTrue(workflows)
        for workflow in workflows:
            for line in workflow.read_text().splitlines():
                match = re.search(r'uses:\s*(\S+)', line)
                if not match or match.group(1).startswith('./'):
                    continue
                with self.subTest(workflow=workflow.name, uses=match.group(1)):
                    self.assertRegex(match.group(1), r'^[\w.-]+/[\w./-]+@[0-9a-f]{40}$')
                    self.assertRegex(line, r'# v\d+\.\d+\.\d+$', 'say which release the hash is')


class ReleaseTitleTests(TestCase):
    def test_title_carries_the_label_of_the_changelog_heading(self):
        import importlib.util
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location('version_script', root / 'scripts' / 'version.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(pathlib.Path, 'read_text', return_value=(
                '# Changelog\n\n## [1.2.3] - 2026-01-01 - First public release\n\nx\n\n## [1.2.2] - 2025-12-31\n\ny\n')):
            self.assertEqual(module.release_title('1.2.3'), 'RouterFleet Improved 1.2.3 - First public release')
            self.assertEqual(module.release_title('1.2.2'), 'RouterFleet Improved 1.2.2')


class ZabbixKitTests(TestCase):
    """contrib/zabbix holds together: what the template asks the agent for is
    defined in the agent configuration, and what that runs is handled by the
    collector. (CI also runs the collector against a real database.)"""

    def setUp(self):
        import pathlib
        self.kit = pathlib.Path(__file__).resolve().parent.parent / 'contrib' / 'zabbix'

    def read(self, relative):
        return (self.kit / relative).read_text()

    def test_template_keys_are_defined_for_the_agent(self):
        import re
        template = self.read('templates/routerfleet-by-zabbix-agent.yaml')
        agent = self.read('agent/routerfleet.conf')
        asked = {re.sub(r'\[.*\]$', '', k) for k in re.findall(r'^\s*key:\s*\'?([a-z0-9_.]+(?:\[[^\]]*\])?)', template, re.M)}
        defined = {re.sub(r'\[\*\]$', '', k) for k in re.findall(r'^UserParameter=([a-z0-9_.]+(?:\[\*\])?),', agent, re.M)}
        self.assertTrue(asked)
        self.assertLessEqual(asked, defined, f'not defined for the agent: {asked - defined}')

    def test_every_agent_command_is_one_the_collector_handles(self):
        import re
        agent = self.read('agent/routerfleet.conf')
        collector = self.read('scripts/zbx-routerfleet.sh')
        commands = set(re.findall(r'zbx-routerfleet\.sh (\w+)', agent))
        handled = set(re.findall(r'^  (\w+)\)', collector, re.M))
        self.assertTrue(commands)
        self.assertLessEqual(commands, handled, f'not handled by the collector: {commands - handled}')

    def test_the_role_gets_columns_not_tables(self):
        sql = self.read('sql/zbx_monitor.sql')
        self.assertNotRegex(sql, r'(?m)^GRANT SELECT ON ')
        self.assertNotIn('ALL TABLES', sql.split('Do not do it')[1] if 'Do not do it' in sql else sql)
        self.assertIn("PASSWORD 'CHANGE_ME'", sql, 'the shipped file carries a placeholder, never a password')

    def test_every_counter_reports_minus_one_when_unreadable(self):
        import re
        collector = self.read('scripts/zbx-routerfleet.sh')
        self.assertNotRegex(collector, r'count\(\*\)[^"]*"\)\s*echo "\$\{val:-0\}"',
                            'a count must go through count_q, which reports -1 when it cannot read')
        self.assertIn('echo -1', collector)
        template = self.read('templates/routerfleet-by-zabbix-agent.yaml')
        for key in re.findall(r'(?m)^UserParameter=(routerfleet\.(?:routers|failing|hostkeys\.\w+|logins\.\w+|changes\.\w+|commands\.\w+)),',
                              self.read('agent/routerfleet.conf')):
            self.assertIn(f'last(/RouterFleet by Zabbix agent/{key})<0', template, key)

    def test_nothing_identifying(self):
        for path in self.kit.rglob('*'):
            if path.is_file():
                with self.subTest(file=path.name):
                    text = path.read_text()
                    self.assertNotRegex(text, r'\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+')
                    self.assertNotRegex(text, r'/Users/|/home/[a-z]')
