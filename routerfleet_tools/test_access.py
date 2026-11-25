"""HTTP access lists: ordered allow and deny entries, IPv4 and IPv6, the
first match decides, then the final policy; nginx renders them; a change
that would shut out its author is refused; a command opens access again."""
import json
import pathlib
import subprocess
import tempfile

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from routerfleet.testing import make_user

from . import https
from .models import AccessRule, AddressBan, HttpsSettings

ROOT = pathlib.Path(__file__).resolve().parent.parent


class EvaluationTests(SimpleTestCase):
    def test_first_match_then_the_final_policy(self):
        rules = [('allow', '192.0.2.10/32'), ('deny', '192.0.2.0/24'), ('deny', '2001:db8:bad::/48'), ('allow', '2001:db8::/32')]
        self.assertTrue(https.access_allows('192.0.2.10', rules, 'deny'))
        self.assertFalse(https.access_allows('192.0.2.11', rules, 'allow'))
        self.assertFalse(https.access_allows('2001:db8:bad::1', rules, 'allow'))
        self.assertTrue(https.access_allows('2001:db8:1::1', rules, 'deny'))
        self.assertTrue(https.access_allows('198.51.100.1', rules, 'allow'))
        self.assertFalse(https.access_allows('198.51.100.1', rules, 'deny'))
        self.assertTrue(https.access_allows('::ffff:192.0.2.10', rules, 'deny'), 'an IPv4 client on a dual-stack socket')

    def test_entries_are_cleaned(self):
        self.assertEqual(https.clean_network('192.0.2.7'), '192.0.2.7/32')
        self.assertEqual(https.clean_network('192.0.2.7/24'), '192.0.2.0/24')
        self.assertEqual(https.clean_network('[2001:DB8::1]'), '2001:db8::1/128')
        for bad in ('', 'example.net', '1.2.3.4; include x', '300.1.1.1'):
            with self.assertRaises(ValueError):
                https.clean_network(bad)


class NginxRenderingTests(SimpleTestCase):
    def render(self, state):
        script = ROOT / 'containers/nginx/rf_https.sh'
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'state.json'
            path.write_text(json.dumps(state))
            driver = pathlib.Path(folder) / 'run.sh'
            driver.write_text('STATE="$1"\n'
                              'eval "$(sed -n \'/^valid_net()/p\' "$2")"\n'
                              'eval "$(awk \'/^access_lines\\(\\) \\{/,/^}/\' "$2")"\n'
                              'eval "$(awk \'/^with_access\\(\\) \\{/,/^}/\' "$2")"\n'
                              'printf \'server {\\n    listen 80;\\n    location /.well-known/acme-challenge/ {\\n        root /x;\\n    }\\n}\\n\' | with_access\n')
            result = subprocess.run(['sh', str(driver), str(path), str(script)], capture_output=True, text=True)
        return result.stdout, result.stderr

    def test_in_order_then_the_final_policy_acme_open(self):
        out, err = self.render({'access': {'rules': [{'action': 'allow', 'network': '192.0.2.0/24'},
                                                     {'action': 'deny', 'network': '2001:db8::/32'}], 'default': 'deny'}})
        self.assertEqual(err, '')
        lines = [line.strip() for line in out.splitlines()]
        self.assertEqual(lines[1:4], ['allow 192.0.2.0/24;', 'deny 2001:db8::/32;', 'deny all;'])
        self.assertIn('allow all;', lines[lines.index('location /.well-known/acme-challenge/ {') + 1])

    def test_nothing_set_nothing_added_and_a_bad_entry_refused(self):
        out, _ = self.render({'mode': 'env'})
        self.assertNotIn('allow', out)
        _, err = self.render({'access': {'rules': [{'action': 'allow', 'network': '1.2.3.4; include /etc/passwd'}]}})
        self.assertIn('not an address', err)


@override_settings(HTTPS_CERT_DIR=None)
class PageTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        override = override_settings(HTTPS_CERT_DIR=self.folder.name)
        override.enable()
        self.addCleanup(override.disable)
        self.client.force_login(make_user(50))

    def post(self, **data):
        return self.client.post('/system/https/access/', data, REMOTE_ADDR='192.0.2.50')

    def state(self):
        return json.loads((pathlib.Path(self.folder.name) / https.STATE_FILE).read_text())['access']

    def test_add_reorder_remove_and_the_state_nginx_reads(self):
        self.post(action='add', verdict='allow', network='192.0.2.0/24', place='end', note='office')
        self.post(action='add', verdict='deny', network='2001:db8::/32', place='start')
        self.assertEqual([(r.action, r.network) for r in AccessRule.objects.all()],
                         [('deny', '2001:db8::/32'), ('allow', '192.0.2.0/24')])
        self.assertEqual(self.state()['rules'][0], {'action': 'deny', 'network': '2001:db8::/32'})
        deny = AccessRule.objects.get(action='deny')
        self.post(action='down', uuid=str(deny.uuid))
        self.assertEqual([r.action for r in AccessRule.objects.all()], ['allow', 'deny'])
        self.post(action='delete', uuid=str(deny.uuid))
        self.assertEqual(AccessRule.objects.count(), 1)

    def test_a_change_that_shuts_out_its_author_is_refused(self):
        page = self.post(action='add', verdict='deny', network='192.0.2.0/24')
        self.assertContains(page, 'would shut out your own address (192.0.2.50)')
        self.assertFalse(AccessRule.objects.exists())
        page = self.post(action='default', default='deny')
        self.assertContains(page, 'would be shut out')
        self.assertEqual(HttpsSettings.load().access_default, 'allow')
        self.post(action='add', verdict='allow', network='192.0.2.50')
        self.post(action='default', default='deny')
        self.assertEqual(HttpsSettings.load().access_default, 'deny')
        self.assertEqual(self.state()['default'], 'deny')

    def test_administrators_only(self):
        self.client.force_login(make_user(40))
        self.assertContains(self.client.get('/system/https/access/'), 'Access Denied')

    def test_the_command_opens_access_again(self):
        AccessRule.objects.create(action='deny', network='0.0.0.0/0')
        HttpsSettings.objects.filter(pk=HttpsSettings.load().pk).update(access_default='deny')
        call_command('http_access', '--reset', stdout=open('/dev/null', 'w'))
        self.assertFalse(AccessRule.objects.exists())
        self.assertEqual(HttpsSettings.load().access_default, 'allow')
        self.assertEqual(self.state(), {'rules': [], 'default': 'allow', 'bans': []})


class BanTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        override = override_settings(HTTPS_CERT_DIR=self.folder.name, LOGIN_LOCKOUT_ATTEMPTS=100)
        override.enable()
        self.addCleanup(override.disable)
        settings_row = HttpsSettings.load()
        settings_row.ban_failures = 3
        settings_row.save()
        make_user(50, username='owner', password='Right-password-2026!')   # else the login page sends to set-up

    def fail(self, address, times=3):
        for _ in range(times):
            self.client.post('/accounts/login/', {'username': 'nobody', 'password': 'wrong'}, REMOTE_ADDR=address)

    def test_failures_ban_the_address_at_nginx_and_in_the_application(self):
        from .bans import is_banned
        self.fail('198.51.100.7', 2)
        self.assertFalse(is_banned('198.51.100.7'))
        self.fail('198.51.100.7', 1)
        self.assertTrue(is_banned('198.51.100.7'))
        state = json.loads((pathlib.Path(self.folder.name) / https.STATE_FILE).read_text())['access']
        self.assertEqual(state['bans'][0]['address'], '198.51.100.7')
        # the application refuses its sign-ins too, even with the right password
        page = self.client.post('/accounts/login/', {'username': 'owner', 'password': 'Right-password-2026!'},
                                REMOTE_ADDR='198.51.100.7')
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(page.status_code, 200)

    def test_whitelisted_loopback_and_disabled_are_never_banned(self):
        from .bans import is_banned
        AccessRule.objects.create(action='allow', network='192.0.2.0/24')
        self.fail('192.0.2.9', 5)
        self.fail('127.0.0.1', 5)
        self.assertFalse(is_banned('192.0.2.9'))
        self.assertFalse(is_banned('127.0.0.1'))
        HttpsSettings.objects.update(ban_enabled=False)
        self.fail('203.0.113.4', 5)
        self.assertFalse(is_banned('203.0.113.4'))

    def test_unban_settings_and_the_reset(self):
        from .bans import is_banned
        self.fail('2001:db8::66')
        self.assertTrue(is_banned('2001:db8::66'))
        self.client.force_login(make_user(50))
        ban = AddressBan.objects.get()
        self.client.post('/system/https/access/', {'action': 'unban', 'uuid': str(ban.uuid)}, REMOTE_ADDR='192.0.2.50')
        self.assertFalse(is_banned('2001:db8::66'))
        self.client.post('/system/https/access/', {'action': 'bans', 'ban_enabled': 'on', 'ban_failures': '1',
                                                   'ban_window_minutes': '5', 'ban_minutes': '30'}, REMOTE_ADDR='192.0.2.50')
        row = HttpsSettings.load()
        self.assertEqual((row.ban_failures, row.ban_window_minutes, row.ban_minutes), (3, 5, 30))
        AddressBan.objects.create(address='203.0.113.9', until=row.updated + __import__('datetime').timedelta(hours=1))
        call_command('http_access', '--reset', stdout=open('/dev/null', 'w'))
        self.assertFalse(AddressBan.objects.exists())


class EdgeTests(TestCase):
    """The paths around the main ones: refused input, an unshared volume, the
    command's listing, unusual addresses."""

    def setUp(self):
        self.client.force_login(make_user(50))

    def post(self, **data):
        return self.client.post('/system/https/access/', data, REMOTE_ADDR='192.0.2.50')

    @override_settings(HTTPS_CERT_DIR='/nonexistent/routerfleet-test')
    def test_refused_entries_unshared_volume_and_edges_of_the_order(self):
        page = self.post(action='add', verdict='allow', network='example.net')
        self.assertContains(page, 'Not added')
        page = self.client.post('/system/https/access/', {'action': 'add', 'verdict': 'allow', 'network': '192.0.2.0/24'},
                                REMOTE_ADDR='192.0.2.50', follow=True)
        self.assertContains(page, 'Saved, not applied')
        self.assertContains(page, 'The certificate volume is not shared')
        first = AccessRule.objects.get()
        self.post(action='up', uuid=str(first.uuid))       # already first: stays
        self.post(action='down', uuid=str(first.uuid))     # already last: stays
        self.assertEqual(AccessRule.objects.count(), 1)
        # removing the only entry that allows me while the final policy denies: refused
        HttpsSettings.objects.update(access_default='deny')
        self.assertContains(self.post(action='delete', uuid=str(first.uuid)), 'Not changed')
        self.post(action='bans', ban_failures='many', ban_window_minutes='0', ban_minutes='99999999')
        row = HttpsSettings.load()
        self.assertEqual((row.ban_failures, row.ban_window_minutes, row.ban_minutes, row.ban_enabled), (5, 1, 43200, False))

    def test_the_command_lists_and_resets_without_a_shared_volume(self):
        import io
        from datetime import timedelta
        from django.utils import timezone
        AccessRule.objects.create(action='allow', network='192.0.2.0/24', note='office')
        AddressBan.objects.create(address='203.0.113.9', failures=7, until=timezone.now() + timedelta(hours=1))
        out = io.StringIO()
        call_command('http_access', stdout=out)
        self.assertIn('1. allow 192.0.2.0/24  (office)', out.getvalue())
        self.assertIn('Banned: 203.0.113.9', out.getvalue())
        with override_settings(HTTPS_CERT_DIR='/nonexistent/routerfleet-test'):
            out = io.StringIO()
            call_command('http_access', '--reset', stdout=out)
        self.assertIn('not shared', out.getvalue())

    def test_unusual_addresses(self):
        from . import bans
        settings_row = HttpsSettings.load()
        self.assertTrue(bans.never_banned('not an address', settings_row))
        self.assertTrue(bans.never_banned('::', settings_row))
        settings_row.mode, settings_row.trusted_proxies = 'proxy', '198.51.100.0/24'
        self.assertTrue(bans.never_banned('198.51.100.3', settings_row))
        self.assertFalse(bans.never_banned('203.0.113.1', settings_row))
        self.assertFalse(bans.is_banned('garbage'))
        self.assertIsNone(bans.consider('garbage'))
        self.assertTrue(https.access_allows('garbage', [('deny', '0.0.0.0/0')], 'allow'))
        self.assertFalse(https.access_allows('192.0.2.1', [('allow', 'bad net'), ('deny', '0.0.0.0/0')], 'allow'))


class SharedAddressTests(TestCase):
    """The container's gateway - every client's address when Docker hands
    connections over itself - is never banned nor locked address-wide."""

    def test_the_gateway_is_never_banned(self):
        from unittest import mock
        from . import bans
        with mock.patch('console.network.gateways', return_value={'172.30.0.1'}):
            self.assertTrue(bans.never_banned('172.30.0.1', HttpsSettings.load()))
            self.assertFalse(bans.never_banned('172.30.0.9', HttpsSettings.load()))

    def test_gateways_are_read_from_the_route_tables(self):
        from console.network import gateways
        with tempfile.TemporaryDirectory() as folder:
            v4 = pathlib.Path(folder) / 'route'
            v4.write_text('Iface\tDestination\tGateway\tFlags\n'
                          'eth0\t00000000\t01001EAC\t0003\n'
                          'eth0\t00001EAC\t00000000\t0001\n')
            v6 = pathlib.Path(folder) / 'ipv6_route'
            v6.write_text('00000000000000000000000000000000 00 00000000000000000000000000000000 00 '
                          'fd7266746c7400000000000000000001 00000400 00000000 00000000 00000003 eth0\n')
            self.assertEqual(gateways(str(v4), str(v6)), {'172.30.0.1', 'fd72:6674:6c74::1'})


class ConsoleForwardedForTests(SimpleTestCase):
    """The console server trusts X-Forwarded-For: nginx sends the client as it
    sees it, alone - never appended to what the browser claimed."""

    def test_every_console_location_sends_the_client_alone(self):
        for name in ('containers/nginx/rf_https.sh', 'containers/nginx/virtualhost.conf',
                     'containers/nginx/virtualhost_noredirect.conf'):
            text = (ROOT / name).read_text()
            position = 0
            while (start := text.find('location /console/ws/', position)) != -1:
                block = text[start:text.index('}', start)]
                with self.subTest(file=name):
                    self.assertIn('X-Forwarded-For', block)
                    self.assertNotIn('proxy_add_x_forwarded_for', block)
                position = start + 1


class HstsAndEntryValidationTests(SimpleTestCase):
    """HSTS only with a certificate browsers trust; a scoped IPv6 entry and a
    proxy range that trusts (almost) everyone are refused."""

    def render(self, mode, files=()):
        with tempfile.TemporaryDirectory() as folder:
            base = pathlib.Path(folder)
            for name in files:
                (base / name).parent.mkdir(parents=True, exist_ok=True)
                (base / name).write_text('x')
            (base / https.STATE_FILE).write_text(json.dumps({'mode': mode, 'generation': 1}))
            result = subprocess.run(['sh', str(ROOT / 'containers/nginx/rf_https.sh'), 'render'], capture_output=True,
                                    text=True, env={'RF_CERT_DIR': folder, 'PATH': '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin'})
        return result.stdout

    def test_hsts_only_with_a_trusted_certificate(self):
        custom = self.render('custom', ['custom/fullchain.pem', 'custom/privkey.pem'])
        self.assertEqual(custom.count('Strict-Transport-Security'), 1)
        self.assertNotIn('Strict-Transport-Security', self.render('selfsigned'))
        self.assertNotIn('Strict-Transport-Security', self.render('plain'))
        # Let's Encrypt before its certificate is issued serves the self-signed one: no HSTS yet
        self.assertNotIn('Strict-Transport-Security', self.render('letsencrypt'))

    def test_a_scoped_ipv6_entry_is_refused(self):
        with self.assertRaises(ValueError):
            https.clean_network('fe80::1%eth0')

    def test_proxy_ranges_that_trust_almost_everyone_are_refused(self):
        from django.core.exceptions import ValidationError
        for value in ('0.0.0.0/1\n128.0.0.0/1', '10.0.0.0/7', '::/1', '2000::/3'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                https.clean_proxies(value)
        self.assertEqual(https.clean_proxies('10.0.0.0/8\n2001:db8::/32'), ['10.0.0.0/8', '2001:db8::/32'])
