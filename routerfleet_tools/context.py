"""Values every page needs."""


def display(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {'rf_page_size': 10}
    from .models import DisplaySettings
    try:
        size = DisplaySettings.objects.values_list('default_page_size', flat=True).first()
    except Exception:  # a page shown before the migrations ran (first start)
        size = None
    return {'rf_page_size': size if size in DisplaySettings.PAGE_SIZES else 10}
