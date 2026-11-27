"""The less travelled paths of signing in: the first-user form, the second
step, passkey refusals, signing out, and the password reset email."""
import json
import threading
import time
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import caches
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts import password_reset
from accounts.test_mfa import HOST, ORIGIN, SoftAuthenticator
from routerfleet.testing import make_user
from user_manager.models import UserAcl, UserPasskey

TEST_PASSWORD = 'Test-only-passphrase-2026!'   # test-only placeholder


class FirstUserFormTests(TestCase):
    """The first-user form shows on an empty installation, and a second
    administrator created at the same moment is refused."""

    def post(self):
        return self.client.post('/accounts/create_first_user/', {
            'username': 'first-admin', 'password': TEST_PASSWORD, 'password2': TEST_PASSWORD})

    def test_the_form_shows_while_there_is_no_user(self):
        response = self.client.get('/accounts/create_first_user/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="username"')

    def test_a_user_appearing_before_the_lock_is_refused(self):
        with mock.patch.object(User.objects, 'exists', return_value=True):
            self.assertEqual(self.post().status_code, 404)
        self.assertFalse(User.objects.exists())

    def test_a_user_appearing_meanwhile_is_refused(self):
        with mock.patch.object(User.objects, 'count', return_value=2):
            self.assertEqual(self.post().status_code, 404)
        self.assertFalse(User.objects.exists())
        self.assertFalse(UserAcl.objects.exists())


class SignInPageTests(TestCase):
    """The sign-in page does not offer passkeys when the HTTPS settings can not
    be read, and the code step shows its form; signing out needs a POST."""

    def test_unreadable_https_settings_offer_no_passkeys(self):
        make_user(50)
        with mock.patch('routerfleet_tools.models.HttpsSettings.load', side_effect=RuntimeError('test')):
            self.assertNotContains(self.client.get('/accounts/login/', HTTP_HOST='localhost'), 'passkey-area')

    def test_the_code_step_shows_its_form(self):
        user = make_user(50)
        session = self.client.session
        session['mfa_pending'] = {'user_id': user.pk, 'at': time.time()}
        session.save()
        response = self.client.get('/accounts/login/verify/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/login_verify.html')

    def test_signing_out_by_a_link_does_nothing(self):
        user = make_user(50)
        self.client.force_login(user)
        self.assertRedirects(self.client.get('/accounts/logout/'), '/', fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)


@override_settings(ALLOWED_HOSTS=[HOST, 'testserver'])
class PasskeyRefusalTests(TestCase):
    """A passkey sign-in is refused while the address is locked out, and for an
    account that was switched off."""

    def setUp(self):
        self.user = make_user(30, username='passkey-user', password=TEST_PASSWORD)
        self.authenticator = SoftAuthenticator()

    def post(self, url, body=None):
        return self.client.post(url, json.dumps(body or {}), content_type='application/json',
                                HTTP_HOST=HOST, HTTP_ORIGIN=ORIGIN)

    def register(self):
        self.client.force_login(self.user)
        options = self.post('/user/settings/passkeys/options/', {'password': TEST_PASSWORD}).json()
        self.post('/user/settings/passkeys/add/', {'name': 'Laptop', 'credential': self.authenticator.create(options)})
        self.client.logout()
        self.assertTrue(UserPasskey.objects.filter(user=self.user).exists())

    def test_no_challenge_for_a_locked_out_address(self):
        with mock.patch('accounts.lockout.address_locked_out', return_value=True):
            response = self.post('/accounts/passkey/options/')
        self.assertEqual(response.status_code, 429)

    def test_an_answer_from_a_locked_out_address_is_refused(self):
        self.register()
        options = self.post('/accounts/passkey/options/').json()
        answer = self.authenticator.get(options, str(self.user.pk).encode())
        with mock.patch('accounts.lockout.address_locked_out', return_value=True):
            response = self.post('/accounts/passkey/login/', answer)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_a_switched_off_account_is_refused(self):
        self.register()
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        options = self.post('/accounts/passkey/options/').json()
        response = self.post('/accounts/passkey/login/', self.authenticator.get(options, str(self.user.pk).encode()))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('_auth_user_id', self.client.session)


class PasswordResetCornerTests(TestCase):
    """The reset form itself, no reset before the migrations ran, a mail server
    that refuses, and sending in the background."""

    def setUp(self):
        caches['limits'].clear()

    def working_mail_server(self):
        from message_center.models import SmtpSettings
        smtp = SmtpSettings.load()
        smtp.enabled, smtp.host, smtp.sender_address, smtp.verified_at = (
            True, 'smtp.example.net', 'rf@example.net', timezone.now())
        smtp.save()

    def test_the_form_shows_while_mail_works(self):
        self.working_mail_server()
        response = self.client.get('/accounts/password_reset/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/password_reset.html')

    def test_not_available_when_the_settings_can_not_be_read(self):
        with mock.patch('message_center.models.SmtpSettings.load', side_effect=RuntimeError('test')):
            self.assertFalse(password_reset.available())

    def test_a_refused_email_is_logged_and_the_rest_still_sent(self):
        with mock.patch('message_center.email.send', side_effect=[OSError('test'), None]) as send, \
                self.assertLogs('accounts.password_reset', 'WARNING'):
            password_reset._send_in_background([('a@example.net', 's', 't'), ('b@example.net', 's', 't')])
        self.assertEqual(send.call_count, 2)

    @override_settings(PASSWORD_RESET_BACKGROUND=True)
    def test_sending_in_the_background_closes_its_connection(self):
        started = []
        real_thread = threading.Thread

        class RecordedThread(real_thread):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                started.append(self)

        with mock.patch('threading.Thread', RecordedThread), \
                mock.patch('message_center.email.send') as send, \
                mock.patch('django.db.connection') as connection:
            password_reset._send_in_background([('a@example.net', 's', 't')])
            for thread in started:
                thread.join(5)
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].name, 'password-reset-mail')
        send.assert_called_once_with('a@example.net', 's', 't')
        connection.close.assert_called_once_with()
