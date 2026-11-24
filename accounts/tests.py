from django.contrib.auth.models import User
from unittest import mock

from django.test import TestCase, override_settings

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
        self.client.post('/accounts/logout/')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_wrong_password(self):
        response = self.client.post('/accounts/login/', {'username': 'bob', 'password': 'nope'})
        self.assertContains(response, 'Invalid username or password')

    def test_empty_form(self):
        self.assertContains(self.client.post('/accounts/login/', {}), 'Both fields are required')


class LockoutTests(TestCase):
    """Five refused attempts within 15 minutes, for an account or from an address,
    and further attempts are turned away without the password being checked."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_user('alice', password='right-password')
        UserAcl.objects.create(user=self.user, user_level=50)

    def attempt(self, password, username='alice', ip='10.0.0.1'):
        return self.client.post('/accounts/login/', {'username': username, 'password': password},
                                REMOTE_ADDR=ip)

    def signed_in(self):
        return '_auth_user_id' in self.client.session

    def test_the_sixth_attempt_is_turned_away_even_with_the_right_password(self):
        for _ in range(5):
            self.attempt('wrong')
        response = self.attempt('right-password')
        self.assertContains(response, 'Too many failed sign')
        self.assertFalse(self.signed_in())
        from audit_log.models import LoginRecord
        self.assertEqual(LoginRecord.objects.filter(actor_kind='locked-out').count(), 1)

    def test_four_failures_then_success_starts_the_count_again(self):
        for _ in range(4):
            self.attempt('wrong')
        self.attempt('right-password')
        self.assertTrue(self.signed_in())
        self.client.logout()
        for _ in range(4):
            self.attempt('wrong')
        self.attempt('right-password')
        self.assertTrue(self.signed_in())

    def test_failing_on_purpose_does_not_lock_the_owner_out(self):
        for _ in range(5):
            self.attempt('wrong', ip='10.6.6.6')
        self.attempt('right-password', ip='10.6.6.6')
        self.assertFalse(self.signed_in(), 'the failing address is locked')
        self.attempt('right-password', ip='10.0.0.1')
        self.assertTrue(self.signed_in(), 'the owner, from elsewhere, is not')

    def test_guessing_spread_over_many_addresses_locks_the_account(self):
        for n in range(20):
            self.attempt('wrong', ip=f'10.1.{n // 4}.{n % 4 + 1}')   # 4 per address: none locked
        self.attempt('right-password', ip='10.9.9.9')
        self.assertFalse(self.signed_in())

    def test_below_the_account_limit_the_account_stays_open(self):
        for n in range(19):
            self.attempt('wrong', ip=f'10.1.{n // 4}.{n % 4 + 1}')
        self.attempt('right-password', ip='10.9.9.9')
        self.assertTrue(self.signed_in())

    def test_an_address_trying_many_names_is_locked(self):
        for n in range(5):
            self.attempt('wrong', username=f'guess{n}')
        self.attempt('right-password')
        self.assertFalse(self.signed_in())
        self.attempt('right-password', ip='10.0.0.2')
        self.assertTrue(self.signed_in(), 'another address is not affected')

    def test_the_lock_ends_after_the_window(self):
        from audit_log.models import LoginRecord
        from django.utils import timezone
        for _ in range(5):
            self.attempt('wrong')
        # LoginRecord is append-only through the ORM's save(); move the clock instead
        later = timezone.now() + timezone.timedelta(minutes=16)
        with mock.patch('accounts.lockout.timezone.now', return_value=later):
            self.attempt('right-password')
        self.assertTrue(self.signed_in())
        self.assertEqual(LoginRecord.objects.filter(actor_kind='locked-out').count(), 0)

    def test_attempts_while_locked_do_not_extend_the_lock(self):
        from django.utils import timezone
        for _ in range(5):
            self.attempt('wrong')
        later = timezone.now() + timezone.timedelta(minutes=10)
        with mock.patch('accounts.lockout.timezone.now', return_value=later):
            for _ in range(3):
                self.attempt('right-password')  # turned away, recorded, not counted
        self.assertFalse(self.signed_in())
        much_later = timezone.now() + timezone.timedelta(minutes=16)
        with mock.patch('accounts.lockout.timezone.now', return_value=much_later):
            self.attempt('right-password')
        self.assertTrue(self.signed_in())

    def test_unknown_and_known_accounts_get_the_same_answer(self):
        for _ in range(5):
            self.attempt('wrong', username='nobody', ip='10.0.0.7')
            self.attempt('wrong', username='alice', ip='10.0.0.8')
        a = self.attempt('x', username='nobody', ip='10.0.0.7').content
        b = self.attempt('x', username='alice', ip='10.0.0.8').content
        self.assertIn(b'Too many failed sign', a)
        self.assertIn(b'Too many failed sign', b)

    @override_settings(LOGIN_LOCKOUT_ATTEMPTS=0)
    def test_zero_turns_it_off(self):
        for _ in range(10):
            self.attempt('wrong')
        self.attempt('right-password')
        self.assertTrue(self.signed_in())

    def test_history_shows_the_lock(self):
        for _ in range(6):
            self.attempt('wrong')
        self.attempt('right-password', ip='10.0.0.2')  # alice is locked: still turned away
        from django.contrib.auth.models import User
        admin = User.objects.create_user('admin', password='x')
        UserAcl.objects.create(user=admin, user_level=50)
        self.client.force_login(admin)
        self.assertContains(self.client.get('/audit_log/login_history/'), 'Turned away: too many failed attempts')
