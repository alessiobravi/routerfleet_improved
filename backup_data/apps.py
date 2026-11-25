from django.apps import AppConfig


class BackupDataConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'backup_data'

    def ready(self):
        from . import signals  # noqa: F401 - a backup's file goes with its row
