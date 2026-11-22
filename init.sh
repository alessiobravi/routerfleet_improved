#!/bin/bash
set -e

# Set up the environment
shopt -s nullglob

# Django startup
python manage.py migrate --noinput
python manage.py collectstatic --noinput
# The device console's WebSocket (routerfleet/asgi.py), behind nginx on
# /console/ws/. A browser that disappears without closing the connection
# (network lost, laptop asleep) is found by the ping and its session closed.
# Restarted if it stops; gunicorn below serves every page.
(
  while true; do
    uvicorn routerfleet.asgi:application --host 0.0.0.0 --port 8002 \
      --ws websockets --ws-ping-interval 20 --ws-ping-timeout 20 --ws-max-size 65536 \
      --proxy-headers --forwarded-allow-ips '*' --no-server-header --log-level warning
    echo "console server stopped; restarting in 2 seconds"
    sleep 2
  done
) &

exec gunicorn routerfleet.wsgi:application \
  --bind 0.0.0.0:8001 \
  --worker-class sync \
  --workers 5 \
  --timeout 300 \
  --graceful-timeout 30 \
  --max-requests 1000 \
  --max-requests-jitter 200 \
  --log-level info \
  --capture-output \
  --access-logfile - \
  --error-logfile -