FROM python:3.14-alpine

WORKDIR /app

# Every update the base image has a fix for is installed at build time: each
# release is rebuilt and scanned, so it ships with no known vulnerability whose
# fix exists. bash runs entrypoint.sh and init.sh, openssl generates the secret
# key, tzdata lets TIMEZONE name a zone.
RUN apk upgrade --no-cache \
    && apk add --no-cache bash openssl tzdata su-exec \
    && addgroup -S -g 1000 routerfleet \
    && adduser -S -D -u 1000 -G routerfleet -h /home/routerfleet -s /sbin/nologin routerfleet

COPY requirements.txt /app/
# pip has no use once the requirements are installed - and its own bundled
# copies of urllib3 and others are what a scanner reports in this layer
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall --yes pip

COPY . /app/

RUN chmod +x /app/init.sh /app/entrypoint.sh
# A checkout on Windows writes these two with CRLF unless it is told otherwise
# (see .gitattributes). Linux then looks for an interpreter called "/bin/sh\r"
# and refuses to run the file at all, so the carriage returns are taken out
# here as well: the image works whatever the checkout looks like.
RUN sed -i 's/\r$//' /app/init.sh /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["/app/init.sh"]
