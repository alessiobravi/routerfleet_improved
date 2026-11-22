"""Removes a user's authenticator app and passkeys, from the command line.

For the one case the web interface cannot help with: the only administrator
has lost the phone or the passkeys, and nobody is left to reset them.

    docker exec -it routerfleet python manage.py reset_mfa <username>
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from user_manager.models import UserPasskey, UserTotp


class Command(BaseCommand):
    help = "Remove a user's authenticator app and passkeys, so they sign in with their password only."

    def add_arguments(self, parser):
        parser.add_argument('username')

    def handle(self, *args, **options):
        user = User.objects.filter(username=options['username']).first()
        if user is None:
            raise CommandError(f'No user named "{options["username"]}".')
        passkeys = UserPasskey.objects.filter(user=user).delete()[0]
        totp = UserTotp.objects.filter(user=user).delete()[0]
        self.stdout.write(f'{user.username}: authenticator app {"removed" if totp else "was not set up"}, '
                          f'{passkeys} passkey(s) removed. Signing in now asks for the password only.')
