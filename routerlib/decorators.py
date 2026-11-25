import hmac
from django.conf import settings
from functools import wraps

from django.http import JsonResponse


def cron_endpoint(view):
    """Marks a view as one of the endpoints the scheduler calls.

    These endpoints carry no authentication on purpose: the cron container calls
    them from inside the network, on a loop, and there is no account behind that
    call. That makes them worth protecting by other means, because whoever
    reaches them makes the installation work - the command endpoints send
    payloads to the devices, one sends messages, one removes old backups.

    The shipped nginx does not pass /cron/ on, and this is the second lock
    behind it: nginx writes the address of the caller into X-Real-IP on the way
    in, while the cron container talks to the application directly, so a request
    that carries that header came from outside and is refused. It is not an
    authentication on its own.

    The authentication: with the compose files from 0.38 the scheduler holds a
    key of its own (CRON_KEY, on the service_keys volume) and sends it with
    every call (X-RF-Cron-Key); then a call without it is refused - so no
    process on the host, and no other container, can make the installation
    work through the application's port. Without that volume (an older compose
    file) CRON_KEY is not set and the calls are taken as before.
    """
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.META.get('HTTP_X_REAL_IP'):
            # Answered the way a path that does not exist is answered, so the
            # reply says nothing about what is there
            return JsonResponse({'error': 'Not found'}, status=404)
        expected = getattr(settings, 'CRON_KEY', '') or ''
        if expected and not hmac.compare_digest(request.META.get('HTTP_X_RF_CRON_KEY', '').encode(),
                                                expected.encode()):
            return JsonResponse({'error': 'Not found'}, status=404)
        return view(request, *args, **kwargs)

    return wrapper
