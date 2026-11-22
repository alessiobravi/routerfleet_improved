"""ASGI entry point: the console's WebSocket (uvicorn, port 8002).

Every page is served by gunicorn (WSGI, port 8001); nginx sends only
/console/ws/ here. The HTTP part answers nothing but the WebSocket path.
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'routerfleet.settings')
django_asgi = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402 - after Django is set up
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.urls import path  # noqa: E402

from console.consumers import ConsoleConsumer  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi,
    # The consumer checks the origin itself (same host as the page), the
    # session cookie, and a one-time ticket
    'websocket': AuthMiddlewareStack(URLRouter([path('console/ws/', ConsoleConsumer.as_asgi())])),
})
