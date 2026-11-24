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
    return {'rf_page_size': size if size in DisplaySettings.PAGE_SIZES else 10, 'rf_is_admin': is_admin}
