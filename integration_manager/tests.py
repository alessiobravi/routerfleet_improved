from unittest import mock

from django.test import TestCase

from integration_manager.models import ExternalIntegration
from routerfleet.testing import make_user


class WireGuardIntegrationTests(TestCase):
    def setUp(self):
        self.user = make_user(50, username='admin-1')
        self.client.force_login(self.user)

    def integration(self):
        return ExternalIntegration.objects.create(name='wireguard_webadmin', integration_type='wireguard_webadmin',
                                                  integration_url='https://wg.example', token='T',
                                                  wireguard_webadmin_default_user_level=20)

    def test_launcher_without_integration(self):
        response = self.client.get('/wireguard_webadmin/')
        self.assertIsNone(response.context['wireguard_integration'])
        self.assertEqual(self.client.get('/wireguard_webadmin/launch/').status_code, 404)

    def test_launch_hands_the_user_over_with_a_token(self):
        self.integration()
        api = mock.Mock(status_code=200, json=lambda: {'authentication_token': 'abc'})
        with mock.patch('integration_manager.views.requests.get', return_value=api) as get:
            response = self.client.get('/wireguard_webadmin/launch/')
        self.assertIn('username=admin-1', get.call_args.args[0])
        self.assertIn('default_user_level=20', get.call_args.args[0])
        self.assertEqual(response['Location'],
                         'https://wg.example/accounts/routerfleet_authenticate_session/?token=abc')

    def test_launch_failures_return_to_the_launcher(self):
        self.integration()
        for kwargs in ({'side_effect': OSError('down')}, {'return_value': mock.Mock(status_code=403)}):
            with self.subTest(kwargs=kwargs), mock.patch('integration_manager.views.requests.get', **kwargs):
                self.assertRedirects(self.client.get('/wireguard_webadmin/launch/'), '/wireguard_webadmin/',
                                     fetch_redirect_response=False)

    def test_delete_needs_confirmation(self):
        self.integration()
        self.client.get('/wireguard_webadmin/manage/?action=delete&confirmation=no')
        self.assertTrue(ExternalIntegration.objects.exists())
        self.client.get('/wireguard_webadmin/manage/?action=delete&confirmation=delete')
        self.assertFalse(ExternalIntegration.objects.exists())

    def test_manage_page(self):
        self.assertEqual(self.client.get('/wireguard_webadmin/manage/').status_code, 200)


class WireGuardFormTests(TestCase):
    def setUp(self):
        self.user = make_user(50, username='admin-2')

    def form(self, **data):
        from integration_manager.forms import WireGuardWebAdminForm
        values = {'integration_url': 'https://wg.example/', 'token': 'T',
                  'wireguard_webadmin_default_user_level': 0}
        values.update(data)
        return WireGuardWebAdminForm(values, user=self.user)

    def api(self, status=200, body=None):
        return mock.patch('integration_manager.forms.requests.get',
                          return_value=mock.Mock(status_code=status, json=lambda: body or {}))

    def test_valid_strips_the_trailing_slash_and_tests_the_token(self):
        with self.api() as get:
            form = self.form()
            self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['integration_url'], 'https://wg.example')
        self.assertIn('action=test', get.call_args.args[0])

    def test_https_is_required(self):
        with self.api():
            self.assertIn('https://', str(self.form(integration_url='http://wg.example').errors))

    def test_empty_url_is_a_form_error_not_a_crash(self):
        form = self.form(integration_url='')
        self.assertFalse(form.is_valid())
        self.assertIn('integration_url', form.errors)

    def test_api_answers(self):
        cases = [(403, None, 'Invalid token'), (400, {'message': 'User unknown'}, 'User unknown'),
                 (400, {}, 'Status Code: 400'), (500, {}, 'Status Code: 500')]
        for status, body, message in cases:
            with self.subTest(status=status), self.api(status, body):
                self.assertIn(message, str(self.form().errors))

    def test_unreachable_and_unparseable(self):
        with mock.patch('integration_manager.forms.requests.get', side_effect=OSError):
            self.assertIn('Error connecting to API', str(self.form().errors))
        bad_json = mock.Mock(status_code=200, json=mock.Mock(side_effect=ValueError))
        with mock.patch('integration_manager.forms.requests.get', return_value=bad_json):
            self.assertIn('Error parsing API response', str(self.form().errors))

    def test_blank_token_keeps_the_stored_one(self):
        from integration_manager.forms import WireGuardWebAdminForm
        stored = ExternalIntegration.objects.create(name='wireguard_webadmin', integration_type='wireguard_webadmin',
                                                    integration_url='https://wg.example', token='KEEP')
        with self.api():
            form = WireGuardWebAdminForm({'integration_url': 'https://wg.example', 'token': '',
                                          'wireguard_webadmin_default_user_level': 0},
                                         instance=stored, user=self.user)
            self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['token'], 'KEEP')
