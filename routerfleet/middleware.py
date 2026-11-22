from django.conf import settings
from django.core.exceptions import ValidationError
from django.views.defaults import page_not_found


class MalformedIdentifierMiddleware:
    """A ?uuid= that is not a UUID is a page that does not exist.

    Views look objects up with get_object_or_404(Model, uuid=...), which answers
    404 for a UUID that matches nothing - but a value that is not a UUID at all
    makes the database layer raise a ValidationError first, and that surfaced as
    a server error. Only that one error is turned into 404: any other
    ValidationError reaching this point is a real fault and keeps its 500.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, ValidationError) and is_malformed_uuid(exception):
            return page_not_found(request, exception)
        return None


def is_malformed_uuid(error):
    """UUIDField.to_python raises code 'invalid' with the value in params."""
    return any(
        getattr(item, 'code', None) == 'invalid' and 'UUID' in str(item.message)
        for item in getattr(error, 'error_list', [error])
    )


class HttpsCookieMiddleware:
    """When HTTPS is managed in System settings, the session and CSRF cookies
    are Secure exactly when the request arrived over HTTPS - directly, or
    through a reverse proxy nginx trusts (nginx sets X-Forwarded-Proto itself
    and overwrites what a client sends). In .env mode nothing changes: the
    cookies follow HTTPS_REDIRECT_POLICY as before.

    Outermost, so it sees the cookies the inner middleware set."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        mode = https_mode()
        if mode != 'env':
            secure = request.is_secure()
            for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
                if name in response.cookies:
                    response.cookies[name]['secure'] = secure
        return response


_mode_cache = {'mode': 'env', 'at': 0.0}


def https_mode():
    """The HTTPS mode, read at most every 10 seconds per process."""
    import time
    now = time.monotonic()
    if now - _mode_cache['at'] > 10:
        from django.db import DatabaseError
        from routerfleet_tools.models import HttpsSettings
        try:
            row = HttpsSettings.objects.filter(name='https_settings').values_list('mode', flat=True).first()
            _mode_cache['mode'] = row or 'env'
        except DatabaseError:
            _mode_cache['mode'] = 'env'
        _mode_cache['at'] = now
    return _mode_cache['mode']


def forget_https_mode():
    _mode_cache['at'] = 0.0
