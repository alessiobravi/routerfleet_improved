from django.apps import AppConfig


class RouterfleetToolsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'routerfleet_tools'

    def ready(self):
        from . import bans  # noqa: F401 - bans after failed sign-ins
