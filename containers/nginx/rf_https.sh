#!/bin/sh
# HTTPS for RouterFleet Improved: renders nginx's configuration from fixed
# templates, from the mode System settings wrote to the certificate volume.
#
#   rf_https.sh apply    render, test and (if nginx runs) reload - once
#   rf_https.sh watch    apply whenever the settings or certificates change;
#                        obtain and renew Let's Encrypt certificates
#   rf_https.sh render   print the configuration of the current mode
#
# The settings file is data. Every value is checked again here before it
# reaches the configuration, and a configuration nginx refuses is never kept:
# the working one stays, and the reason is written to the status file.

CERT_DIR=${RF_CERT_DIR:-/certificate}
STATE="$CERT_DIR/routerfleet-https.json"
STATUS="$CERT_DIR/routerfleet-https-status.json"
CONF=${RF_NGINX_CONF:-/etc/nginx/conf.d/default.conf}
TEMPLATES=${RF_TEMPLATES:-/etc/nginx}
LE_DIR="$CERT_DIR/letsencrypt"
ACME_ROOT="$CERT_DIR/acme"
LE_LIVE="$LE_DIR/live/routerfleet"
LE_ERROR=""

log() { echo "[rf_https] $*"; }

valid_domain() { printf '%s' "$1" | grep -Eq '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$'; }
valid_email() { printf '%s' "$1" | grep -Eq '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'; }
valid_net() { printf '%s' "$1" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$|^[0-9A-Fa-f:]+(/[0-9]{1,3})?$'; }

state() { [ -f "$STATE" ] && jq -r "$1 // empty" "$STATE" 2>/dev/null; }

# The HTTPS port the host publishes (HTTPS_PORT in .env, 443 by default): an
# HTTP request is redirected to it. Inside the container nginx always listens
# on 80 and 443. Prints ":8443", or nothing for 443 or a value that is not a port.
https_port_suffix() {
    port=${HTTPS_PORT:-443}
    if ! printf '%s' "$port" | grep -Eq '^[0-9]{1,5}$' || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        log "HTTPS_PORT is not a port: $port - redirecting to 443" >&2
        port=443
    fi
    [ "$port" = "443" ] || printf ':%s' "$port"
}

mode() {
    # The emergency override in .env wins over System settings
    case "$HTTPS_MODE_OVERRIDE" in
        env|selfsigned) echo "$HTTPS_MODE_OVERRIDE"; return ;;
    esac
    m=$(state .mode)
    case "$m" in
        env|letsencrypt|custom|selfsigned|proxy|plain) echo "$m" ;;
        *) echo env ;;
    esac
}

# The locations every server block serves; $1 is what X-Forwarded-Proto is set to
locations() {
    cat <<EOF
    location /static/ {
        root /;
        # Text compressed; only here - a page carrying a CSRF token is never
        # compressed (BREACH)
        gzip on;
        gzip_vary on;
        gzip_min_length 1024;
        gzip_types text/css application/javascript text/javascript image/svg+xml application/json text/plain;
        # Django sends these for the pages it renders; files served here
        # directly need them from nginx
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "same-origin" always;
        # A plain name may change at an update: asked again (304 when unchanged)
        add_header Cache-Control "no-cache" always;
        # A name with the hash of its content never changes: kept for a year
        location ~ "\.[0-9a-f]{12}\.[A-Za-z0-9]+\$" {
            add_header X-Content-Type-Options "nosniff" always;
            add_header Referrer-Policy "same-origin" always;
            add_header Cache-Control "public, max-age=31536000, immutable" always;
        }
    }

    # The scheduler calls the application directly on the internal network
    location /cron/ {
        return 404;
    }

    # The device console's WebSocket: served by uvicorn beside gunicorn
    location /console/ws/ {
        proxy_pass http://routerfleet:8002;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $1;
        proxy_read_timeout 4h;
        proxy_send_timeout 4h;
    }

    # Firmware uploads (System settings): the only place a large body is
    # accepted; every other page keeps nginx's 1 MB
    location = /system/firmware/upload/ {
        client_max_body_size 1024m;
        client_body_timeout 30m;
        proxy_request_buffering on;
        proxy_read_timeout 30m;
        proxy_send_timeout 30m;
        proxy_pass http://routerfleet:8001;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $1;
    }

    location / {
        proxy_pass http://routerfleet:8001;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        # Set here, never passed through from the client
        proxy_set_header X-Forwarded-Proto $1;
    }
EOF
}

tls_server() {  # $1 certificate, $2 key
    cat <<EOF
server {
    server_tokens off;
    listen 80;
    location /.well-known/acme-challenge/ {
        root $ACME_ROOT;
    }
    location / {
        return 301 https://\$host$(https_port_suffix)\$request_uri;
    }
}

server {
    server_tokens off;
    listen 443 ssl;
    ssl_certificate     $1;
    ssl_certificate_key $2;
    ssl_protocols       TLSv1.2 TLSv1.3;

$(locations https)
}
EOF
}

selfsigned_for() {  # a self-signed certificate for a domain, made once
    dir="$CERT_DIR/selfsigned"
    mkdir -p "$dir"
    if [ ! -f "$dir/$1.pem" ] || [ ! -f "$dir/$1.key" ]; then
        log "creating a self-signed certificate for $1"
        openssl req -x509 -newkey rsa:3072 -nodes -days 825 -subj "/CN=$1" \
            -addext "subjectAltName=DNS:$1" -keyout "$dir/$1.key" -out "$dir/$1.pem" 2>/dev/null || return 1
        chmod 600 "$dir/$1.key"
    fi
}

# IPv6 beside IPv4, where the container has it: every listen line gets its
# [::] twin (nginx would refuse to start on [::] without IPv6)
with_ipv6() {
    if [ -s /proc/net/if_inet6 ]; then
        awk '{ print } /^[ \t]*listen [0-9]+/ { line = $0; sub(/listen /, "listen [::]:", line); print line }'
    else
        cat
    fi
}

# The HTTP access list (System settings, HTTP access): nginx's allow and deny,
# checked in order - the first match decides - then the final policy, in every
# server block. Let's Encrypt's validation path stays open.
access_lines() {
    [ -f "$STATE" ] || return 0
    rules=$(jq -r '.access.rules[]? | "\(.action) \(.network)"' "$STATE" 2>/dev/null)
    default=$(jq -r '.access.default // "allow"' "$STATE" 2>/dev/null)
    [ -n "$rules" ] || [ "$default" = "deny" ] || return 0
    echo "$rules" | while read -r action net; do
        [ -n "$net" ] || continue
        case "$action" in allow|deny) ;; *) echo "access list: not an action: $action" >&2; return 1 ;; esac
        valid_net "$net" || { echo "access list: not an address: $net" >&2; return 1; }
        echo "    $action $net;"
    done
    case "$default" in deny) echo "    deny all;" ;; *) echo "    allow all;" ;; esac
}

with_access() {
    RF_ACCESS="$(access_lines)" || return 1
    if [ -z "$RF_ACCESS" ]; then cat; return 0; fi
    RF_ACCESS="$RF_ACCESS" awk '
        { print }
        /^[ \t]*server[ \t]*\{/ { print ENVIRON["RF_ACCESS"] }
        /location \/\.well-known\/acme-challenge\/[ \t]*\{/ { print "        allow all;" }
    '
}

render() {  # writes the configuration for the current mode to stdout
    m=$(mode)
    case "$m" in
        env)
            if [ "$HTTPS_REDIRECT_POLICY" = "never" ]; then
                cat "$TEMPLATES/virtualhost_noredirect.conf.disabled"
            else
                # The redirect goes to the published HTTPS port
                sed "s|https://\$host\$request_uri|https://\$host$(https_port_suffix)\$request_uri|" \
                    "$TEMPLATES/virtualhost.conf.disabled"
            fi
            ;;
        selfsigned)
            domain=$(state .domain)
            if [ -n "$domain" ] && valid_domain "$domain" && selfsigned_for "$domain"; then
                tls_server "$CERT_DIR/selfsigned/$domain.pem" "$CERT_DIR/selfsigned/$domain.key"
            else
                tls_server "$CERT_DIR/nginx.pem" "$CERT_DIR/nginx.key"
            fi
            ;;
        custom)
            [ -f "$CERT_DIR/custom/fullchain.pem" ] && [ -f "$CERT_DIR/custom/privkey.pem" ] || {
                echo "no uploaded certificate on the volume" >&2; return 1; }
            tls_server "$CERT_DIR/custom/fullchain.pem" "$CERT_DIR/custom/privkey.pem"
            ;;
        letsencrypt)
            # Until the certificate is issued, the self-signed one keeps HTTPS up
            if [ -f "$LE_LIVE/fullchain.pem" ] && [ -f "$LE_LIVE/privkey.pem" ]; then
                tls_server "$LE_LIVE/fullchain.pem" "$LE_LIVE/privkey.pem"
            else
                tls_server "$CERT_DIR/nginx.pem" "$CERT_DIR/nginx.key"
            fi
            ;;
        plain)
            cat <<EOF
server {
    server_tokens off;
    listen 80;
$(locations '$scheme')
}

server {
    server_tokens off;
    listen 443 ssl;
    ssl_certificate     $CERT_DIR/nginx.pem;
    ssl_certificate_key $CERT_DIR/nginx.key;
$(locations '$scheme')
}
EOF
            ;;
        proxy)
            nets=$(jq -r '.trusted_proxies[]? // empty' "$STATE" 2>/dev/null)
            [ -n "$nets" ] || { echo "no trusted proxy listed" >&2; return 1; }
            for net in $nets; do valid_net "$net" || { echo "not an address: $net" >&2; return 1; }; done
            echo "# Only the listed proxies may say the browser used HTTPS"
            echo 'geo $realip_remote_addr $rf_trusted_proxy {'
            echo '    default 0;'
            for net in $nets; do echo "    $net 1;"; done
            echo '}'
            echo 'map "$rf_trusted_proxy:$http_x_forwarded_proto" $rf_forwarded_proto {'
            echo '    default $scheme;'
            echo '    "1:https" https;'
            echo '}'
            echo
            echo 'server {'
            echo '    server_tokens off;'
            echo '    listen 80;'
            for net in $nets; do echo "    set_real_ip_from $net;"; done
            echo '    real_ip_header X-Forwarded-For;'
            echo '    real_ip_recursive on;'
            locations '$rf_forwarded_proto'
            echo '}'
            ;;
    esac
}

certificate_end() {  # the end date of the certificate the current mode serves
    case "$(mode)" in
        custom) f="$CERT_DIR/custom/fullchain.pem" ;;
        letsencrypt) f="$LE_LIVE/fullchain.pem" ;;
        selfsigned) d=$(state .domain); f="$CERT_DIR/selfsigned/$d.pem"; [ -n "$d" ] && [ -f "$f" ] || f="$CERT_DIR/nginx.pem" ;;
        *) f="$CERT_DIR/nginx.pem" ;;
    esac
    [ -f "$f" ] && openssl x509 -enddate -noout -in "$f" 2>/dev/null | sed 's/^notAfter=//'
}

write_status() {  # $1 applied (true/false), $2 error
    issued=false
    [ -f "$LE_LIVE/fullchain.pem" ] && issued=true
    jq -n --arg mode "$(mode)" --argjson generation "$(state .generation | grep -E '^[0-9]+$' || echo 0)" \
        --argjson applied "$1" --arg error "$2" --arg checked "$(date -u '+%Y-%m-%d %H:%M UTC')" \
        --arg not_after "$(certificate_end)" --argjson issued "$issued" --arg le_error "$LE_ERROR" \
        --arg override "${HTTPS_MODE_OVERRIDE:-}" \
        '{mode: $mode, generation: $generation, applied: $applied, error: $error, checked: $checked,
          certificate_not_after: $not_after, certificate_issued: $issued, letsencrypt_error: $le_error,
          override: $override}' > "$STATUS.tmp" && mv "$STATUS.tmp" "$STATUS"
}

apply() {
    if ! render > "$CONF.raw" 2> /tmp/rf_render_error; then
        rm -f "$CONF.raw"
        write_status false "$(head -c 300 /tmp/rf_render_error)"
        log "not applied: $(cat /tmp/rf_render_error)"
        return 1
    fi
    if ! with_ipv6 < "$CONF.raw" | with_access > "$CONF.new" 2> /tmp/rf_render_error || [ -s /tmp/rf_render_error ]; then
        rm -f "$CONF.raw" "$CONF.new"
        write_status false "$(head -c 300 /tmp/rf_render_error)"
        log "not applied: $(cat /tmp/rf_render_error)"
        return 1
    fi
    rm -f "$CONF.raw"
    [ -f "$CONF" ] && cp "$CONF" "$CONF.bak"
    mv "$CONF.new" "$CONF"
    if nginx -t 2> /tmp/rf_nginx_test; then
        # The watcher runs beside nginx, so it reloads it. At start-up (apply
        # before nginx starts) there is nothing to reload yet. pgrep is not
        # used to tell the two apart: it is not reliable in this image, and a
        # skipped reload looked applied while nginx kept the old configuration
        if [ "$RF_RELOAD" = "1" ]; then
            if ! nginx -s reload 2> /tmp/rf_nginx_reload; then
                write_status false "nginx did not reload: $(head -c 300 /tmp/rf_nginx_reload)"
                log "reload failed: $(cat /tmp/rf_nginx_reload)"
                return 1
            fi
        fi
        write_status true ""
        log "applied mode $(mode)"
    else
        # nginx refuses it: the working configuration stays
        [ -f "$CONF.bak" ] && mv "$CONF.bak" "$CONF"
        write_status false "$(grep -m1 -i 'emerg\|error' /tmp/rf_nginx_test | head -c 300)"
        log "refused by nginx, kept the previous configuration"
        return 1
    fi
}

letsencrypt() {  # issue or replace when needed; renew once a day
    [ "$(mode)" = "letsencrypt" ] || return 0
    domain=$(state .domain); email=$(state .email)
    valid_domain "$domain" && valid_email "$email" || { LE_ERROR="invalid domain or email in the settings"; return 0; }
    mkdir -p "$ACME_ROOT" "$LE_DIR"
    staging=""
    [ "$LETSENCRYPT_STAGING" = "1" ] && staging="--staging"
    common="--config-dir $LE_DIR --work-dir /tmp/le-work --logs-dir /tmp/le-logs --non-interactive"
    now=$(date +%s)
    have=$(cat "$LE_DIR/routerfleet-domain" 2>/dev/null)
    if [ "$have" != "$domain" ] || [ ! -f "$LE_LIVE/fullchain.pem" ]; then
        last=$(cat "$LE_DIR/routerfleet-attempt" 2>/dev/null || echo 0)
        # Let's Encrypt limits failed attempts: at most one an hour from here
        [ $((now - last)) -lt 3600 ] && return 0
        echo "$now" > "$LE_DIR/routerfleet-attempt"
        [ -n "$have" ] && certbot delete --cert-name routerfleet $common >/dev/null 2>&1
        log "requesting a Let's Encrypt certificate for $domain"
        if certbot certonly --webroot -w "$ACME_ROOT" --cert-name routerfleet -d "$domain" -m "$email" \
                --agree-tos --no-eff-email $staging $common > /tmp/le-out 2>&1; then
            echo "$domain" > "$LE_DIR/routerfleet-domain"
            LE_ERROR=""
            apply
        else
            LE_ERROR=$(grep -E -m2 'Detail:|Error|error' /tmp/le-out | tr '\n' ' ' | head -c 400)
            [ -n "$LE_ERROR" ] || LE_ERROR="certbot failed; see the nginx container's log"
            log "Let's Encrypt: $LE_ERROR"
            write_status true ""
        fi
        return 0
    fi
    last=$(cat "$LE_DIR/routerfleet-renew-check" 2>/dev/null || echo 0)
    if [ $((now - last)) -ge 86400 ]; then
        echo "$now" > "$LE_DIR/routerfleet-renew-check"
        # Renews only within 30 days of expiry; the hook reloads nginx
        if certbot renew --cert-name routerfleet $common --deploy-hook "touch /tmp/le-renewed" > /tmp/le-out 2>&1; then
            [ -f /tmp/le-renewed ] && { rm -f /tmp/le-renewed; log "certificate renewed"; apply; }
            LE_ERROR=""
        else
            LE_ERROR="renewal failed: $(grep -E -m1 'Detail:|Error' /tmp/le-out | head -c 300)"
            write_status true ""
        fi
    fi
}

signature() {
    for f in "$STATE" "$CERT_DIR/custom/fullchain.pem" "$CERT_DIR/custom/privkey.pem" "$LE_LIVE/fullchain.pem"; do
        [ -f "$f" ] && md5sum "$f" 2>/dev/null
    done
    echo "$HTTPS_MODE_OVERRIDE"
}

case "$1" in
    apply)
        apply || {
            # At start-up, a broken setting must not keep nginx down: .env mode
            log "falling back to HTTPS_REDIRECT_POLICY for this start"
            HTTPS_MODE_OVERRIDE=env apply
        }
        ;;
    watch)
        RF_RELOAD=1
        last=$(signature)
        while true; do
            sleep 5
            current=$(signature)
            if [ "$current" != "$last" ]; then
                last="$current"
                apply
            fi
            letsencrypt
            last=$(signature)
        done
        ;;
    render)
        # The configuration of the current mode, printed - nothing applied
        render
        ;;
    *)
        echo "usage: rf_https.sh apply|watch|render" >&2
        exit 2
        ;;
esac
