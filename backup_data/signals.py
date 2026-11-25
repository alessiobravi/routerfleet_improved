"""A backup's binary file goes with its row.

Django never deletes the file of a FileField: housekeeping's retention (a
queryset delete), deleting a device (its backups go with it) or any other
delete would leave the file in MEDIA_ROOT, slowly filling the volume. Every
delete of a RouterBackup sends post_delete - a queryset delete too, as soon as
a receiver listens - and the file is removed here.
"""
import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_delete, sender='backup_data.RouterBackup')
def remove_binary_file(sender, instance, **kwargs):
    stored = instance.backup_binary
    if not stored or not stored.name:
        return
    try:
        if stored.storage.exists(stored.name):
            stored.storage.delete(stored.name)
    except OSError:   # never a reason for the delete itself to fail
        logger.warning('The file of a deleted backup could not be removed.', exc_info=True)
