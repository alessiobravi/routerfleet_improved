from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import TestCase

from routerfleet.testing import make_user
from user_manager.models import UserAcl


class UserManagerTests(TestCase):
    def setUp(self):
        self.admin = make_user(50)
        self.client.force_login(self.admin)

    def test_add_user(self):
        response = self.client.post('/user/manage/', {'username': 'carol', 'password1': 'Xk2!rT9#pq',
                                                      'password2': 'Xk2!rT9#pq', 'user_level': 30})
        self.assertRedirects(response, '/user/list/', fetch_redirect_response=False)
        self.assertEqual(UserAcl.objects.get(user__username='carol').user_level, 30)

    def test_change_level_without_touching_the_password(self):
        user = make_user(10, password='Original-pw-1')
        acl = UserAcl.objects.get(user=user)
        self.client.post(f'/user/manage/?uuid={acl.uuid}', {'username': user.username, 'user_level': 40})
        acl.refresh_from_db()
        self.assertEqual(acl.user_level, 40)
        user.refresh_from_db()
        self.assertTrue(user.check_password('Original-pw-1'))

    def test_an_email_added_by_an_administrator_is_stored(self):
        user = make_user(10, password='Original-pw-1')
        acl = UserAcl.objects.get(user=user)
        response = self.client.post(f'/user/manage/?uuid={acl.uuid}', {'username': user.username, 'user_level': 10,
                                                                       'email': 'ops@example.com'})
        self.assertRedirects(response, '/user/list/', fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertEqual(user.email, 'ops@example.com')
        self.assertTrue(user.check_password('Original-pw-1'), 'the password is untouched')
        self.assertContains(self.client.get(f'/user/manage/?uuid={acl.uuid}'), 'ops@example.com')

    def test_new_password_signs_the_user_out_everywhere(self):
        user = make_user(10, password='Original-pw-1')
        other = self.client_class()
        other.force_login(user)
        self.assertEqual(Session.objects.count(), 2)
        acl = UserAcl.objects.get(user=user)
        self.client.post(f'/user/manage/?uuid={acl.uuid}', {'username': user.username, 'user_level': 10,
                                                            'password1': 'Brand-new-pw-2',
                                                            'password2': 'Brand-new-pw-2'})
        remaining = [s.get_decoded().get('_auth_user_id') for s in Session.objects.all()]
        self.assertNotIn(str(user.pk), remaining)

    def test_duplicate_username(self):
        make_user(username='taken')
        response = self.client.post('/user/manage/', {'username': 'taken', 'password1': 'Xk2!rT9#pq',
                                                      'password2': 'Xk2!rT9#pq', 'user_level': 10})
        self.assertEqual(User.objects.filter(username='taken').count(), 1)
        self.assertEqual(response.status_code, 200)

    def test_delete_needs_the_username_as_confirmation(self):
        user = make_user(10, username='dave')
        acl = UserAcl.objects.get(user=user)
        self.client.post(f'/user/manage/?uuid={acl.uuid}', {'action': 'delete', 'confirmation': 'wrong'})
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.client.post(f'/user/manage/?uuid={acl.uuid}', {'action': 'delete', 'confirmation': 'dave'})
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_list(self):
        self.assertEqual(self.client.get('/user/list/').status_code, 200)


class SelfServiceSettingsTests(TestCase):
    def test_any_user_can_set_their_address_link(self):
        user = make_user(None)
        self.client.force_login(user)
        self.client.post('/user/settings/', {'address_link': 'both'})
        self.assertEqual(UserAcl.objects.get(user=user).address_link, 'both')

    def test_address_link_reaches_the_router_list(self):
        user = make_user(10)
        UserAcl.objects.filter(user=user).update(address_link='ssh')
        self.client.force_login(user)
        self.assertEqual(self.client.get('/router/list/').context['address_link'], 'ssh')


class UserDetailsTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(50))

    def test_name_surname_and_phone_are_stored_and_shown(self):
        response = self.client.post('/user/manage/', {'username': 'erin', 'password1': 'Xk2!rT9#pq', 'password2': 'Xk2!rT9#pq',
                                                      'user_level': 20, 'first_name': 'Erin', 'last_name': 'Rossi',
                                                      'phone': '+39 055 123 4567', 'email': 'erin@example.com'})
        self.assertRedirects(response, '/user/list/', fetch_redirect_response=False)
        acl = UserAcl.objects.get(user__username='erin')
        self.assertEqual((acl.user.first_name, acl.user.last_name, acl.phone), ('Erin', 'Rossi', '+39 055 123 4567'))
        page = self.client.get('/user/list/')
        self.assertContains(page, 'Erin Rossi')
        self.assertContains(page, '+39 055 123 4567')
        edit = self.client.get(f'/user/manage/?uuid={acl.uuid}')
        self.assertContains(edit, 'value="+39 055 123 4567"')
        # Editing keeps the password and changes the phone
        self.client.post(f'/user/manage/?uuid={acl.uuid}', {'username': 'erin', 'user_level': 20, 'first_name': 'Erin',
                                                            'last_name': 'Rossi', 'phone': '055 999'})
        acl.refresh_from_db()
        self.assertEqual(acl.phone, '055 999')
        self.assertTrue(acl.user.check_password('Xk2!rT9#pq'))

    def test_a_phone_that_is_not_one_is_refused(self):
        for phone in ('call me', '12', '+39<script>'):
            with self.subTest(phone=phone):
                self.client.post('/user/manage/', {'username': f'u{len(phone)}', 'password1': 'Xk2!rT9#pq',
                                                   'password2': 'Xk2!rT9#pq', 'user_level': 10, 'phone': phone})
                self.assertFalse(UserAcl.objects.filter(user__username=f'u{len(phone)}').exists())
