"""Second factors: authenticator-app codes and passkeys.

The passkey tests use a software authenticator - a real P-256 key, a real
attestation object and real signed assertions - so they go through the actual
WebAuthn verification, not a mock of it.
"""
import base64
import hashlib
import json
import struct
from unittest import mock

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts import mfa
from audit_log.models import LoginRecord
from routerfleet.testing import make_user
from user_manager.models import UserAcl, UserPasskey, UserTotp

HOST = 'fleet.example.org'
ORIGIN = f'https://{HOST}'
# RFC 6238, appendix B: the SHA-1 key "12345678901234567890"
RFC_SECRET = base64.b32encode(b'12345678901234567890').decode()


def b64url(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


class SoftAuthenticator:
    """Just enough of a FIDO2 authenticator to register and sign in."""

    def __init__(self, rp_id=HOST):
        self.rp_id = rp_id
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = b'soft-' + hashlib.sha256(str(id(self)).encode()).digest()[:16]
        self.count = 0

    def _cose(self):
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, 'big'), -3: numbers.y.to_bytes(32, 'big')})

    def _client_data(self, kind, challenge, origin):
        return json.dumps({'type': kind, 'challenge': challenge, 'origin': origin, 'crossOrigin': False}).encode()

    def create(self, options, origin=ORIGIN):
        client_data = self._client_data('webauthn.create', options['challenge'], origin)
        attested = bytes(16) + struct.pack('>H', len(self.credential_id)) + self.credential_id + self._cose()
        auth_data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x45]) + struct.pack('>I', self.count) + attested
        attestation = cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth_data})
        return {'id': b64url(self.credential_id), 'rawId': b64url(self.credential_id), 'type': 'public-key',
                'response': {'clientDataJSON': b64url(client_data), 'attestationObject': b64url(attestation),
                             'transports': ['internal']},
                'clientExtensionResults': {}}

    def get(self, options, user_handle, origin=ORIGIN):
        self.count += 1
        client_data = self._client_data('webauthn.get', options['challenge'], origin)
        auth_data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x05]) + struct.pack('>I', self.count)
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id': b64url(self.credential_id), 'rawId': b64url(self.credential_id), 'type': 'public-key',
                'response': {'clientDataJSON': b64url(client_data), 'authenticatorData': b64url(auth_data),
                             'signature': b64url(signature), 'userHandle': b64url(user_handle)},
                'clientExtensionResults': {}}


class TotpAlgorithmTests(TestCase):
    def test_rfc6238_vectors(self):
        # Appendix B, SHA-1, 8 digits
        for seconds, expected in ((59, '94287082'), (1111111109, '07081804'),
                                  (1111111111, '14050471'), (1234567890, '89005924'),
                                  (2000000000, '69279037'), (20000000000, '65353130')):
            with self.subTest(seconds=seconds):
                self.assertEqual(mfa.totp_at(RFC_SECRET, seconds // 30, digits=8), expected)

    def test_window_and_replay(self):
        now = 1234567890
        step = mfa.current_step(now)
        for offset in (-1, 0, 1):
            self.assertEqual(mfa.matching_step(RFC_SECRET, mfa.totp_at(RFC_SECRET, step + offset), now=now), step + offset)
        self.assertIsNone(mfa.matching_step(RFC_SECRET, mfa.totp_at(RFC_SECRET, step + 2), now=now))
        self.assertIsNone(mfa.matching_step(RFC_SECRET, mfa.totp_at(RFC_SECRET, step), last_used_step=step, now=now))
        self.assertIsNone(mfa.matching_step(RFC_SECRET, 'abcdef', now=now))
        self.assertIsNone(mfa.matching_step(RFC_SECRET, '', now=now))

    def test_secret_uri_and_qr(self):
        secret = mfa.new_totp_secret()
        self.assertEqual(len(secret), 32)
        uri = mfa.provisioning_uri(secret, 'alice')
        self.assertTrue(uri.startswith('otpauth://totp/RouterFleet%20Improved%3Aalice?secret='))
        self.assertIn('<svg', mfa.qr_svg(uri))


class TotpSignInTests(TestCase):
    def setUp(self):
        self.user = make_user(50, username='alice', password='right-password')
        self.totp = UserTotp.objects.create(user=self.user, secret=RFC_SECRET, confirmed=True)

    def code(self, offset=0):
        return mfa.totp_at(RFC_SECRET, mfa.current_step() + offset)

    def password(self, password='right-password'):
        return self.client.post('/accounts/login/', {'username': 'alice', 'password': password})

    def signed_in(self):
        return '_auth_user_id' in self.client.session

    def test_password_then_code(self):
        self.assertRedirects(self.password(), '/accounts/login/verify/', fetch_redirect_response=False)
        self.assertFalse(self.signed_in(), 'the password alone signs nobody in')
        self.assertContains(self.client.post('/accounts/login/verify/', {'code': '000000'}), 'not correct')
        self.assertFalse(self.signed_in())
        self.assertRedirects(self.client.post('/accounts/login/verify/', {'code': self.code()}), '/',
                             fetch_redirect_response=False)
        self.assertTrue(self.signed_in())
        self.assertTrue(LoginRecord.objects.filter(username='alice', successful=True).exists())
        self.assertTrue(LoginRecord.objects.filter(username='alice', successful=False).exists())

    def test_a_code_is_never_accepted_twice(self):
        code = self.code()
        self.password()
        self.client.post('/accounts/login/verify/', {'code': code})
        self.client.logout()
        self.password()
        self.assertContains(self.client.post('/accounts/login/verify/', {'code': code}), 'already been used')
        self.assertFalse(self.signed_in())

    def test_the_code_step_needs_the_password_first_and_expires(self):
        self.assertRedirects(self.client.get('/accounts/login/verify/'), '/accounts/login/', fetch_redirect_response=False)
        self.password()
        with mock.patch('accounts.views.time.time', return_value=10 ** 11):
            self.assertRedirects(self.client.post('/accounts/login/verify/', {'code': self.code()}), '/accounts/login/',
                                 fetch_redirect_response=False)
        self.assertFalse(self.signed_in())

    def test_wrong_codes_count_towards_the_lockout(self):
        self.password()
        for _ in range(5):
            self.client.post('/accounts/login/verify/', {'code': '000000'})
        self.assertContains(self.client.post('/accounts/login/verify/', {'code': self.code()}), 'Too many failed')
        self.assertFalse(self.signed_in())

    def test_an_unconfirmed_app_is_not_asked_for(self):
        self.totp.confirmed = False
        self.totp.save()
        self.assertRedirects(self.password(), '/', fetch_redirect_response=False)
        self.assertTrue(self.signed_in())


class TotpSettingsTests(TestCase):
    def setUp(self):
        self.user = make_user(10, username='bob', password='pw')
        self.client.force_login(self.user)

    def test_turn_on_and_off(self):
        page = self.client.get('/user/settings/totp/')
        self.assertContains(page, '<svg')
        totp = UserTotp.objects.get(user=self.user)
        self.assertFalse(totp.confirmed)
        self.client.post('/user/settings/totp/', {'action': 'enable', 'code': '000000'})
        self.assertFalse(UserTotp.objects.get(user=self.user).confirmed)
        self.client.post('/user/settings/totp/', {'action': 'enable', 'code': mfa.totp_at(totp.secret, mfa.current_step())})
        self.assertTrue(UserTotp.objects.get(user=self.user).confirmed)
        self.assertContains(self.client.get('/user/settings/'), 'rf-set-pill on">On')
        # Turning it off needs a code, and not the one just used
        self.client.post('/user/settings/totp/', {'action': 'disable', 'code': mfa.totp_at(totp.secret, mfa.current_step())})
        self.assertTrue(UserTotp.objects.filter(user=self.user).exists())
        self.client.post('/user/settings/totp/', {'action': 'disable', 'code': mfa.totp_at(totp.secret, mfa.current_step() + 1)})
        self.assertFalse(UserTotp.objects.filter(user=self.user).exists())

    def test_the_secret_is_encrypted_at_rest(self):
        self.client.get('/user/settings/totp/')
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute('SELECT secret FROM user_manager_usertotp')
            self.assertTrue(cursor.fetchone()[0].startswith('gAAAAA'))


class ResetTests(TestCase):
    def setUp(self):
        self.admin = make_user(50, username='admin')
        self.victim = make_user(10, username='carol')
        UserTotp.objects.create(user=self.victim, secret=RFC_SECRET, confirmed=True)
        UserPasskey.objects.create(user=self.victim, name='k', credential_id='x', public_key='y', rp_id=HOST)
        self.acl = UserAcl.objects.get(user=self.victim)

    def test_an_administrator_resets_with_the_username_as_confirmation(self):
        self.client.force_login(self.admin)
        url = f'/user/manage/?uuid={self.acl.uuid}'
        self.assertContains(self.client.get(url), '1 passkey')
        self.client.post(url, {'action': 'reset_mfa', 'confirmation': 'wrong'})
        self.assertTrue(UserTotp.objects.filter(user=self.victim).exists())
        self.client.post(url, {'action': 'reset_mfa', 'confirmation': 'carol'})
        self.assertFalse(UserTotp.objects.filter(user=self.victim).exists())
        self.assertFalse(UserPasskey.objects.filter(user=self.victim).exists())

    def test_the_command_line_reset(self):
        import io
        out = io.StringIO()
        call_command('reset_mfa', 'carol', stdout=out)
        self.assertIn('1 passkey(s) removed', out.getvalue())
        self.assertFalse(UserTotp.objects.filter(user=self.victim).exists())
        from django.core.management import CommandError
        with self.assertRaises(CommandError):
            call_command('reset_mfa', 'nobody')


@override_settings(ALLOWED_HOSTS=[HOST, 'testserver', '192.168.1.5'])
class PasskeyTests(TestCase):
    def setUp(self):
        self.user = make_user(30, username='dave', password='pw')
        self.authenticator = SoftAuthenticator()

    def post(self, url, body=None, origin=ORIGIN, host=HOST):
        return self.client.post(url, json.dumps(body or {}), content_type='application/json',
                                HTTP_HOST=host, HTTP_ORIGIN=origin)

    def register(self, name='Laptop'):
        self.client.force_login(self.user)
        options = self.post('/user/settings/passkeys/options/').json()
        response = self.post('/user/settings/passkeys/add/', {'name': name, 'credential': self.authenticator.create(options)})
        self.client.logout()
        return response

    def sign_in(self, origin=ORIGIN, handle=None):
        options = self.post('/accounts/passkey/options/', origin=origin).json()
        return self.post('/accounts/passkey/login/',
                         self.authenticator.get(options, handle or str(self.user.pk).encode(), origin=origin), origin=origin)

    def test_register_then_sign_in(self):
        self.assertEqual(self.register().json(), {'redirect': '/user/settings/'})
        passkey = UserPasskey.objects.get(user=self.user)
        self.assertEqual((passkey.name, passkey.rp_id), ('Laptop', HOST))
        response = self.sign_in()
        self.assertEqual(response.json(), {'redirect': '/'})
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)
        passkey.refresh_from_db()
        self.assertEqual(passkey.sign_count, 1)
        self.assertIsNotNone(passkey.last_used)
        self.assertTrue(LoginRecord.objects.filter(username='dave', successful=True).exists())

    def test_a_passkey_skips_the_code(self):
        UserTotp.objects.create(user=self.user, secret=RFC_SECRET, confirmed=True)
        self.register()
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertIn('_auth_user_id', self.client.session)

    def test_another_origin_is_refused(self):
        self.register()
        self.assertEqual(self.sign_in(origin='https://evil.example.net').status_code, 400)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertTrue(LoginRecord.objects.filter(username='(passkey)', successful=False).exists())

    def test_a_port_in_the_origin_is_the_same_relying_party(self):
        self.client.force_login(self.user)
        options = self.post('/user/settings/passkeys/options/', origin=f'{ORIGIN}:8443').json()
        response = self.post('/user/settings/passkeys/add/', {'credential': self.authenticator.create(options, origin=f'{ORIGIN}:8443')},
                             origin=f'{ORIGIN}:8443')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserPasskey.objects.get().name, 'Passkey')

    def test_a_user_handle_of_another_account_is_refused(self):
        self.register()
        other = make_user(10, username='eve')
        self.assertEqual(self.sign_in(handle=str(other.pk).encode()).status_code, 400)

    def test_a_challenge_cannot_be_answered_twice(self):
        self.register()
        options = self.post('/accounts/passkey/options/').json()
        answer = self.authenticator.get(options, str(self.user.pk).encode())
        self.assertEqual(self.post('/accounts/passkey/login/', answer).status_code, 200)
        self.client.logout()
        self.assertEqual(self.post('/accounts/passkey/login/', answer).status_code, 400)

    def test_an_ip_address_cannot_have_passkeys(self):
        response = self.post('/accounts/passkey/options/', host='192.168.1.5', origin='https://192.168.1.5')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not by an IP address', response.json()['error'])

    def test_only_the_owner_removes_a_passkey(self):
        self.register()
        passkey = UserPasskey.objects.get()
        self.client.force_login(make_user(50, username='frank'))
        self.assertEqual(self.client.post('/user/settings/passkeys/delete/', {'uuid': str(passkey.uuid)}).status_code, 404)
        self.client.force_login(self.user)
        self.client.post('/user/settings/passkeys/delete/', {'uuid': str(passkey.uuid)})
        self.assertFalse(UserPasskey.objects.exists())

    def test_get_is_not_accepted(self):
        self.assertEqual(self.client.get('/accounts/passkey/options/', HTTP_HOST=HOST).status_code, 405)

    def test_asking_for_a_challenge_creates_no_session(self):
        from django.contrib.sessions.models import Session
        for _ in range(5):
            response = self.post('/accounts/passkey/options/')
            self.assertEqual(response.status_code, 200)
        self.assertEqual(Session.objects.count(), 0, 'a flood of anonymous calls stores nothing')
        cookie = response.cookies['rf_passkey_challenge']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Strict')
        self.assertEqual(cookie['path'], '/accounts/passkey/')

    def test_an_answer_replayed_with_its_cookie_is_refused(self):
        self.register()
        options = self.post('/accounts/passkey/options/').json()
        cookie = self.client.cookies['rf_passkey_challenge'].value
        answer = self.authenticator.get(options, str(self.user.pk).encode())
        self.assertEqual(self.post('/accounts/passkey/login/', answer).status_code, 200)
        self.client.logout()
        # The attacker has both: the answer and the cookie it was given with,
        # and a passkey that does not advance its counter
        UserPasskey.objects.update(sign_count=0)
        self.client.cookies['rf_passkey_challenge'] = cookie
        self.assertEqual(self.post('/accounts/passkey/login/', answer).status_code, 400)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_a_forged_or_missing_cookie_is_refused(self):
        self.register()
        options = self.post('/accounts/passkey/options/').json()
        answer = self.authenticator.get(options, str(self.user.pk).encode())
        self.client.cookies['rf_passkey_challenge'] = options['challenge'] + ':1'
        self.assertEqual(self.post('/accounts/passkey/login/', answer).status_code, 400)
        del self.client.cookies['rf_passkey_challenge']
        self.assertEqual(self.post('/accounts/passkey/login/', answer).status_code, 400)
