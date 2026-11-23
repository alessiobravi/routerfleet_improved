"""Database maintenance: statistics, and giving back the space of deleted data.

Freeing duplicates empties large text columns; deleting old backups removes
rows. Neither shrinks the database by itself:

- PostgreSQL marks the space reusable when autovacuum passes (the bundled image
  has it on); VACUUM does it at once and refreshes the planner's statistics,
  without locking anyone out. Only VACUUM FULL gives the space back to the disk,
  and it locks each table while it rewrites it - offered, never automatic.
- SQLite keeps the file at its largest size until VACUUM rewrites it, which
  locks the database while it runs (seconds to a few minutes) and needs free
  disk space the size of the database.

ANALYZE alone - fresh statistics, so queries use the indexes well - is cheap
and runs after every backup clean-up.
"""
import logging
import os

from django.db import connection

logger = logging.getLogger(__name__)


def size():
    """The database's size in bytes, or None when it can not be read."""
    try:
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_database_size(current_database())')
                return cursor.fetchone()[0]
        if connection.vendor == 'sqlite':
            name = connection.settings_dict['NAME']
            return os.path.getsize(name) if name and os.path.exists(str(name)) else None
    except Exception as error:   # a size is information, never a failure
        logger.warning(f'Database size could not be read: {error}')
    return None


def analyze():
    """Fresh planner statistics; cheap, safe at any time."""
    with connection.cursor() as cursor:
        cursor.execute('ANALYZE')


def optimize(full=False):
    """VACUUM (and ANALYZE). full: PostgreSQL VACUUM FULL. Must run outside a
    transaction. Returns (bytes before, bytes after)."""
    before = size()
    connection.ensure_connection()
    if connection.in_atomic_block:
        raise RuntimeError('database maintenance can not run inside a transaction')
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute('VACUUM (FULL, ANALYZE)' if full else 'VACUUM (ANALYZE)')
        else:
            cursor.execute('VACUUM')
            cursor.execute('ANALYZE')
    return before, size()
