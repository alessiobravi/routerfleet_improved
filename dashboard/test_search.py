"""One search for everything: it finds devices, groups, profiles, commands,
keys, users and pages - only what the user may open."""
from django.test import TestCase

from routerfleet.testing import make_router, make_user


class SearchTests(TestCase):
    def setUp(self):
        from router_manager.models import RouterGroup
        make_router(name='core-gw-01', address='192.0.2.10')
        make_router(name='branch-ap', address='192.0.2.77')
        RouterGroup.objects.create(name='Core sites')

    def search(self, query, level=10):
        self.client.force_login(make_user(level))
        return {group['title']: group['items'] for group in self.client.get('/search/', {'q': query}).json()['groups']}

    def test_devices_by_name_or_address(self):
        found = self.search('core')
        self.assertEqual([item['label'] for item in found['Devices']], ['core-gw-01'])
        self.assertTrue(found['Devices'][0]['href'].startswith('/router/details/?uuid='))
        self.assertEqual([item['label'] for item in found['Device groups']], ['Core sites'])
        self.assertEqual([item['label'] for item in self.search('192.0.2.77')['Devices']], ['branch-ap'])

    def test_too_short_or_nothing(self):
        self.assertEqual(self.search('c'), {})
        self.assertEqual(self.search('zzzz-nothing'), {})

    def test_pages_by_their_words_and_level(self):
        self.assertIn('HTTPS', [item['label'] for item in self.search('certificate', level=50).get('Pages', [])])
        self.assertNotIn('Pages', self.search('certificate', level=10), 'settings pages are for administrators')
        self.assertIn('Jobs', [item['label'] for item in self.search('jobs', level=20)['Pages']])
        self.assertNotIn('Pages', self.search('jobs', level=10))

    def test_commands_and_users_only_for_their_levels(self):
        from fleet_commander.models import Command
        Command.objects.create(name='core backup now')
        self.assertNotIn('Commands', self.search('core backup', level=10))
        self.assertIn('Commands', self.search('core backup', level=20))
        make_user(10, username='core-operator')
        self.assertNotIn('Users', self.search('core-operator', level=40))
        self.assertEqual([item['label'] for item in self.search('core-operator', level=50)['Users']], ['core-operator'])

    def test_signed_out_is_sent_to_sign_in(self):
        response = self.client.get('/search/', {'q': 'core'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/accounts/login/'))

    def test_every_page_offers_it(self):
        self.client.force_login(make_user(10))
        page = self.client.get('/router/list/').content.decode()
        self.assertIn('id="rf-search-open"', page)
        self.assertIn('routerfleet/search.js', page)
