"""System settings: HTTPS - the checks and failures the main tests leave out."""
import datetime
import os
import time
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.core.exceptions import ValidationError
from django.test import TestCase

from routerfleet.testing import make_user
from routerfleet_tools import https
from routerfleet_tools.models import HttpsSettings
from routerfleet_tools.test_https import VolumeTestCase, make_certificate

TOKEN = 'review-token-0123456789'


def plain_certificate(name='rf.example.net', starts_in_days=0, with_names=True):
    """A self-made certificate; without names it has no SubjectAlternativeName."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    start = now + datetime.timedelta(days=starts_in_days) - datetime.timedelta(minutes=5)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(start)
               .not_valid_after(start + datetime.timedelta(days=90)))
    if with_names:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
    certificate = builder.sign(key, hashes.SHA256())
    return (certificate.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode())


class CheckTests(TestCase):
    """The validation helpers refuse what nginx could not use."""

    def test_email_with_a_brace_is_refused(self):
        self.assertEqual(https.clean_email(' admin@example.net '), 'admin@example.net')
        with self.assertRaisesRegex(ValidationError, 'valid email'):
            https.clean_email('rf{x}@example.net')

    def test_key_that_is_not_pem(self):
        certificate, _ = make_certificate()
        with self.assertRaisesRegex(ValidationError, 'not an unencrypted PEM key'):
            https.check_certificate(certificate, 'not a key', 'rf.example.net')

    def test_certificate_not_valid_yet(self):
        certificate, key = plain_certificate(starts_in_days=3)
        with self.assertRaisesRegex(ValidationError, 'valid only from'):
            https.check_certificate(certificate, key, 'rf.example.net')

    def test_certificate_without_names_and_without_a_domain(self):
        certificate, key = plain_certificate(with_names=False)
        checked = https.check_certificate(certificate, key, '')
        self.assertEqual(checked['names'], [])
        self.assertFalse(checked['trusted'])

    def test_wildcard_domain_is_never_called_trusted(self):
        self.assertFalse(https.trusted_by_browsers([], '*.example.net'))

    def test_a_chain_that_verifies_is_trusted(self):
        certificate, _ = make_certificate()
        chain = x509.load_pem_x509_certificates(certificate.encode())
        with mock.patch('cryptography.x509.verification.Store'), \
                mock.patch('cryptography.x509.load_pem_x509_certificates', return_value=chain), \
                mock.patch('cryptography.x509.verification.PolicyBuilder') as builder:
            self.assertTrue(https.trusted_by_browsers(chain, 'rf.example.net'))
        builder.return_value.store.return_value.build_server_verifier.return_value.verify.assert_called_once()


class PasskeyRequirementTests(TestCase):
    """Passkeys need a domain name: an IP address in the settings is not one."""

    def test_an_ip_address_is_not_a_name(self):
        settings = HttpsSettings(mode='selfsigned', domain='192.0.2.30')
        requirements = dict(https.passkey_requirements(settings, status={}))
        self.assertFalse(requirements['Reached by a domain name, not an IP address'])
        self.assertTrue(requirements['Served over HTTPS'])


class VolumeFileTests(VolumeTestCase):
    """Files on the certificate volume: failed writes, pending reviews, tokens."""

    def checked(self):
        return {'key': b'test-only key bytes', 'pem': b'certificate bytes'}

    def test_a_failed_write_leaves_no_temporary_file(self):
        target = os.path.join(self.volume, 'out.json')
        with mock.patch('routerfleet_tools.https.os.chmod', side_effect=OSError('read-only')), \
                self.assertRaises(OSError):
            https._write_atomically(target, b'{}')
        self.assertEqual(os.listdir(self.volume), [])

    def test_a_failed_replace_after_the_file_was_moved(self):
        target = os.path.join(self.volume, 'out.json')

        def gone_then_fail(source, destination):
            os.remove(source)
            raise OSError('cross-device link')

        with mock.patch('routerfleet_tools.https.os.replace', side_effect=gone_then_fail), \
                self.assertRaises(OSError):
            https._write_atomically(target, b'{}')
        self.assertEqual(os.listdir(self.volume), [])

    def test_a_bad_token_is_refused(self):
        with self.assertRaises(ValueError):
            https._pending_path('../../etc')
        https.discard_pending_certificate('../../etc')   # ignored, nothing raised

    def test_old_reviews_are_cleared(self):
        base = os.path.join(self.volume, https.CUSTOM_DIR, https.PENDING_DIR)
        old = os.path.join(base, 'old-review-0123456789')
        os.makedirs(old)
        two_hours_ago = time.time() - 7200
        os.utime(old, (two_hours_ago, two_hours_ago))
        recent = os.path.join(base, 'recent-review-0123456789')
        os.makedirs(recent)
        https.store_pending_certificate(TOKEN, self.checked())
        self.assertEqual(sorted(os.listdir(base)), sorted([TOKEN, 'recent-review-0123456789']))

    def test_activating_a_missing_review(self):
        self.assertFalse(https.activate_pending_certificate(TOKEN))

    def test_activating_a_stored_review(self):
        https.store_pending_certificate(TOKEN, self.checked())
        self.assertTrue(https.activate_pending_certificate(TOKEN))
        with open(os.path.join(self.volume, https.CUSTOM_DIR, 'privkey.pem'), 'rb') as handle:
            self.assertEqual(handle.read(), b'test-only key bytes')


class PageEdgeTests(VolumeTestCase):
    """The HTTPS page: a bad mode, a missing certificate, HSTS, and failed applies."""

    def setUp(self):
        super().setUp()
        self.client.force_login(make_user(50))

    def review(self, **data):
        return self.client.post('/system/https/', dict(action='review', **data))

    def apply(self, page):
        data = {'action': 'apply', 'token': page.context['token']}
        if page.context['needs_sum']:
            data['answer'] = str(page.context['first'] + page.context['second'])
        return self.client.post('/system/https/', data, follow=True)

    def test_unknown_mode(self):
        self.assertContains(self.review(mode='telnet'), 'Choose a mode.')

    def test_custom_needs_a_certificate(self):
        self.assertContains(self.review(mode='custom', domain='rf.example.net'), 'Paste the certificate')

    def test_custom_without_a_new_certificate_keeps_the_current_one(self):
        current = HttpsSettings.load()
        current.mode, current.domain = 'custom', 'rf.example.net'
        current.save()
        page = self.review(mode='custom', domain='rf.example.net')
        self.assertIsNone(page.context['proposal']['certificate'])

    def test_plain_after_letsencrypt_warns_about_hsts(self):
        current = HttpsSettings.load()
        current.mode, current.domain = 'letsencrypt', 'rf.example.net'
        current.save()
        page = self.review(mode='plain')
        self.assertTrue(any('HSTS' in note for note in page.context['notes']))
        self.assertTrue(any('HTTPS is being switched off' in note for note in page.context['notes']))

    def test_a_reviewed_certificate_that_vanished_is_not_applied(self):
        certificate, key = make_certificate()
        page = self.review(mode='custom', domain='rf.example.net', certificate=certificate, private_key=key)
        https.discard_pending_certificate(page.context['token'])
        result = self.apply(page)
        self.assertContains(result, 'no longer there')
        self.assertEqual(HttpsSettings.load().mode, 'env')

    def test_a_volume_that_can_not_be_written(self):
        page = self.review(mode='selfsigned')
        with mock.patch('routerfleet_tools.https.write_state', side_effect=OSError('disk full')):
            result = self.apply(page)
        self.assertContains(result, 'Saved, not applied')
        self.assertEqual(HttpsSettings.load().mode, 'selfsigned')
