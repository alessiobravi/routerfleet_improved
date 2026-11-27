"""Trust on first use, against a real SSH server on localhost: the point is
what reaches the server when the key does not match, so nothing is mocked."""
import socket
import threading

import paramiko
from django.contrib.auth.models import User
from django.test import TestCase

from router_manager.models import Router, SSHHostKey
from routerlib import host_keys
from routerlib.functions import connect_to_ssh
from user_manager.models import UserAcl


class FakeDevice(paramiko.ServerInterface):
    def __init__(self):
        self.passwords = []

    def check_auth_password(self, username, password):
        self.passwords.append(password)
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username):
        return 'password'


class Server:
    """Accepts connections on a free port, presenting `key`, until stopped."""

    def __init__(self, key):
        self.key = key
        self.device = FakeDevice()
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.running = True
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            transport = paramiko.Transport(conn)
            transport.add_server_key(self.key)
            try:
                transport.start_server(server=self.device)
                transport.accept(2)
            except Exception:
                pass

    def stop(self):
        self.running = False
        self.sock.close()


class HostKeyTests(TestCase):
    first = paramiko.ECDSAKey.generate()
    impostor_same_type = paramiko.ECDSAKey.generate()
    impostor_other_type = paramiko.RSAKey.generate(2048)

    def serve(self, key):
        server = Server(key)
        self.addCleanup(server.stop)
        return server

    def connect(self, server):
        connect_to_ssh('127.0.0.1', server.port, 'admin', 'the-password').close()

    def test_the_first_connection_stores_the_key_and_the_next_one_needs_it(self):
        server = self.serve(self.first)
        self.connect(server)
        stored = SSHHostKey.objects.get(address='127.0.0.1', port=server.port)
        self.assertEqual(stored.fingerprint, host_keys.fingerprint(self.first))
        self.assertEqual(stored.key_type, self.first.get_name())
        self.connect(server)
        self.assertEqual(SSHHostKey.objects.count(), 1)
        self.assertEqual(server.device.passwords, ['the-password'] * 2)

    def assert_refused(self, impostor_key):
        genuine = self.serve(self.first)
        self.connect(genuine)
        genuine.stop()
        impostor = self.serve(impostor_key)
        # The impostor takes over the address and port the key was stored for
        SSHHostKey.objects.filter(port=genuine.port).update(port=impostor.port)
        with self.assertRaises(host_keys.HostKeyChanged) as refused:
            self.connect(impostor)
        self.assertEqual(impostor.device.passwords, [], 'the password reached the impostor')
        self.assertIn(host_keys.fingerprint(self.first), str(refused.exception))
        self.assertIn(host_keys.fingerprint(impostor_key), str(refused.exception))
        self.assertEqual(SSHHostKey.objects.get().fingerprint, host_keys.fingerprint(self.first))
        return impostor

    def test_a_different_key_is_refused_before_the_password_is_sent(self):
        self.assert_refused(self.impostor_same_type)

    def test_a_key_of_another_type_is_refused_too(self):
        # An impostor cannot get around the stored key by offering another algorithm
        self.assert_refused(self.impostor_other_type)

    def test_forgetting_trusts_the_next_key(self):
        replaced = self.assert_refused(self.impostor_same_type)
        self.assertEqual(host_keys.forget('127.0.0.1', replaced.port), 1)
        self.connect(replaced)
        self.assertEqual(SSHHostKey.objects.get().fingerprint, host_keys.fingerprint(self.impostor_same_type))

    def test_a_failed_first_connection_stores_nothing(self):
        class Refusing(FakeDevice):
            def check_auth_password(self, username, password):
                return paramiko.AUTH_FAILED
        server = self.serve(self.first)
        server.device = Refusing()
        with self.assertRaises(paramiko.AuthenticationException):
            self.connect(server)
        self.assertFalse(SSHHostKey.objects.exists())

    def test_the_policy_refuses_whenever_a_key_is_stored(self):
        stored = SSHHostKey(address='h', port=22, key_type=self.first.get_name(),
                            key_base64=self.first.get_base64(), fingerprint=host_keys.fingerprint(self.first))
        with self.assertRaises(host_keys.HostKeyChanged):
            host_keys.TrustOnFirstUse(stored).missing_host_key(None, 'h', self.impostor_other_type)
        host_keys.TrustOnFirstUse(None).missing_host_key(None, 'h', self.first)  # first use: accepted

    def test_known_hosts_name(self):
        self.assertEqual(host_keys.known_hosts_name('10.0.0.1', 22), '10.0.0.1')
        self.assertEqual(host_keys.known_hosts_name('10.0.0.1', 2222), '[10.0.0.1]:2222')


class ForgetHostKeyViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('admin', password='x')
        UserAcl.objects.create(user=self.user, user_level=50)
        self.client.force_login(self.user)
        self.router = Router.objects.create(name='r1', address='10.0.0.1', port=22, router_type='routeros')
        SSHHostKey.objects.create(address='10.0.0.1', port=22, key_type='ssh-ed25519',
                                  key_base64='AAAA', fingerprint='SHA256:abc')

    def test_details_show_the_fingerprint(self):
        self.assertContains(self.client.get(f'/router/details/?uuid={self.router.uuid}'), 'SHA256:abc')

    def test_forget_needs_post_an_administrator_and_the_confirmation(self):
        url = f'/router/manage/?uuid={self.router.uuid}'
        self.client.get(url + '&action=forget_host_key')
        self.assertTrue(SSHHostKey.objects.exists())
        viewer = User.objects.create_user('viewer', password='x')
        UserAcl.objects.create(user=viewer, user_level=20)
        self.client.force_login(viewer)
        self.client.post(url, {'action': 'forget_host_key'})
        self.assertTrue(SSHHostKey.objects.exists())
        self.client.force_login(self.user)
        self.client.post(url, {'action': 'forget_host_key'})
        self.assertTrue(SSHHostKey.objects.exists(), 'without the confirmation the key stays')
        self.client.post(url, {'action': 'forget_host_key', 'confirmation': 'forget'})
        self.assertFalse(SSHHostKey.objects.exists())
        self.assertNotContains(self.client.get(f'/router/details/?uuid={self.router.uuid}'), 'SHA256:abc')


class HostKeyListTests(TestCase):
    """The SSH Keys page lists the devices' host keys; one no device uses -
    left by a deleted device - is marked unused and can be removed."""

    def setUp(self):
        self.admin = User.objects.create_user('admin', password='x')
        UserAcl.objects.create(user=self.admin, user_level=50)
        self.client.force_login(self.admin)
        Router.objects.create(name='kept', address='10.0.0.1', port=22, router_type='routeros')
        self.used = SSHHostKey.objects.create(address='10.0.0.1', port=22, key_type='ssh-ed25519',
                                              key_base64='AAAA', fingerprint='SHA256:used')
        self.orphan = SSHHostKey.objects.create(address='10.0.0.9', port=2222, key_type='ssh-rsa',
                                                key_base64='BBBB', fingerprint='SHA256:orphan')

    def test_unused_keys_are_marked_and_listed_first(self):
        page = self.client.get('/ssh_keys/').content.decode()
        self.assertIn('1 unused host key', page)
        section = page.split('id="host-keys"')[1]
        self.assertLess(section.index('10.0.0.9:2222'), section.index('10.0.0.1:22'), 'the unused first')
        orphan_row = section.split('10.0.0.9:2222')[1].split('rf-set-row')[0]
        self.assertIn('Unused', orphan_row)
        used_row = section.split('10.0.0.1:22')[1].split('rf-set-row')[0]
        self.assertNotIn('Unused', used_row)
        self.assertIn('kept', used_row)

    def test_a_deleted_device_leaves_an_unused_key(self):
        Router.objects.get(name='kept').delete()
        self.assertIn('2 unused host keys', self.client.get('/ssh_keys/').content.decode())

    def test_remove_needs_an_administrator_and_the_confirmation(self):
        viewer = User.objects.create_user('viewer', password='x')
        UserAcl.objects.create(user=viewer, user_level=40)
        self.client.force_login(viewer)
        self.assertNotContains(self.client.get('/ssh_keys/'), 'forget_host_key')
        self.client.post('/ssh_keys/', {'action': 'forget_host_key', 'host_key': self.orphan.pk, 'confirmation': 'forget'})
        self.assertTrue(SSHHostKey.objects.filter(pk=self.orphan.pk).exists(), 'not for a non-administrator')
        self.client.force_login(self.admin)
        self.client.post('/ssh_keys/', {'action': 'forget_host_key', 'host_key': self.orphan.pk})
        self.assertTrue(SSHHostKey.objects.filter(pk=self.orphan.pk).exists(), 'not without the confirmation')
        response = self.client.post('/ssh_keys/', {'action': 'forget_host_key', 'host_key': self.orphan.pk, 'confirmation': 'forget'})
        self.assertRedirects(response, '/ssh_keys/#host-keys', fetch_redirect_response=False)
        self.assertFalse(SSHHostKey.objects.filter(pk=self.orphan.pk).exists())
        self.assertTrue(SSHHostKey.objects.filter(pk=self.used.pk).exists())

    def test_the_removal_is_in_the_audit_log(self):
        from audit_log.models import ChangeRecord
        self.client.post('/ssh_keys/', {'action': 'forget_host_key', 'host_key': self.orphan.pk, 'confirmation': 'forget'})
        record = ChangeRecord.objects.filter(action='deleted', model_label='router_manager.SSHHostKey').first()
        self.assertIsNotNone(record)
        self.assertEqual(record.actor, self.admin)
