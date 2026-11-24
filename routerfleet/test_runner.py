"""The test runner (manage.py test only - never a running installation).

Passwords are hashed with a fast hasher in tests: the production hasher's
1.5 million rounds per password made most of the suite's time, and what the
tests check - who may sign in, lockouts, password changes - does not depend on
how strong the hash is. Installations keep Django's default (PBKDF2)."""
from django.test.runner import DiscoverRunner


class FastHashingRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        from django.conf import settings
        settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
        # No real connections from a test: the discovery page does not check reachability by itself
        settings.DISCOVERY_CHECK_ON_PAGE = False
        super().setup_test_environment(**kwargs)
