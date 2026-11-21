from django.contrib.auth.models import User
from django.test import TestCase

from accounts.forms import CreateUserForm
from routerfleet.testing import make_user
from user_manager.models import UserAcl


class FirstUserTests(TestCase):
    def test_login_redirects_to_first_user_on_an_empty_install(self):
        self.assertRedirects(self.client.get('/accounts/login/'), '/accounts/create_first_user/',
                             fetch_redirect_response=False)

    def test_first_user_is_an_administrator(self):
        response = self.client.post('/accounts/create_first_user/',
                                    {'username': 'Admin', 'password': 'pw-1234567', 'password2': 'pw-1234567'})
        self.assertTemplateUsed(response, 'accounts/superuser_created.html')
        user = User.objects.get()
        self.assertEqual(user.username, 'admin')
        self.assertTrue(user.is_superuser)
        self.assertEqual(UserAcl.objects.get(user=user).user_level, 50)

    def test_first_user_page_disappears_once_there_is_a_user(self):
        make_user()
        self.assertEqual(self.client.get('/accounts/create_first_user/').status_code, 404)

    def test_form_rules(self):
        self.assertFalse(CreateUserForm({'username': 'a b', 'password': 'x', 'password2': 'x'}).is_valid())
        self.assertFalse(CreateUserForm({'username': 'a', 'password': 'x', 'password2': 'y'}).is_valid())

    def test_missing_username_is_a_form_error_not_a_crash(self):
        form = CreateUserForm({'password': 'x', 'password2': 'x'})
        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)


class LoginTests(TestCase):
    def setUp(self):
        self.user = make_user(10, username='bob', password='bob-password-1')

    def test_login_and_logout(self):
        response = self.client.post('/accounts/login/', {'username': 'bob', 'password': 'bob-password-1'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)
        self.client.get('/accounts/logout/')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_wrong_password(self):
        response = self.client.post('/accounts/login/', {'username': 'bob', 'password': 'nope'})
        self.assertContains(response, 'Invalid username or password')

    def test_empty_form(self):
        self.assertContains(self.client.post('/accounts/login/', {}), 'Both fields are required')
