"""Tests of the release scripts (scripts/*.py) and the small helpers of
routerfleet_tools: release notes, database maintenance, page context."""
import contextlib
import importlib.util
import io
import json
import pathlib
import runpy
import sys
import tempfile
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_script(name):
    spec = importlib.util.spec_from_file_location(f'{name}_under_test', ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_as_main(name, argv):
    """Runs a script as `python scripts/<name>.py ...`; returns (exit code, stdout)."""
    out = io.StringIO()
    code = 0
    with mock.patch.object(sys, 'argv', [str(ROOT / 'scripts' / f'{name}.py')] + argv), \
            contextlib.redirect_stdout(out):
        try:
            runpy.run_path(str(ROOT / 'scripts' / f'{name}.py'), run_name='__main__')
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue()


class VersionScriptTests(TestCase):
    """scripts/version.py reads, checks and raises the release - here on copies
    of settings.py and CHANGELOG.md in a temporary directory."""

    CHANGELOG = ('# Changelog\n\n'
                 '## [1.2.3] - 2026-01-02 - Big one\n\n### Added\n- New thing.\n\n'
                 '## [1.2.2] - 2026-01-01\n\n- Old thing.\n')

    def setUp(self):
        self.module = load_script('version')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'routerfleet').mkdir()
        self.settings_file = self.root / 'routerfleet' / 'settings.py'
        self.write_settings('1.2.3')
        (self.root / 'CHANGELOG.md').write_text(self.CHANGELOG, encoding='utf-8')
        self.module.ROOT = self.root
        self.module.SETTINGS = self.settings_file

    def write_settings(self, release):
        self.settings_file.write_text(f"DEBUG = False\nROUTERFLEET_RELEASE = '{release}'\nOTHER = 1\n",
                                      encoding='utf-8')

    def run_main(self, *args):
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out):
            try:
                self.module.main(['version.py', *args])
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue()

    def test_the_release_is_read_from_settings(self):
        self.assertEqual(self.module.release(), '1.2.3')
        self.assertEqual(self.run_main(), (0, '1.2.3\n'))
        self.assertEqual(self.run_main('print'), (0, '1.2.3\n'))

    def test_settings_without_a_release_stop_it(self):
        self.settings_file.write_text('DEBUG = False\n', encoding='utf-8')
        with self.assertRaises(SystemExit) as stop:
            self.module.release()
        self.assertIn('ROUTERFLEET_RELEASE not found', str(stop.exception.code))

    def test_a_malformed_release_is_refused(self):
        self.write_settings('1.2')
        with self.assertRaises(ValueError):
            self.module.release()

    def test_the_changelog_section(self):
        self.assertEqual(self.module.changelog_section('1.2.3'), '### Added\n- New thing.')
        self.assertEqual(self.module.changelog_section('1.2.2'), '- Old thing.')
        self.assertIsNone(self.module.changelog_section('9.9.9'))

    def test_check_and_notes(self):
        self.assertEqual(self.run_main('check'), (0, '1.2.3: changelog section present\n'))
        self.assertEqual(self.run_main('notes'), (0, '### Added\n- New thing.\n'))
        self.write_settings('1.2.4')
        code, _ = self.run_main('check')
        self.assertIn('has no section "## [1.2.4]"', code)
        code, _ = self.run_main('notes')
        self.assertIn('no section for 1.2.4', code)

    def test_newer_than(self):
        self.assertEqual(self.run_main('newer-than', 'refs/tags/v1.2.2', 'latest', 'v0.9.0'),
                         (0, '1.2.3: above every release\n'))
        self.assertEqual(self.run_main('newer-than'), (0, '1.2.3: above every release\n'),
                         'no release yet')
        self.assertEqual(self.run_main('newer-than', 'sha-abc'), (0, '1.2.3: above every release\n'))
        code, _ = self.run_main('newer-than', 'v1.2.3')
        self.assertIn('not above the latest release 1.2.3', code)
        code, _ = self.run_main('newer-than', 'v1.2.2', 'v2.0.0')
        self.assertIn('latest release 2.0.0', code)

    def test_title(self):
        self.assertEqual(self.run_main('title'), (0, 'RouterFleet Improved 1.2.3 - Big one\n'))
        self.write_settings('1.2.2')
        self.assertEqual(self.run_main('title'), (0, 'RouterFleet Improved 1.2.2\n'))

    def test_retired(self):
        self.assertEqual(self.run_main('retired', 'refs/tags/v1.2.2')[0], 0)
        self.assertEqual(self.run_main('retired', 'v1.2.3')[0], 1)
        self.assertEqual(self.run_main('retired', 'v1.3.0')[0], 1)
        self.assertEqual(self.run_main('retired')[0], 1, 'no tag is not a retired release')

    def test_bump_rewrites_only_the_release_line(self):
        code, out = self.run_main('bump', 'minor')
        self.assertEqual(code, 0)
        self.assertIn('1.2.3 -> 1.3.0', out)
        self.assertEqual(self.settings_file.read_text(encoding='utf-8'),
                         "DEBUG = False\nROUTERFLEET_RELEASE = '1.3.0'\nOTHER = 1\n")
        self.run_main('bump', 'patch')
        self.assertEqual(self.module.release(), '1.3.1')

    def test_bump_without_a_part_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_main('bump')
        self.assertEqual(self.module.release(), '1.2.3', 'nothing written')

    def test_an_unknown_command_prints_the_usage(self):
        code, _ = self.run_main('no-such-command')
        self.assertIn('scripts/version.py bump patch|minor', code)

    def test_run_as_a_program(self):
        # Reads the real settings.py, writes nothing
        from django.conf import settings
        self.assertEqual(run_as_main('version', []), (0, f'{settings.ROUTERFLEET_RELEASE}\n'))


class CveReportMainTests(TestCase):
    """scripts/cve_report.py reads the Trivy reports named on its command line."""

    def test_reports_are_read_from_files(self):
        module = load_script('cve_report')
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index, report in enumerate([
                    {'ArtifactName': 'example/app:1.0.0', 'Results': None},
                    {'ArtifactName': 'example/db:1.0.0', 'Results': [{'Vulnerabilities': [
                        {'VulnerabilityID': 'ID-1', 'Severity': 'MEDIUM', 'PkgName': 'libx',
                         'FixedVersion': '2'}]}]}]):
                path = pathlib.Path(tmp) / f'report{index}.json'
                path.write_text(json.dumps(report), encoding='utf-8')
                paths.append(str(path))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                module.main(['cve_report.py', '0.50.0', *paths])
        text = out.getvalue()
        self.assertIn('scanned with Trivy 0.50.0', text)
        self.assertIn('**example/app:1.0.0** — none', text)
        self.assertIn('1 medium (all with a fix)', text)
        self.assertIn('ID-1 medium — libx (fixed in 2)', text)
        self.assertNotIn('<details>', text, 'a short list is not folded')

    def test_a_missing_name_and_no_results_key(self):
        text = load_script('cve_report').section('0.50.0', [{}])
        self.assertIn('- **?** — none', text)

    def test_too_few_arguments_print_the_usage(self):
        code, out = run_as_main('cve_report', ['0.50.0'])
        self.assertIn('TRIVY_VERSION report.json', code)
        self.assertEqual(out, '')


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ImagePruneRequestTests(TestCase):
    """scripts/prune_images.py talks only to GitHub's API and registry, over
    https - here with urllib replaced, nothing leaves the machine."""

    def setUp(self):
        self.module = load_script('prune_images')

    def test_other_hosts_and_schemes_are_refused(self):
        with mock.patch('urllib.request.urlopen') as urlopen:
            for url in ('http://api.github.com/x', 'https://example.com/x', 'file:///etc/hosts',
                        'https://api.github.com.example.net/x'):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    self.module._request(url)
        urlopen.assert_not_called()

    def test_headers_and_answers(self):
        with mock.patch('urllib.request.urlopen', return_value=FakeResponse(b'{"a": 1}')) as urlopen:
            self.assertEqual(self.module._request('https://api.github.com/x', 'test-only-token',
                                                  method='DELETE', accept='application/json'), {'a': 1})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), 'DELETE')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-only-token')
        self.assertEqual(request.get_header('Accept'), 'application/json')
        self.assertEqual(request.get_header('User-agent'), 'routerfleet-image-prune')
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 30)
        with mock.patch('urllib.request.urlopen', return_value=FakeResponse(b'')) as urlopen:
            self.assertIsNone(self.module._request('https://ghcr.io/y'))
        request = urlopen.call_args.args[0]
        self.assertIsNone(request.get_header('Authorization'), 'no token, no header')
        self.assertIsNone(request.get_header('Accept'))

    def test_versions_are_read_page_by_page(self):
        pages = [[{'id': 1, 'name': 'sha256:a', 'metadata': {'container': {'tags': ['1.0.0']}}}],
                 [{'id': 2, 'name': 'sha256:b', 'metadata': {'container': {'tags': []}}}], []]
        with mock.patch.object(self.module, '_request', side_effect=pages) as request:
            versions = self.module.list_versions('example', 'app', 'test-only-token')
        self.assertEqual(versions, [{'id': 1, 'name': 'sha256:a', 'tags': ['1.0.0']},
                                    {'id': 2, 'name': 'sha256:b', 'tags': []}])
        self.assertIn('page=3', request.call_args.args[0])

    def test_children_of_an_index_and_of_a_single_manifest(self):
        answers = {
            'https://ghcr.io/token?scope=repository:example/app:pull': {'token': 'anonymous'},
            'https://ghcr.io/v2/example/app/manifests/multi': {
                'mediaType': self.module.INDEX_TYPES[0], 'manifests': [{'digest': 'sha256:c1'}]},
            'https://ghcr.io/v2/example/app/manifests/untyped': {'manifests': [{'digest': 'sha256:c2'}]},
            'https://ghcr.io/v2/example/app/manifests/single': {
                'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'layers': []},
        }
        with mock.patch.object(self.module, '_request', side_effect=lambda url, *a, **k: answers[url]):
            children_of = self.module.registry_children('example', 'app')
            self.assertEqual(children_of('multi'), ['sha256:c1'])
            self.assertEqual(children_of('untyped'), ['sha256:c2'])
            self.assertEqual(children_of('single'), [])

    def run_main(self, argv, env, versions=(), children=None):
        out = io.StringIO()
        code = 0
        with mock.patch.dict('os.environ', env, clear=True), \
                mock.patch.object(self.module, 'list_versions', return_value=list(versions)), \
                mock.patch.object(self.module, 'registry_children', return_value=children or (lambda tag: [])), \
                mock.patch.object(self.module, '_request') as request, contextlib.redirect_stdout(out):
            try:
                self.module.main(['prune_images.py', *argv])
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue(), request

    VERSIONS = [{'id': 1, 'name': 'sha256:new', 'tags': ['1.1.0', 'latest']},
                {'id': 2, 'name': 'sha256:old', 'tags': ['1.0.0']},
                {'id': 3, 'name': 'sha256:loose', 'tags': []}]

    def test_wrong_arguments_and_no_token(self):
        self.assertIn('IMAGE RELEASE', self.run_main(['app'], {'GH_TOKEN': 'test-only'})[0])
        self.assertEqual(self.run_main(['app', '1.1.0'], {})[0], 'GH_TOKEN is not set')

    def test_an_unknown_release_deletes_nothing(self):
        code, _, request = self.run_main(['app', '9.9.9'], {'GH_TOKEN': 'test-only'}, self.VERSIONS)
        self.assertIn('not among the versions - nothing deleted', code)
        request.assert_not_called()

    def test_dry_run_deletes_nothing(self):
        code, out, request = self.run_main(['app', '1.1.0', '--dry-run'], {'GH_TOKEN': 'test-only'},
                                           self.VERSIONS)
        self.assertEqual(code, 0)
        request.assert_not_called()
        self.assertIn('keeping 1 tagged (1.1.0, latest)', out)
        self.assertIn('would delete 2 sha256:old (1.0.0)', out)
        self.assertIn('would delete 3 sha256:loose (untagged)', out)
        self.assertIn('app: 2 would be deleted', out)

    def test_the_rest_is_deleted(self):
        code, out, request = self.run_main(
            ['app', '1.1.0'], {'GH_TOKEN': 'test-only', 'GITHUB_REPOSITORY_OWNER': 'example'}, self.VERSIONS)
        self.assertEqual(code, 0)
        urls = [c.args[0] for c in request.call_args_list]
        self.assertEqual(urls, ['https://api.github.com/users/example/packages/container/app/versions/2',
                                'https://api.github.com/users/example/packages/container/app/versions/3'])
        self.assertTrue(all(c.kwargs['method'] == 'DELETE' for c in request.call_args_list))
        self.assertIn('deleted 2 sha256:old (1.0.0)', out)
        self.assertIn('app: 2 deleted', out)

    def test_run_as_a_program_without_arguments(self):
        code, out = run_as_main('prune_images', [])
        self.assertIn('IMAGE RELEASE [--dry-run]', code)


class ReleaseNotesTests(TestCase):
    """The "What's new" notes: the local changelog, the published one (fetched
    once an hour at most, never while the update check is off) and the
    rendering from escaped text."""

    PUBLISHED = '## [99.0.0] - 2099-01-01\n\n- Ahead.\n'

    def setUp(self):
        from django.core.cache import caches
        caches['limits'].clear()
        self.addCleanup(caches['limits'].clear)

    def test_a_missing_local_changelog_is_empty(self):
        from routerfleet_tools.release_notes import local_changelog
        with tempfile.TemporaryDirectory() as tmp, override_settings(BASE_DIR=pathlib.Path(tmp) / 'gone'):
            self.assertEqual(local_changelog(), '')
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / 'CHANGELOG.md').write_text('hello', encoding='utf-8')
            with override_settings(BASE_DIR=tmp):
                self.assertEqual(local_changelog(), 'hello')

    def test_no_address_no_request(self):
        from routerfleet_tools.release_notes import published_changelog
        with override_settings(UPDATE_NOTES_URL=''), mock.patch('routerlib.outbound.https_get') as get:
            self.assertEqual(published_changelog(), '')
        get.assert_not_called()

    def test_fetched_once_then_cached(self):
        from routerfleet_tools.release_notes import published_changelog
        with mock.patch('routerlib.outbound.https_get') as get:
            get.return_value = mock.Mock(text=self.PUBLISHED, raise_for_status=lambda: None)
            self.assertEqual(published_changelog(), self.PUBLISHED)
            self.assertEqual(published_changelog(), self.PUBLISHED)
        get.assert_called_once()

    def test_a_failure_is_cached_too(self):
        from routerfleet_tools.release_notes import published_changelog
        with mock.patch('routerlib.outbound.https_get', side_effect=OSError('down')) as get, \
                self.assertLogs('routerfleet_tools.release_notes', 'INFO') as logs:
            self.assertEqual(published_changelog(), '')
            self.assertEqual(published_changelog(), '')
        get.assert_called_once()
        self.assertIn('Release notes could not be read: OSError', logs.output[0])

    def test_render_paragraphs_lists_and_links(self):
        from routerfleet_tools.release_notes import render
        html = str(render('First line\nsecond line.\n\n- item one\n  continued\n'
                          'A paragraph after the list with a [link](https://example.com/x) and *italics*.\n'
                          '### Heading <b>\n- last'))
        self.assertEqual(html, '<p>First line second line.</p>\n'
                               '<ul><li>item one continued</li></ul>\n'
                               '<p>A paragraph after the list with a link and <em>italics</em>.</p>\n'
                               '<h6 class="rf-notes-h">Heading &lt;b&gt;</h6>\n'
                               '<ul><li>last</li></ul>')
        self.assertEqual(str(render(None)), '')
        self.assertEqual(str(render('')), '')

    def test_sections_of_nothing(self):
        from routerfleet_tools.release_notes import sections
        self.assertEqual(sections(''), [])
        self.assertEqual(sections(None), [])
        found = sections('## [1.0.0]\nbody')
        self.assertEqual((found[0]['date'], found[0]['label'], found[0]['body']), ('', '', 'body'))

    def test_notes_without_the_running_release_in_the_changelog(self):
        from routerfleet_tools import release_notes
        with mock.patch.object(release_notes, 'published_changelog', return_value=self.PUBLISHED), \
                mock.patch.object(release_notes, 'local_changelog', return_value='## [0.0.1] - 2020-01-01\n- x\n'):
            result = release_notes.notes()
        self.assertIsNone(result['running'])
        self.assertTrue(result['fetched'])
        self.assertEqual([s['version'] for s in result['newer']], ['99.0.0'])
        self.assertIn('<li>Ahead.</li>', str(result['newer'][0]['html']))

    def test_notes_with_the_running_release(self):
        from django.conf import settings
        from routerfleet_tools import release_notes
        local = f'## [{settings.ROUTERFLEET_RELEASE}] - 2026-01-01\n\n- Running.\n'
        with mock.patch.object(release_notes, 'published_changelog', return_value=''), \
                mock.patch.object(release_notes, 'local_changelog', return_value=local):
            result = release_notes.notes()
        self.assertFalse(result['fetched'])
        self.assertEqual(result['newer'], [])
        self.assertIn('<li>Running.</li>', str(result['running']['html']))


class DatabaseMaintenanceTests(TestCase):
    """The database size and VACUUM per database engine, with the connection
    replaced so both engines are exercised whichever the tests run on."""

    def fake_connection(self, vendor, name=None, fetched=(12345,)):
        connection = mock.MagicMock(vendor=vendor, in_atomic_block=False, settings_dict={'NAME': name})
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = fetched
        return connection, cursor

    def test_postgresql_size(self):
        from routerfleet_tools import database
        connection, cursor = self.fake_connection('postgresql')
        with mock.patch.object(database, 'connection', connection):
            self.assertEqual(database.size(), 12345)
        cursor.execute.assert_called_once_with('SELECT pg_database_size(current_database())')

    def test_sqlite_size(self):
        from routerfleet_tools import database
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(b'x' * 100)
            handle.flush()
            with mock.patch.object(database, 'connection', self.fake_connection('sqlite', handle.name)[0]):
                self.assertEqual(database.size(), 100)
        for name in (None, '', '/nonexistent/example.sqlite3'):
            with mock.patch.object(database, 'connection', self.fake_connection('sqlite', name)[0]):
                self.assertIsNone(database.size(), name)

    def test_other_engines_and_errors_have_no_size(self):
        from routerfleet_tools import database
        with mock.patch.object(database, 'connection', self.fake_connection('mysql')[0]):
            self.assertIsNone(database.size())
        connection, cursor = self.fake_connection('postgresql')
        cursor.execute.side_effect = RuntimeError('no permission')
        with mock.patch.object(database, 'connection', connection), \
                self.assertLogs('routerfleet_tools.database', 'WARNING') as logs:
            self.assertIsNone(database.size())
        self.assertIn('Database size could not be read: no permission', logs.output[0])

    def test_vacuum_per_engine(self):
        from routerfleet_tools import database
        for vendor, full, expected in (('postgresql', False, ['VACUUM (ANALYZE)']),
                                       ('postgresql', True, ['VACUUM (FULL, ANALYZE)']),
                                       ('sqlite', False, ['VACUUM', 'ANALYZE'])):
            connection, cursor = self.fake_connection(vendor)
            with self.subTest(vendor=vendor, full=full), mock.patch.object(database, 'connection', connection), \
                    mock.patch.object(database, 'size', side_effect=[200, 100]):
                self.assertEqual(database.optimize(full=full), (200, 100))
                self.assertEqual([c.args[0] for c in cursor.execute.call_args_list], expected)

    def test_outcome_with_one_size_unknown(self):
        from routerfleet_tools.database import outcome
        self.assertEqual(outcome(None, 1048576, False, 'sqlite'), 'From an unknown size to 1.0 MB.')
        self.assertIn('unknown size', outcome(None, None, False, 'postgresql'))


class DisplayContextTests(TestCase):
    """Every page's values: the page size for everyone, the administrator flag
    for the menu - and sensible values before the migrations ran."""

    def request(self, user):
        request = RequestFactory().get('/')
        request.user = user
        return request

    def test_anonymous_and_userless_requests(self):
        from django.contrib.auth.models import AnonymousUser
        from routerfleet_tools.context import display
        self.assertEqual(display(self.request(AnonymousUser())), {'rf_page_size': 10})
        self.assertEqual(display(RequestFactory().get('/')), {'rf_page_size': 10})

    def test_the_chosen_page_size_and_the_administrator(self):
        from routerfleet.testing import make_user
        from routerfleet_tools.context import display
        from routerfleet_tools.models import DisplaySettings
        DisplaySettings.objects.create(default_page_size=50)
        self.assertEqual(display(self.request(make_user(50))), {'rf_page_size': 50, 'rf_is_admin': True})
        self.assertEqual(display(self.request(make_user(10, username='viewer'))),
                         {'rf_page_size': 50, 'rf_is_admin': False})

    def test_before_the_migrations_ran(self):
        from routerfleet.testing import make_user
        from routerfleet_tools.context import display
        from routerfleet_tools.models import DisplaySettings
        from user_manager.models import UserAcl
        user = make_user(50)
        with mock.patch.object(DisplaySettings.objects, 'values_list', side_effect=RuntimeError('no table')), \
                mock.patch.object(UserAcl.objects, 'filter', side_effect=RuntimeError('no table')):
            self.assertEqual(display(self.request(user)), {'rf_page_size': 10, 'rf_is_admin': False})

    def test_an_unknown_page_size_falls_back(self):
        from routerfleet.testing import make_user
        from routerfleet_tools.context import display
        from routerfleet_tools.models import DisplaySettings
        DisplaySettings.objects.create(default_page_size=7)
        self.assertEqual(display(self.request(make_user(10)))['rf_page_size'], 10)
