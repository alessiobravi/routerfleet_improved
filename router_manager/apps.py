from django.apps import AppConfig


class RouterManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'router_manager'

    def ready(self):
        from django.db.models.signals import post_delete, post_save
        from . import device_types
        from .models import DeviceType
        post_save.connect(device_types.clear_cache, sender=DeviceType, dispatch_uid='device_type_cache_save')
        post_delete.connect(device_types.clear_cache, sender=DeviceType, dispatch_uid='device_type_cache_delete')
