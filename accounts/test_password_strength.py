"""Every form that sets a password applies the configured password validators:
minimum length, not a common password, not entirely numeric, not too close to
the username."""
from django.contrib.auth.models import User
from django.test import TestCase

from accounts.forms import CreateUserForm
from routerfleet.testing import make_user
from user_manager.forms import UserAclForm
from user_manager.models import UserAcl

STRONG = 'quiet-harbour-7351'


class FirstAdministratorTests(TestCase):
    def form(self, password, username='admin'):
        return CreateUserForm({'username': username, 'password': password, 'password2': password})

    def test_weak_passwords_are_refused(self):
        for weak in ('1', '12345678901', 'password123', 'admin1234'):
            with self.subTest(password=weak):
                form = self.form(weak)
                self.assertFalse(form.is_valid())
                self.assertIn('password', form.errors)

    def test_a_strong_password_is_accepted(self):
        self.assertTrue(self.form(STRONG).is_valid())

    def test_the_page_refuses_a_weak_first_password(self):
        self.client.post('/accounts/create_first_user/', {'username': 'admin', 'password': '1', 'password2': '1'})
        self.assertFalse(User.objects.exists())


class UserManagerPasswordTests(TestCase):
    def test_a_new_user_needs_a_strong_password(self):
        form = UserAclForm({'username': 'newuser', 'password1': '1', 'password2': '1', 'user_level': 10})
        self.assertFalse(form.is_valid())
        self.assertTrue(UserAclForm({'username': 'newuser', 'password1': STRONG, 'password2': STRONG,
                                     'user_level': 10}).is_valid())

    def test_changing_a_password_needs_a_strong_one_and_leaving_it_empty_keeps_it(self):
        user = make_user(10, username='carol', password=STRONG)
        weak = UserAclForm({'username': 'carol', 'password1': '1', 'password2': '1', 'user_level': 10},
                           instance=user, user_id=user.id)
        self.assertFalse(weak.is_valid())
        unchanged = UserAclForm({'username': 'carol', 'password1': '', 'password2': '', 'user_level': 20},
                                instance=user, user_id=user.id)
        self.assertTrue(unchanged.is_valid(), unchanged.errors)
        unchanged.save()
        user.refresh_from_db()
        self.assertTrue(user.check_password(STRONG))
        self.assertEqual(UserAcl.objects.get(user=user).user_level, 20)
