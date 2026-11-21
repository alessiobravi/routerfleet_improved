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
        response = mock.Mock(json=lambda: {'update_available': True, 'current_version': '0.8'})
        with mock.patch('routerfleet_tools.views.requests.get', return_value=response) as get:
            data = self.client.get('/cron/check_updates/').json()
        url = get.call_args.args[0]
        self.assertTrue(url.startswith('https://updates.example.org/check?app=routerfleet&version='))
        self.assertEqual(get.call_args.kwargs['timeout'], 10)
        self.assertTrue(data['update_available'])
        self.assertEqual(WebadminSettings.objects.get().latest_version, 8000)

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


class VersionSpellingTests(TestCase):
    def test_sidebar_and_release_tag_agree(self):
        import importlib.util, pathlib
        from routerfleet_tools.templatetags.custom_tags import version_string
        spec = importlib.util.spec_from_file_location(
            'release_version', pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'version.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        for number in (7515, 7600, 10002, 0):
            self.assertEqual(version_string(number), script.version_string(number))
        self.assertEqual(version_string(7600), '0.7600')

    def test_current_version_is_shown(self):
        from django.conf import settings
        from routerfleet_tools.templatetags.custom_tags import version_string
        self.client.force_login(make_user(10))
        self.assertContains(self.client.get('/'), version_string(settings.ROUTERFLEET_VERSION))
