"""IPv4 and IPv6 everywhere: the address of the installation, device
addresses and their links, client addresses on a dual-stack socket, the guard
on RouterFleet's own networks, and services listening on both."""
import pathlib
import subprocess
import tempfile

from django.test import SimpleTestCase, TestCase

ROOT = pathlib.Path(__file__).resolve().parent.parent


class ServicesListenOnBothTests(SimpleTestCase):
    def test_gunicorn_and_uvicorn_take_ipv6_where_there_is_some(self):
        init = (ROOT / 'init.sh').read_text()
        self.assertIn("if grep -q . /proc/net/if_inet6 2>/dev/null; then LISTEN_HOST='::'; BIND='[::]:8001'", init)
        # /proc files report size 0: a size test would never see IPv6
        for path in ('init.sh', 'containers/nginx/rf_https.sh', 'containers/monitoring/listener.py'):
            text = (ROOT / path).read_text()
            self.assertNotIn('-s /proc/net/if_inet6', text, path)
            self.assertNotIn("getsize('/proc/net/if_inet6')", text, path)
        self.assertIn('--bind "$BIND"', init)
        self.assertIn('--host "$LISTEN_HOST"', init)

    def test_nginx_listens_on_ipv6_beside_ipv4(self):
        script = (ROOT / 'containers/nginx/rf_https.sh').read_text()
        rule = script.split("awk '", 1)[1].split("'", 1)[0]
        out = subprocess.run(['awk', rule], input='    listen 80;\n    listen 443 ssl;\n    server_name x;\n',
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, '    listen 80;\n    listen [::]:80;\n    listen 443 ssl;\n    listen [::]:443 ssl;\n    server_name x;\n')
        self.assertIn('with_ipv6 < "$CONF.raw" | with_access > "$CONF.new"', script)


class ServerAddressTests(SimpleTestCase):
    def host_of(self, value):
        script = (ROOT / 'entrypoint.sh').read_text()
        case = script[script.index('case "$SERVER_ADDRESS" in'):script.index('esac', script.index('case "$SERVER_ADDRESS" in')) + 4]
        out = subprocess.run(['bash', '-c', case + '\necho "$SERVER_HOSTNAME|$SERVER_ADDRESS"'],
                             env={'SERVER_ADDRESS': value, 'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def test_every_form(self):
        self.assertEqual(self.host_of('rf.example.net:8443'), 'rf.example.net|rf.example.net:8443')
        self.assertEqual(self.host_of('192.0.2.5'), '192.0.2.5|192.0.2.5')
        self.assertEqual(self.host_of('2001:db8::5'), '[2001:db8::5]|[2001:db8::5]')
        self.assertEqual(self.host_of('[2001:db8::5]:8443'), '[2001:db8::5]|[2001:db8::5]:8443')


class AddressTests(TestCase):
    def test_links_put_an_ipv6_address_in_brackets(self):
        from router_manager import device_types
        from router_manager.templatetags.router_tags import url_host
        from routerfleet.testing import make_router
        self.assertEqual(url_host('2001:db8::1'), '[2001:db8::1]')
        self.assertEqual(url_host('192.0.2.1'), '192.0.2.1')
        router = make_router(name='v6r', address='2001:db8::9', router_type='routeros')
        self.assertIn(('Winbox', 'winbox://[2001:db8::9]'), device_types.links_for(router))

    def test_the_device_form_takes_ipv6_with_or_without_brackets(self):
        from router_manager.forms import RouterForm
        from routerfleet.testing import make_profile
        for typed in ('2001:DB8::9', '[2001:db8::9]'):
            form = RouterForm({'name': f'v6-{len(typed)}', 'address': typed, 'port': 22, 'username': 'rf',
                               'password': 'device-secret-1', 'router_type': 'routeros', 'backup_profile': make_profile().pk,
                               'monitoring': 'on', 'enabled': 'on', 'keep_unchanged': 'profile'})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data['address'], '2001:db8::9')

    def test_an_ipv4_client_on_a_dual_stack_socket(self):
        from django.test import RequestFactory
        from audit_log import context
        request = RequestFactory().get('/', REMOTE_ADDR='::ffff:192.0.2.7')
        self.assertEqual(context.client_address(request)[0], '192.0.2.7')
        request = RequestFactory().get('/', REMOTE_ADDR='2001:db8::7')
        self.assertEqual(context.client_address(request)[0], '2001:db8::7')


class OwnNetworksGuardTests(SimpleTestCase):
    """The guard on RouterFleet's own networks (console, and server-side
    fetches) knows the IPv6 networks of the container too."""

    def test_ipv6_on_link_networks(self):
        from console import network
        with tempfile.NamedTemporaryFile('w', suffix='route6', delete=False) as handle:
            # fd00:dead:beef::/64 on a link; a default route; a link-local one
            handle.write('fd00deadbeef00000000000000000000 40 00000000000000000000000000000000 00 00000000000000000000000000000000 00000100 00000001 00000000 00000001 eth0\n')
            handle.write('00000000000000000000000000000000 00 00000000000000000000000000000000 00 fd00deadbeef00000000000000000001 00000400 00000001 00000000 00000003 eth0\n')
            handle.write('fe800000000000000000000000000000 40 00000000000000000000000000000000 00 00000000000000000000000000000000 00000100 00000001 00000000 00000001 eth0\n')
        networks = network.attached_networks(route_table='/no/such/route', ipv6_route_table=handle.name)
        self.assertEqual([str(n) for n in networks], ['fd00:dead:beef::/64'])
