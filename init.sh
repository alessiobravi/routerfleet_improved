#!/bin/bash
set -e

# Set up the environment
shopt -s nullglob

# Django startup
python manage.py migrate --noinput
python manage.py collectstatic --noinput
# IPv4 and IPv6: where the container has IPv6, one socket on [::] serves both
# (Linux accepts IPv4 on it, as ::ffff:a.b.c.d); without IPv6, 0.0.0.0
if [ -s /proc/net/if_inet6 ]; then LISTEN_HOST='::'; BIND='[::]:8001'; else LISTEN_HOST='0.0.0.0'; BIND='0.0.0.0:8001'; fi

# The device console's WebSocket (routerfleet/asgi.py), behind nginx on
# /console/ws/. A browser that disappears without closing the connection
# (network lost, laptop asleep) is found by the ping and its session closed.
# Restarted if it stops; gunicorn below serves every page.
(
  while true; do
    uvicorn routerfleet.asgi:application --host "$LISTEN_HOST" --port 8002 \
      --ws websockets --ws-ping-interval 20 --ws-ping-timeout 20 --ws-max-size 65536 \
      --proxy-headers --forwarded-allow-ips '*' --no-server-header --log-level warning
    echo "console server stopped; restarting in 2 seconds"
    sleep 2
  done
) &

# Threads: the scheduler's jobs (backups, router information, commands,
# firmware) each hold a request for up to a minute while they talk to devices;
# with one request per worker, a page clicked meanwhile waited behind them.
# The access log carries how long each request took (the last field, ms).
# SQLite takes one writer at a time: one thread per worker there, as before.
THREADS=4
if [[ "${DATABASE_ENGINE,,}" == "sqlite" ]]; then THREADS=1; fi
exec gunicorn routerfleet.wsgi:application \
  --bind "$BIND" \
  --worker-class gthread \
  --workers 5 \
  --threads "$THREADS" \
  --access-logformat '%(h)s %(t)s "%(r)s" %(s)s %(b)s %(M)sms' \
  --timeout 300 \
  --graceful-timeout 30 \
  --max-requests 1000 \
  --max-requests-jitter 200 \
  --log-level info \
  --capture-output \
  --access-logfile - \
  --error-logfile -