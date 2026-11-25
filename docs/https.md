# HTTPS

How RouterFleet Improved serves HTTPS is chosen under **System settings** in
the menu (administrators, level 50). The page shows what is served now, what
nginx applied, the certificate's expiry, and whether **passkeys** can work.

## Modes

| Mode | What happens | Passkeys |
|---|---|---|
| **As configured in .env** | `HTTPS_REDIRECT_POLICY` decides, as before this page existed; the certificate already in the volume is served | if that certificate is trusted |
| **Let's Encrypt** | A certificate for your domain, obtained and renewed automatically | yes |
| **Certificate provided by an administrator** | You paste the certificate (with its chain) and its private key | if browsers trust it |
| **Self-signed, generated** | Made for the domain you enter (or the default one); browsers warn | no |
| **HTTPS off: behind a reverse proxy** | nginx serves plain HTTP for a proxy that serves HTTPS | if the proxy's certificate is trusted |
| **HTTPS off: plain HTTP** | No encryption at all | no |

**After an upgrade nothing changes:** the mode is *As configured in .env*, and
the certificate already deployed - self-signed, or one you placed in the volume
as `nginx.pem` - keeps being served. No mode ever writes over it; the CI stack
test checks its fingerprint before and after switching through the modes.

## What passkeys need

Browsers allow passkeys only when all three hold, and the page checks each:

- the site is **served over HTTPS**;
- it is reached by a **domain name** - never an IP address;
- its certificate is **trusted by browsers** (a public authority, not
  self-signed).

Before a change that would stop passkeys working, the review says how many
users sign in with one: they will need their password and second factor.

## HSTS

With a certificate browsers trust - an uploaded one, or Let's Encrypt's once
issued - nginx also sends `Strict-Transport-Security` (one year): browsers then
keep to HTTPS for that name, and refuse plain HTTP there. Never with the
self-signed certificate. Switching such an installation to plain HTTP later,
the review warns that browsers that used it will refuse the plain address for
up to a year.

## Changing the mode

1. Choose the mode and fill in what it needs.
2. **Review**: the page says what will change for people - passkeys, browser
   warnings, unencrypted passwords - and checks an uploaded certificate before
   anything happens (PEM format, key matching the certificate, not expired,
   issued for the domain, trusted by browsers or not).
3. **Apply**. Switching **HTTPS off** - either HTTPS-off mode - takes a red
   button and a sum to solve right below it, checked on the server, once.

Every change is recorded in the change log. nginx applies it within seconds:
it renders its configuration from fixed templates, tests it, and reloads
without dropping connections. A configuration nginx refuses is never used -
the working one stays, and the page shows why.

## Let's Encrypt

- Uses the **HTTP-01** challenge: Let's Encrypt connects to
  `http://<your domain>/.well-known/acme-challenge/` on **port 80**, so the
  server must be reachable from the internet under that name.
  With `HTTP_PORT` changed, forward port 80 from the internet to it
  ([Ports](installation.md#ports)).
- Until the certificate is issued, the current certificate keeps HTTPS up.
- A failed request is retried at most once an hour (Let's Encrypt limits
  failures); the reason is shown on the page.
- Renewal is checked daily and happens within 30 days of expiry.
- `LETSENCRYPT_STAGING=1` in `.env` uses Let's Encrypt's test service, for
  trying a setup without touching the real rate limits.

## Behind a reverse proxy

List the proxy's addresses. Only requests from them may say, through
`X-Forwarded-Proto`, that the browser used HTTPS, and through
`X-Forwarded-For`, who the browser is - which sign-in lockouts and the login
history rely on. nginx sets `X-Forwarded-Proto` itself in every mode and
overwrites what any other client sends. Make sure only the proxy can reach
RouterFleet's port 80.

The proxy must pass on the browser's `Host` header unchanged and let WebSockets
through for the console - what each common proxy needs is in
[Installation](installation.md#behind-your-own-reverse-proxy).

## The private key

An uploaded key is never stored in the database and never shown again. While a
change is being reviewed it waits on the certificate volume, readable only by
its owner, and is removed if the change is not applied within the hour. Once
applied it lives in `custom/privkey.pem` on that volume (mode 0600).

## If a change locks you out

On the server:

```bash
docker compose exec routerfleet python manage.py https status
docker compose exec routerfleet python manage.py https reset        # back to .env
docker compose exec routerfleet python manage.py https mode selfsigned
```

Or set `HTTPS_MODE_OVERRIDE=env` (or `selfsigned`) in `.env` and restart nginx
(`docker compose up -d`): the override wins over System settings until it is
removed.

Shut out by the HTTP access list or a ban rather than by HTTPS?
`docker compose exec routerfleet python manage.py http_access --reset` opens
access again.

## HTTP access

Which addresses may reach the site at all - allow and deny entries, a final
policy, and bans after failed sign-ins - is on its own page beside this one,
*System settings → Security → HTTP access*, and applies in every mode above.
See [HTTP access lists and banning](security.md#http-access-lists-and-banning).

## Compose files from before this page

The page needs the certificate volume shared with the application. A compose
file older than 0.16.0 mounts it only in nginx: add

```yaml
      - https_cert:/certificate
```

to the `volumes:` of the `routerfleet` service and run `docker compose up -d`.
The page explains this when the volume is missing, and HTTPS keeps following
`.env` meanwhile.
