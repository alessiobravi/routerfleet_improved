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


class SessionLengthMiddleware:
    """A sign-in lasts as long as its user chose in My Settings (24 hours by
    default), counted from the last use. Pages refreshing by themselves (the
    live parts, X-RF-Live) and the scheduler's calls are not use: a tab left
    open on the status page does not keep a sign-in alive. The expiry is renewed at most every
    RENEW_SECONDS, not written at every request."""

    RENEW_SECONDS = 300

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Cheap checks first: reading the user loads the session
        if request.headers.get('X-RF-Live') == '1' or request.path.startswith('/cron/'):
            return self.get_response(request)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            import time
            from user_manager.models import UserAcl
            hours = UserAcl.objects.filter(user=user).values_list('session_hours', flat=True).first() or 24
            session = request.session
            now = time.time()
            if session.get('rf_hours') != hours or now - session.get('rf_renewed', 0) > self.RENEW_SECONDS:
                session['rf_hours'], session['rf_renewed'] = hours, now
                session.set_expiry(hours * 3600)
        return self.get_response(request)


class NoAccessLevelMiddleware:
    """An account without an access level ("No access level": no UserAcl, or
    level 0 - an account made outside the User Manager) sees no page but its
    own settings and signing out. Superusers keep their access, as before.
    The levels themselves are checked by each page."""

    OPEN = ('/accounts/', '/user/settings/', '/static/', '/cron/', '/monitoring/', '/console/ws/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # The path first: the scheduler's and monitoring's calls never load a session
        if request.path.startswith(self.OPEN):
            return self.get_response(request)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and not user.is_superuser:
            from user_manager.models import UserAcl
            if not UserAcl.objects.filter(user=user, user_level__gte=10).exists():
                from django.shortcuts import render
                return render(request, 'access_denied.html', {'page_title': 'Access Denied'}, status=403)
        return self.get_response(request)
