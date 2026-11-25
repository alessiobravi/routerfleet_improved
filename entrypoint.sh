#!/bin/bash
PRODUCTION_SETTINGS_FILE="/app/routerfleet/production_settings.py"

set -e

# A value from the environment as a Python string literal: a quote or a
# backslash in a password can neither break the settings nor run as code
pystr() { python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"; }

# 09a: PostgreSQL 18 (0.24); 08a: the compose files before it, still valid -
# their PostgreSQL 16 image keeps being published until they move
if [[ "$COMPOSE_VERSION" != "09a" && "$COMPOSE_VERSION" != "08a" ]]; then
    echo "ERROR: Please upgrade your docker compose file. Exiting."
    exit 1
fi

if [ -z "$SERVER_ADDRESS" ]; then
    echo "SERVER_ADDRESS environment variable is not set. Exiting."
    exit 1
fi

if [[ "${DATABASE_ENGINE,,}" == "sqlite" ]]; then
    if [[ "$COMPOSE_TYPE" != "no-postgres" ]]; then
        echo "ERROR: Please use 'docker-compose-no-postgres.yml' when using sqlite as DATABASE_ENGINE. Exiting."
        exit 1
    fi
    DATABASES_CONFIG="DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': '/var/lib/routerfleet_sqlite/routerfleet-db.sqlite3',
            # A write waits for another to finish instead of failing at once
            # with database-is-locked: five workers, the scheduler and monitoring
            'OPTIONS': {'timeout': 20},
        }
    }"
elif [[ "${DATABASE_ENGINE,,}" == "postgres" ]]; then
    if [ -n "$POSTGRES_HOST" ]; then
      if [[ "$COMPOSE_TYPE" != "no-postgres" ]]; then
          echo "ERROR: When using a remote PostgreSQL server, please use 'docker-compose-no-postgres.yml'. Exiting."
          exit 1
      fi
      if [ -z "$POSTGRES_PORT" ]; then
          POSTGRES_PORT="5432"
      fi
    else
        if [[ "$COMPOSE_TYPE" != "with-postgres" ]]; then
            echo "ERROR: Local postgres is selected. Please use 'docker-compose.yml'. Exiting."
            exit 1
        fi
        POSTGRES_HOST="routerfleet-postgres"
        POSTGRES_PORT="5432"
    fi

    if [ -z "$POSTGRES_DB" ] || [ -z "$POSTGRES_USER" ] || [ -z "$POSTGRES_PASSWORD" ]; then
        echo "POSTGRES_DB, POSTGRES_USER, or POSTGRES_PASSWORD environment variable is not set. Exiting."
        exit 1
    fi
    DATABASES_CONFIG="DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': $(pystr "$POSTGRES_DB"),
            'USER': $(pystr "$POSTGRES_USER"),
            'PASSWORD': $(pystr "$POSTGRES_PASSWORD"),
            'HOST': $(pystr "$POSTGRES_HOST"),
            'PORT': $(pystr "$POSTGRES_PORT"),
        }
    }"
else
    echo "Unsupported DATABASE_ENGINE. Exiting."
    exit 1
fi

DEBUG_VALUE="False"
if [[ "${DEBUG_MODE,,}" == "true" ]]; then
    DEBUG_VALUE="True"
fi

# The host of SERVER_ADDRESS, as browsers send it: a name or IPv4 address with
# an optional :port, or an IPv6 address - in brackets with a port
# ([2001:db8::5]:8443), or bare (2001:db8::5), which is put in brackets
case "$SERVER_ADDRESS" in
    \[*\]*) SERVER_HOSTNAME="${SERVER_ADDRESS%%]*}]" ;;
    *:*:*)  SERVER_ADDRESS="[$SERVER_ADDRESS]"; SERVER_HOSTNAME="$SERVER_ADDRESS" ;;
    *)      SERVER_HOSTNAME="${SERVER_ADDRESS%%:*}" ;;
esac

cat > $PRODUCTION_SETTINGS_FILE <<EOL
DEBUG = $DEBUG_VALUE
# localhost and 127.0.0.1 are the machine the application itself runs on, and a
# browser keeps them apart: an installation reached as https://localhost and the
# same one reached as https://127.0.0.1 are two different hosts to it. Both are
# always allowed, and the address the installation is used at comes on top -
# otherwise only the one written in SERVER_ADDRESS answers and the other one is
# refused with 400, which reads like the application being down.
ALLOWED_HOSTS = ['routerfleet', 'localhost', '127.0.0.1', '[::1]', $(pystr "$SERVER_HOSTNAME")]
CSRF_TRUSTED_ORIGINS = ['http://routerfleet', $(pystr "https://$SERVER_ADDRESS"), $(pystr "http://$SERVER_ADDRESS"), 'https://localhost', 'http://localhost', 'https://127.0.0.1', 'http://127.0.0.1', 'https://[::1]', 'http://[::1]']
SECRET_KEY = '$(openssl rand -base64 32)'
$DATABASES_CONFIG
EOL

if [ -n "$TZ" ]; then
    echo "TIME_ZONE = $(pystr "$TZ")" >> $PRODUCTION_SETTINGS_FILE
fi

# The session and CSRF cookies only travel over HTTPS when the installation
# enforces HTTPS - which it does unless HTTPS_REDIRECT_POLICY is 'never', where
# sign-in over plain HTTP has to keep working. A compose file from before this
# setting reached the application does not pass it at all; for those nothing
# changes.
# The update check is on unless UPDATE_CHECK says off (off, false, no or 0)
if [[ "${UPDATE_CHECK,,}" =~ ^(off|false|no|0)$ ]]; then
    echo "UPDATE_CHECK = False" >> $PRODUCTION_SETTINGS_FILE
fi

if [ "${HTTPS_REDIRECT_POLICY+set}" = "set" ] && [[ "${HTTPS_REDIRECT_POLICY,,}" != "never" ]]; then
    echo "SESSION_COOKIE_SECURE = True" >> $PRODUCTION_SETTINGS_FILE
    echo "CSRF_COOKIE_SECURE = True" >> $PRODUCTION_SETTINGS_FILE
fi

# A key file is written whole or not at all: generated into a temporary file,
# checked, then moved into place. An empty file - a start that failed halfway,
# before this was so - is replaced: with it the application fell back to a key
# that changed at every start, so nothing saved with it was readable after a
# restart anyway; from now on what is saved stays readable.
make_key() {  # $1 file, $2 command printing a new key
    if [ -s "$1" ]; then return 0; fi
    if [ -e "$1" ]; then
        echo "WARNING: $1 was empty - creating it now. Device passwords saved before this start may"
        echo "need to be entered again (they were stored with a key that changed at every start)."
    fi
    sh -c "$2" > "$1.tmp"
    [ -s "$1.tmp" ] || { echo "ERROR: could not create $1"; rm -f "$1.tmp"; exit 1; }
    chmod 600 "$1.tmp"
    mv "$1.tmp" "$1"
}

make_key /app_secrets/monitoring_key "cat /proc/sys/kernel/random/uuid"
echo "MONITORING_KEY = $(pystr "$(cat /app_secrets/monitoring_key)")" >> $PRODUCTION_SETTINGS_FILE

make_key /app_secrets/encryption_key "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
echo "ENCRYPTION_KEY = $(pystr "$(cat /app_secrets/encryption_key)")" >> $PRODUCTION_SETTINGS_FILE

# The keys other containers need - and only those - on a volume of their own
# (compose files from 0.38): monitoring reads the monitoring key there instead
# of the whole secrets volume, which holds the key to every device password;
# the scheduler sends its key with every call, and only then is it required.
# A compose file without this volume keeps working exactly as before.
# Compose files from 0.40 give each its own volume - monitoring_keys holds the
# monitoring key alone, cron_keys the scheduler's alone - so neither container
# can act as the other; 0.38/0.39's shared service_keys volume still works.
copy_key() {  # $1 source file, $2 target folder
    if [ ! -s "$2/$(basename "$1")" ] || ! cmp -s "$1" "$2/$(basename "$1")"; then
        cp "$1" "$2/.$(basename "$1").tmp" && chmod 600 "$2/.$(basename "$1").tmp" \
            && mv "$2/.$(basename "$1").tmp" "$2/$(basename "$1")"
    fi
}
for dir in /service_keys /monitoring_keys; do
    [ -d "$dir" ] && copy_key /app_secrets/monitoring_key "$dir"
done
if [ -d /service_keys ] || [ -d /cron_keys ]; then
    make_key /app_secrets/cron_key "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
    # The same key wherever it is read (it was made in service_keys before 0.40)
    if [ -s /service_keys/cron_key ] && ! cmp -s /service_keys/cron_key /app_secrets/cron_key; then
        cp /service_keys/cron_key /app_secrets/cron_key
    fi
    for dir in /service_keys /cron_keys; do
        [ -d "$dir" ] && copy_key /app_secrets/cron_key "$dir"
    done
    echo "CRON_KEY = $(pystr "$(cat /app_secrets/cron_key)")" >> $PRODUCTION_SETTINGS_FILE
fi

# The Django admin is not served in production (a setting, the code untouched)
echo "DJANGO_ADMIN_ENABLED = False" >> $PRODUCTION_SETTINGS_FILE

# The application runs as the unprivileged user "routerfleet". Its settings
# hold the keys: readable by it, by nobody else. The folders it writes are
# handed over once (a later start finds them its own already); APP_USER=root
# in .env runs it as before, should anything need it.
APP_UID=$(id -u routerfleet 2>/dev/null || echo "")
if [ -n "$APP_UID" ] && [ "${APP_USER:-routerfleet}" != "root" ]; then
    chown root:routerfleet "$PRODUCTION_SETTINGS_FILE" && chmod 640 "$PRODUCTION_SETTINGS_FILE"
    for dir in /var/lib/routerfleet /var/lib/routerfleet_sqlite /app_static_files /certificate /discovery; do
        [ -d "$dir" ] || continue
        if [ "$(stat -c %u "$dir")" != "$APP_UID" ]; then
            echo "Handing $dir to the application user (once)"
            chown -R routerfleet:routerfleet "$dir"
        fi
    done
    # Shared with the monitoring container, whose root has every capability
    # dropped and so obeys file permissions like anyone: the application's
    # user owns it, root's group (the monitoring container) may write too,
    # nobody else
    if [ -d /discovery ]; then chown routerfleet:0 /discovery && chmod 0770 /discovery; fi
    export HOME=/home/routerfleet
    exec su-exec routerfleet "$@"
fi

exec "$@"
