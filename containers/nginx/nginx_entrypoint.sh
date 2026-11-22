#!/bin/sh

CERT_DIR="/certificate"

if [ ! -f "$CERT_DIR/nginx.key" ] || [ ! -f "$CERT_DIR/nginx.pem" ]; then
    echo "Creating self signed certificate..."
    openssl req -x509 -newkey rsa:4096 -nodes -keyout "$CERT_DIR/nginx.key" -out "$CERT_DIR/nginx.pem" -days 3650 -subj "/CN=localhost"
else
    echo "Skipping self signed certificate creation, files already exist."
fi

# The configuration follows System settings (HTTPS), and HTTPS_REDIRECT_POLICY
# in .env until an administrator chooses something else there
/rf_https.sh apply
# Applies later changes and looks after Let's Encrypt
/rf_https.sh watch &


exec "$@"