"""System settings: HTTPS."""
import datetime
import json
import os
import shutil
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings

from audit_log.models import ChangeRecord
from routerfleet.middleware import forget_https_mode
from routerfleet.testing import make_user
from routerfleet_tools import https
from routerfleet_tools.models import HttpsSettings
from user_manager.models import UserPasskey


def make_certificate(names=('rf.example.net',), days=90, key=None):
    key = key or ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=max(1, 1 - days)))
                   .not_valid_after(now + datetime.timedelta(days=days))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
                   .sign(key, hashes.SHA256()))
    return (certificate.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode())


class VolumeTestCase(TestCase):
    def setUp(self):
        self.volume = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.volume, True)
        override = override_settings(HTTPS_CERT_DIR=self.volume)
        override.enable()
        self.addCleanup(override.disable)
        forget_https_mode()
        self.addCleanup(forget_https_mode)

    def state(self):
        with open(os.path.join(self.volume, https.STATE_FILE)) as handle:
            return json.load(handle)


class ValidationTests(TestCase):
    def test_domain(self):
        self.assertEqual(https.clean_domain(' RF.Example.NET. '), 'rf.example.net')
        for bad in ('192.0.2.1', 'localhost', 'a b.com', 'evil.com;\nserver {', ''):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                https.clean_domain(bad)

    def test_proxies(self):
        self.assertEqual(https.clean_proxies('192.0.2.5\n10.0.0.0/24, 2001:db8::/32'),
                         ['192.0.2.5/32', '10.0.0.0/24', '2001:db8::/32'])
        for bad in ('', 'not-an-ip', '1.2.3.4; include /etc/passwd', '0.0.0.0/0'):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                https.clean_proxies(bad)

    def test_certificate_checks(self):
        certificate, key = make_certificate()
        checked = https.check_certificate(certificate, key, 'rf.example.net')
        self.assertEqual(checked['names'], ['rf.example.net'])
        self.assertFalse(checked['trusted'], 'self-made: not trusted by browsers')
        _, other_key = make_certificate()
        with self.assertRaisesRegex(ValidationError, 'does not belong'):
            https.check_certificate(certificate, other_key, 'rf.example.net')
        with self.assertRaisesRegex(ValidationError, 'not issued for'):
            https.check_certificate(certificate, key, 'other.example.net')
        expired, expired_key = make_certificate(days=-2)
        with self.assertRaisesRegex(ValidationError, 'expired'):
            https.check_certificate(expired, expired_key, 'rf.example.net')
        with self.assertRaisesRegex(ValidationError, 'not a PEM'):
            https.check_certificate('nonsense', key, 'rf.example.net')
        wildcard, wildcard_key = make_certificate(names=('*.example.net',))
        self.assertTrue(https.check_certificate(wildcard, wildcard_key, 'rf.example.net'))


class PageTests(VolumeTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(make_user(50))

    def review(self, **data):
        return self.client.post('/system/https/', dict(action='review', **data))

    def apply(self, page, answer=None):
        data = {'action': 'apply', 'token': page.context['token']}
        if page.context['needs_sum']:
            data['answer'] = str(page.context['first'] + page.context['second'] if answer is None else answer)
        return self.client.post('/system/https/', data)

    def test_only_administrators(self):
        self.client.force_login(make_user(40))
        self.assertContains(self.client.get('/system/https/'), 'Access Denied')
        self.assertContains(self.client.post('/system/https/', {'action': 'apply'}), 'Access Denied')

    def test_an_upgraded_installation_keeps_its_env_behaviour(self):
        self.assertEqual(HttpsSettings.load().mode, 'env')
        self.assertFalse(os.path.exists(os.path.join(self.volume, https.STATE_FILE)), 'nothing written by itself')

    def test_selfsigned_is_reviewed_then_applied_and_recorded(self):
        page = self.review(mode='selfsigned', domain='rf.example.net')
        self.assertFalse(page.context['needs_sum'])
        self.assertFalse(page.context['passkeys_ok'])
        self.assertEqual(HttpsSettings.load().mode, 'env', 'a review changes nothing')
        self.apply(page)
        self.assertEqual(self.state(), {'mode': 'selfsigned', 'generation': 1, 'domain': 'rf.example.net'})
        self.assertTrue(ChangeRecord.objects.filter(model_label='routerfleet_tools.HttpsSettings').exists())

    def test_https_off_needs_the_sum_once(self):
        page = self.review(mode='plain')
        self.assertTrue(page.context['needs_sum'])
        self.assertContains(page, 'btn btn-danger btn-lg')
        self.assertContains(page, 'unencrypted')
        right = page.context['first'] + page.context['second']
        self.apply(page, answer=right + 1)
        self.assertEqual(HttpsSettings.load().mode, 'env')
        self.apply(page, answer=right)   # used up by the wrong answer
        self.assertEqual(HttpsSettings.load().mode, 'env')
        self.apply(self.review(mode='plain'))
        self.assertEqual(self.state()['mode'], 'plain')

    def test_proxy_mode_lists_the_proxies(self):
        page = self.review(mode='proxy', trusted_proxies='192.0.2.5')
        self.assertTrue(page.context['needs_sum'])
        self.apply(page)
        self.assertEqual(self.state()['trusted_proxies'], ['192.0.2.5/32'])
        self.assertContains(self.review(mode='proxy', trusted_proxies='0.0.0.0/0'), 'Trusting every address')

    def test_passkey_users_are_warned(self):
        UserPasskey.objects.create(user=make_user(10), name='k', credential_id='c', public_key='p', rp_id='rf.example.net')
        page = self.review(mode='selfsigned')
        self.assertTrue(any('1 user signs in with a passkey' in note for note in page.context['notes']))

    def test_custom_certificate_key_never_reaches_the_database(self):
        certificate, key = make_certificate()
        page = self.review(mode='custom', domain='rf.example.net', certificate=certificate, private_key=key)
        secret = key.split('\n')[1]
        from django.contrib.sessions.models import Session
        self.assertFalse(any(secret in s.session_data for s in Session.objects.all()), 'not in the session table')
        self.apply(page)
        current = HttpsSettings.load()
        self.assertEqual(current.mode, 'custom')
        self.assertNotIn(secret, json.dumps([str(getattr(current, f.name)) for f in current._meta.fields]))
        keyfile = os.path.join(self.volume, 'custom', 'privkey.pem')
        self.assertEqual(oct(os.stat(keyfile).st_mode & 0o777), '0o600')
        for name in ('custom/fullchain.pem', https.STATE_FILE):
            self.assertEqual(oct(os.stat(os.path.join(self.volume, name)).st_mode & 0o777), '0o600', name)
        with open(keyfile) as handle:
            self.assertIn('PRIVATE KEY', handle.read())
        self.assertFalse(os.listdir(os.path.join(self.volume, 'custom', 'pending')), 'the pending copy is gone')
        self.assertNotContains(self.client.get('/system/https/'), secret)

    def test_letsencrypt_needs_the_agreement_and_a_domain(self):
        self.assertContains(self.review(mode='letsencrypt', domain='rf.example.net', email='a@example.net'),
                            'Subscriber Agreement')
        self.assertContains(self.review(mode='letsencrypt', domain='192.0.2.1', email='a@example.net', accept_terms='on'),
                            'not an IP address')
        page = self.review(mode='letsencrypt', domain='rf.example.net', email='a@example.net', accept_terms='on')
        self.assertTrue(page.context['passkeys_ok'])
        self.apply(page)
        self.assertEqual(self.state()['email'], 'a@example.net')

    def test_status_from_nginx_is_shown(self):
        current = HttpsSettings.load()
        current.mode, current.generation = 'plain', 4
        current.save()
        with open(os.path.join(self.volume, https.STATUS_FILE), 'w') as handle:
            json.dump({'mode': 'plain', 'generation': 4, 'applied': False, 'error': 'nginx: [emerg] something'}, handle)
        page = self.client.get('/system/https/')
        self.assertContains(page, 'kept the previous configuration')
        self.assertContains(page, 'nginx: [emerg] something')

    @override_settings(HTTPS_CERT_DIR='/nonexistent/volume')
    def test_an_old_compose_file_is_explained(self):
        page = self.client.get('/system/https/')
        self.assertContains(page, 'https_cert:/certificate')
        self.assertContains(self.review(mode='selfsigned'), 'not shared')


class CookieTests(VolumeTestCase):
    def test_env_mode_leaves_cookies_alone_and_managed_modes_follow_the_request(self):
        make_user(50)   # with no user at all, sign-in redirects to creating the first one
        def cookie(**headers):
            # A new browser each time, so the CSRF cookie is set again
            return self.client_class().get('/accounts/login/', headers=headers).cookies['csrftoken']['secure']
        self.assertFalse(cookie())
        HttpsSettings.objects.update_or_create(name='https_settings', defaults={'mode': 'selfsigned'})
        forget_https_mode()
        self.assertTrue(cookie(**{'X-Forwarded-Proto': 'https'}))
        self.assertFalse(cookie())


class CommandTests(VolumeTestCase):
    def test_reset_and_mode(self):
        call_command('https', 'mode', 'plain', stdout=open(os.devnull, 'w'))
        self.assertEqual(self.state()['mode'], 'plain')
        import io
        out = io.StringIO()
        call_command('https', 'status', stdout=out)
        self.assertIn('"configured": "plain"', out.getvalue())
        call_command('https', 'reset', stdout=open(os.devnull, 'w'))
        self.assertEqual(self.state()['mode'], 'env')
        self.assertEqual(HttpsSettings.load().mode, 'env')


class DeployedCertificateTests(VolumeTestCase):
    """The certificate already deployed (nginx.pem) is kept on upgrade and
    never written by any mode."""

    def test_selfsigned_deployed_certificate_is_recognised(self):
        certificate, key = make_certificate(names=('localhost',))
        with open(os.path.join(self.volume, 'nginx.pem'), 'w') as handle:
            handle.write(certificate)
        served = https.served_certificate()
        self.assertTrue(served['self_signed'])
        self.assertFalse(served['trusted'])

    def test_no_mode_writes_the_deployed_certificate(self):
        certificate, key = make_certificate()
        for name, content in (('nginx.pem', certificate), ('nginx.key', key)):
            with open(os.path.join(self.volume, name), 'w') as handle:
                handle.write(content)
        before = {n: open(os.path.join(self.volume, n)).read() for n in ('nginx.pem', 'nginx.key')}
        current = HttpsSettings.load()
        for mode in ('selfsigned', 'plain', 'env'):
            current.mode = mode
            https.write_state(current)
        self.assertEqual(before, {n: open(os.path.join(self.volume, n)).read() for n in ('nginx.pem', 'nginx.key')})


class SystemSettingsTests(TestCase):
    """System Settings: the hub, notifications and channels."""

    def setUp(self):
        from message_center.models import MessageChannel
        self.Channel = MessageChannel
        self.client.force_login(make_user(50))

    def test_hub_and_levels(self):
        page = self.client.get('/system/')
        self.assertContains(page, '/system/https/')
        self.assertContains(page, '/system/notifications/')
        self.client.force_login(make_user(40))
        page = self.client.get('/system/')
        self.assertNotContains(page, '/system/https/')
        self.assertContains(self.client.get('/system/notifications/channel/'), 'Telegram')
        self.assertContains(self.client.get('/system/notifications/email/'), 'Access Denied')
        self.client.force_login(make_user(30))
        self.assertContains(self.client.get('/system/'), 'Access Denied')

    def test_removed_callmebot_channels_are_listed(self):
        self.Channel.objects.create(name='whatsapp', channel_type='callmebot', destination='+391234', enabled=False)
        page = self.client.get('/system/notifications/')
        self.assertContains(page, 'CallMeBot is no longer supported')
        self.assertContains(page, 'whatsapp')

    def test_a_stored_token_is_never_shown_and_empty_keeps_it(self):
        from unittest import mock
        channel = self.Channel.objects.create(name='tg', channel_type='telegram', destination='42', token='123:SECRETBOT')
        page = self.client.get(f'/system/notifications/channel/?uuid={channel.uuid}')
        self.assertNotContains(page, 'SECRETBOT')
        self.assertContains(page, 'stored - leave empty to keep it')
        with mock.patch('message_center.channels.requests.get', return_value=mock.Mock(status_code=200)) as get:
            self.client.post(f'/system/notifications/channel/?uuid={channel.uuid}', {
                'name': 'tg', 'channel_type': 'telegram', 'destination': '42', 'token': '', 'enabled': 'on',
                'backup_fail': 'on'})
        channel.refresh_from_db()
        self.assertEqual(channel.token, '123:SECRETBOT')
        self.assertTrue(channel.backup_fail)
        self.assertIn('bot123:SECRETBOT', get.call_args.args[0], 'the test message used the kept token')

    def test_new_channel_offers_three_types(self):
        page = self.client.get('/system/notifications/channel/').content.decode()
        for value in ('email', 'ntfy', 'telegram'):
            self.assertIn(f'name="channel_type" value="{value}"', page)
        self.assertNotIn('value="callmebot"', page)
