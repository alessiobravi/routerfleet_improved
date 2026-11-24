"""Makes the space of deleted data reusable and refreshes the planner statistics;
--full (PostgreSQL) also gives it back to the disk.

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
        from django.db import connection
        before, after = database.optimize(full=options['full'])
        self.stdout.write('Database optimized: ' + database.outcome(before, after, options['full'], connection.vendor))
