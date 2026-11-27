"""My Settings and the user manager beyond the everyday paths: email
notifications, a test email, the password page, the authenticator app once on,
passkey requests that do not check out, and access for lower levels."""
import json
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.test_mfa import HOST, RFC_SECRET
from routerfleet.testing import make_user
from user_manager.models import UserAcl, UserTotp

TEST_PASSWORD = 'Test-only-passphrase-2026!'   # test-only placeholder


def working_mail_server():
    from message_center.models import SmtpSettings
    smtp = SmtpSettings.load()
    smtp.enabled, smtp.host, smtp.sender_address, smtp.verified_at = (
        True, 'smtp.example.net', 'rf@example.net', timezone.now())
    smtp.save()


class EmailSettingsTests(TestCase):
    """The personal email address and the notifications it receives: saved
    while mail works, kept while it does not, refused when not an address."""

    def setUp(self):
        self.user = make_user(20)
        self.client.force_login(self.user)

    def channel(self):
        from message_center import email
        return email.personal_channel(self.user)

    def events(self):
        from message_center import email
        return [field for field, _, _ in email.PERSONAL_EVENTS]

    def test_saved_with_the_chosen_notifications_while_mail_works(self):
        working_mail_server()
        first = self.events()[0]
        response = self.client.post('/user/settings/', {'action': 'email', 'email': 'person@example.net', first: 'on'},
                                    follow=True)
        self.assertContains(response, '1 kind of notification to person@example.net')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'person@example.net')
        self.assertTrue(getattr(self.channel(), first))

    def test_the_choices_are_kept_while_mail_does_not_work(self):
        working_mail_server()
        events = self.events()
        self.client.post('/user/settings/', {'action': 'email', 'email': 'person@example.net',
                                             **dict.fromkeys(events, 'on')})
        from message_center.models import SmtpSettings
        SmtpSettings.objects.update(enabled=False)
        response = self.client.post('/user/settings/', {'action': 'email', 'email': 'other@example.net'}, follow=True)
        expected = f'{len(events)} kind{"s" if len(events) != 1 else ""} of notification to other@example.net'
        self.assertContains(response, expected)
        self.assertTrue(all(getattr(self.channel(), field) for field in events))

    def test_no_address_means_no_notifications(self):
        response = self.client.post('/user/settings/', {'action': 'email', 'email': ''}, follow=True)
        self.assertContains(response, 'You receive no email notifications.')

    def test_an_invalid_address_is_not_saved(self):
        response = self.client.post('/user/settings/', {'action': 'email', 'email': 'not an address'}, follow=True)
        self.assertContains(response, 'The email address is not valid.')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')

    def test_a_session_length_that_is_not_a_number_is_ignored(self):
        before = UserAcl.objects.get(user=self.user).session_hours
        self.assertRedirects(self.client.post('/user/settings/', {'action': 'session', 'session_hours': 'many'}),
                             '/user/settings/', fetch_redirect_response=False)
        self.assertEqual(UserAcl.objects.get(user=self.user).session_hours, before)


class TestEmailTests(TestCase):
    """A test email reports that it was sent, or why not - with the password of
    the mail server scrubbed from the reason."""

    def setUp(self):
        self.user = make_user(20)
        self.user.email = 'person@example.net'
        self.user.save()
        self.client.force_login(self.user)
        working_mail_server()

    def test_a_sent_test_email(self):
        with mock.patch('message_center.email.send') as send:
            response = self.client.post('/user/settings/', {'action': 'test_email'}, follow=True)
        send.assert_called_once()
        self.assertEqual(send.call_args[0][0], 'person@example.net')
        self.assertContains(response, 'Test email sent')

    def test_a_failed_test_email_shows_the_scrubbed_reason(self):
        from message_center.models import SmtpSettings
        SmtpSettings.objects.update(password='test-only-smtp-pw')
        with mock.patch('message_center.email.send', side_effect=OSError('refused test-only-smtp-pw | here')):
            response = self.client.post('/user/settings/', {'action': 'test_email'}, follow=True)
        self.assertContains(response, 'Test email failed')
        self.assertNotContains(response, 'test-only-smtp-pw')


class PasswordPageTests(TestCase):
    """The password page shows its form, and a new password that does not match
    itself is not saved."""

    def setUp(self):
        self.user = make_user(20, password=TEST_PASSWORD)
        self.client.force_login(self.user)

    def test_the_form_shows(self):
        response = self.client.get('/user/settings/password/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'user_manager/password_change.html')

    def test_mismatched_new_passwords_are_refused(self):
        response = self.client.post('/user/settings/password/', {
            'old_password': TEST_PASSWORD, 'new_password1': 'New-test-only-pass-1!', 'new_password2': 'Other-test-only-2!'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(TEST_PASSWORD))


class TotpPageOnceOnTests(TestCase):
    """With the app turned on, the page shows no new secret, and asking to turn
    it on again changes nothing."""

    def setUp(self):
        self.user = make_user(20)
        self.client.force_login(self.user)
        UserTotp.objects.create(user=self.user, secret=RFC_SECRET, confirmed=True)

    def test_no_secret_is_shown(self):
        response = self.client.get('/user/settings/totp/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('qr_svg', response.context)

    def test_enabling_again_changes_nothing(self):
        response = self.client.post('/user/settings/totp/', {'action': 'enable', 'code': '000000'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(UserTotp.objects.get(user=self.user).confirmed)


@override_settings(ALLOWED_HOSTS=[HOST, 'testserver', '192.0.2.5'])
class PasskeyRequestTests(TestCase):
    """Passkey requests that can not work: a body that is not JSON, an address
    instead of a name, no registration started, an answer that does not verify."""

    def setUp(self):
        self.user = make_user(30, password=TEST_PASSWORD)
        self.client.force_login(self.user)

    def post(self, url, body, host=HOST):
        return self.client.post(url, body, content_type='application/json', HTTP_HOST=host, HTTP_ORIGIN=f'https://{host}')

    def test_a_body_that_is_not_json_counts_as_no_password(self):
        response = self.post('/user/settings/passkeys/options/', 'not json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'Type your password.')

    def test_an_ip_address_is_refused(self):
        response = self.post('/user/settings/passkeys/options/', json.dumps({'password': TEST_PASSWORD}), host='192.0.2.5')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not by an IP address', response.json()['error'])

    def test_adding_without_a_started_registration_has_expired(self):
        response = self.post('/user/settings/passkeys/add/', json.dumps({'credential': {}}))
        self.assertEqual(response.status_code, 400)
        self.assertIn('expired', response.json()['error'])

    def test_an_answer_that_does_not_verify_is_refused(self):
        self.assertEqual(self.post('/user/settings/passkeys/options/',
                                   json.dumps({'password': TEST_PASSWORD})).status_code, 200)
        response = self.post('/user/settings/passkeys/add/', json.dumps({'credential': {'id': 'x'}}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'The passkey could not be verified.')


class UserManagerAccessTests(TestCase):
    """Below the administrator level the user list and the user form are
    refused; a new password ends every session of the account once."""

    def test_lower_levels_are_refused(self):
        self.client.force_login(make_user(40))
        for url in ('/user/list/', '/user/manage/'):
            response = self.client.get(url)
            self.assertTemplateUsed(response, 'access_denied.html')

    def test_a_new_password_ends_every_session_of_the_account(self):
        admin = make_user(50)
        target = make_user(20)
        other = make_user(20)
        for user in (target, target, other):
            Client().force_login(user)
        acl = UserAcl.objects.get(user=target)
        self.client.force_login(admin)
        response = self.client.post(f'/user/manage/?uuid={acl.uuid}', {
            'username': target.username, 'user_level': 20,
            'password1': 'New-test-only-pass-1!', 'password2': 'New-test-only-pass-1!'}, follow=True)
        self.assertContains(response, 'has been disconnected')
        decoded = [session.get_decoded().get('_auth_user_id') for session in Session.objects.all()]
        self.assertNotIn(str(target.pk), decoded)
        self.assertIn(str(other.pk), decoded)
        self.assertIn(str(admin.pk), decoded)
        self.assertTrue(User.objects.get(pk=target.pk).check_password('New-test-only-pass-1!'))
