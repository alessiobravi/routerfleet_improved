"""HTTPS from the command line - the way back when a change made the web
interface unreachable:

    docker compose exec routerfleet python manage.py https status
    docker compose exec routerfleet python manage.py https reset
    docker compose exec routerfleet python manage.py https mode selfsigned
"""
import json

from django.core.management.base import BaseCommand, CommandError

from audit_log.bulk import collect
from routerfleet_tools import https
from routerfleet_tools.models import HttpsSettings

CLI_MODES = ('env', 'selfsigned', 'plain')


class Command(BaseCommand):
    help = 'Show or reset how the installation serves HTTPS.'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        sub.add_parser('status', help='What is configured, and what nginx applied')
        sub.add_parser('reset', help='Back to HTTPS_REDIRECT_POLICY in .env, as before System settings')
        mode = sub.add_parser('mode', help='Set a mode that needs no further input')
        mode.add_argument('mode', choices=CLI_MODES)

    def handle(self, *args, **options):
        current = HttpsSettings.load()
        if options['action'] == 'status':
            self.stdout.write(json.dumps({'configured': current.mode, 'domain': current.domain,
                                          'generation': current.generation, 'nginx': https.read_status()},
                                         indent=2, default=str))
            return
        mode = 'env' if options['action'] == 'reset' else options['mode']
        with collect(f'HTTPS mode set from the command line: {mode}'):
            current.mode = mode
            current.save()
        try:
            https.write_state(current)
        except OSError as error:
            raise CommandError(f'Saved, but the certificate volume could not be written: {error}') from None
        self.stdout.write(self.style.SUCCESS(f'HTTPS mode is now "{mode}". nginx applies it within a few seconds.'))
