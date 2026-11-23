"""Gives back the space of deleted data and refreshes the planner statistics.

    python manage.py optimize_database          VACUUM + ANALYZE
    python manage.py optimize_database --full   PostgreSQL: VACUUM FULL (locks each table while it runs)
"""
from django.core.management.base import BaseCommand

from routerfleet_tools import database


class Command(BaseCommand):
    help = 'VACUUM and ANALYZE the database; --full rewrites PostgreSQL tables to give space back to the disk.'

    def add_arguments(self, parser):
        parser.add_argument('--full', action='store_true', help='PostgreSQL VACUUM FULL: locks each table while it runs')

    def handle(self, *args, **options):
        before, after = database.optimize(full=options['full'])

        def mb(value):
            return 'unknown' if value is None else f'{value / 1048576:.1f} MB'
        self.stdout.write(f'Database optimized: {mb(before)} before, {mb(after)} after.')
