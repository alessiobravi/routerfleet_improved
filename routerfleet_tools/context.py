"""Values every page needs."""


def display(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {'rf_page_size': 10}
    from .models import DisplaySettings
    try:
        size = DisplaySettings.objects.values_list('default_page_size', flat=True).first()
    except Exception:  # a page shown before the migrations ran (first start)
        size = None
    try:
        from user_manager.models import UserAcl
        is_admin = UserAcl.objects.filter(user=request.user, user_level__gte=50).exists()
    except Exception:  # before the migrations ran
        is_admin = False
    # System settings are for administrators only: the menu shows them only to them
    return {'rf_page_size': size if size in DisplaySettings.PAGE_SIZES else 10, 'rf_is_admin': is_admin,
            'rf_system_warnings': system_warnings()}


def system_warnings():
    """The installation's own problems for the status bar (routerfleet_tools/
    health.py): a stopped scheduler, a filling disk. Kept a minute per worker,
    so a page costs one cache read."""
    from django.core.cache import cache
    found = cache.get('rf-system-warnings')
    if found is None:
        try:
            from . import health
            found = health.warnings()
        except Exception:  # before the migrations ran
            found = []
        cache.set('rf-system-warnings', found, 60)
    return found
