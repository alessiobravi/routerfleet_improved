"""The HTTP access list, from the host - for when the web interface shut you out.

    python manage.py http_access            shows the list
    python manage.py http_access --reset    removes every entry and every ban, Allow as the final policy, and applies it
"""
from django.core.management.base import BaseCommand

from routerfleet_tools import https
from routerfleet_tools.models import AccessRule, AddressBan, HttpsSettings


class Command(BaseCommand):
    help = 'Shows the HTTP access list; --reset opens access again (removes every entry, final policy Allow).'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='remove every entry and allow every address')

    def handle(self, *args, **options):
        settings_row = HttpsSettings.load()
        if options['reset']:
            AccessRule.objects.all().delete()
            AddressBan.objects.all().delete()
            settings_row.access_default = 'allow'
            settings_row.save(update_fields=['access_default', 'updated'])
            try:
                https.write_state(settings_row)
                self.stdout.write('HTTP access opened: no entry, no ban, every address allowed. nginx applies it within seconds.')
            except OSError:
                self.stdout.write('HTTP access list emptied; the certificate volume is not shared, so nginx keeps what it had.')
            return
        for number, rule in enumerate(AccessRule.objects.all(), start=1):
            self.stdout.write(f'{number}. {rule.action} {rule.network}' + (f'  ({rule.note})' if rule.note else ''))
        self.stdout.write(f'Finally: {settings_row.access_default} every other address.')
        from routerfleet_tools.bans import active
        for ban in active():
            self.stdout.write(f'Banned: {ban.address} until {ban.until:%Y-%m-%d %H:%M} UTC ({ban.failures} failed sign-ins)')
