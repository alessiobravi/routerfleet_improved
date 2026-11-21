# Installation — full specification

Everything about deploying RouterFleet Improved: what it creates, every option,
and every variant. For the three-command version, see the
[README](../README.md#quick-install).

- [Requirements](#requirements)
- [What gets created](#what-gets-created)
- [Images and versions](#images-and-versions)
- [Choosing a compose file](#choosing-a-compose-file)
- [Standard installation](#standard-installation)
- [Variant: SQLite](#variant-sqlite)
- [Variant: external PostgreSQL](#variant-external-postgresql)
- [Configuration reference](#configuration-reference)
- [Ports](#ports)
- [TLS certificates](#tls-certificates)
- [Behind your own reverse proxy](#behind-your-own-reverse-proxy)
- [Volumes and what to back up](#volumes-and-what-to-back-up)
- [Verifying the installation](#verifying-the-installation)
- [Building the images yourself](#building-the-images-yourself)
- [Uninstalling](#uninstalling)

> **No warranty — use at your own risk.** RouterFleet Improved is provided
> "as is", without warranty of any kind. It logs in to your network devices
> with administrative rights and acts on them; a command or update sent through
> it can interrupt service or leave a device unusable, and a backup can fail or
> be incomplete without notice. Any consequence of using it is yours. Read the
> full [disclaimer](../README.md#disclaimer) before installing.

## Requirements

| Requirement | Detail |
|---|---|
| Docker Engine with the Compose v2 plugin | `docker compose` (with a space). The legacy `docker-compose` binary is not supported |
| CPU architecture | **x86-64 (amd64) or 64-bit ARM (arm64)** — Raspberry Pi 4/5 with a 64-bit OS, ARM servers and cloud instances. Every image is published for both; Docker picks the right one. 32-bit ARM is not supported |
| Free ports | 80 and 443 on the host, or others you choose — see [Ports](#ports) |
| Outbound from the host | TCP to your devices' SSH ports; ICMP to devices you monitor; HTTPS to `downloads.openwrt.org` for OpenWrt update checks; HTTPS to your notification services |
| A name or address | The address users will type in the browser — it becomes `SERVER_ADDRESS` |

## What gets created

The default `docker-compose.yml` starts **five containers**; the
`docker-compose-no-postgres.yml` variant starts **four** (no database
container).

| Container | Image | Role | Published ports |
|---|---|---|---|
| `routerfleet` | `ghcr.io/alessiobravi/routerfleet` | The application under gunicorn, internal port 8001 | none |
| `routerfleet-cron` | `ghcr.io/alessiobravi/routerfleet-cron` | Calls the application's scheduler endpoints every minute | none |
| `routerfleet-monitoring` | `ghcr.io/alessiobravi/routerfleet-monitoring` | Pings the fleet and reports status changes | none |
| `routerfleet-nginx` | `ghcr.io/alessiobravi/routerfleet-nginx` | TLS, static files, the only published service | 80, 443 |
| `routerfleet-postgres` | `postgres:16` (official image) | The database — default compose file only | none |

Also created: one Compose network (internal; containers reach each other by
name) and six named volumes — see [Volumes](#volumes-and-what-to-back-up).

**Start order and health.** PostgreSQL, the application and nginx have healthchecks,
and the others wait for them: the application starts once PostgreSQL accepts
connections, and the scheduler, the monitoring loop and nginx start once the
application answers a real page — so nothing calls it while it is still
migrating. `docker compose ps` shows `healthy` for both when the installation is
up. nginx is healthy once it listens on 443, which on a first start is after it
has generated its certificate. The application is given three minutes for its
first start, which is when a large database migrates.
How the pieces talk to each other is in [Architecture](architecture.md).

## Images and versions

Every push to `main` is a release. Each of the four images is published as:

| Tag | Meaning |
|---|---|
| `:latest` | The most recent release. The compose files use this |
| `:<version>`, e.g. `:0.8.0` | That exact release, never moved |

Releases are numbered MAJOR.MINOR.PATCH: a release that only fixes or maintains
raises the last number (0.8.0 → 0.8.1), one that brings a new feature raises the
middle one (→ 0.9.0). Moving between two releases with the same middle number
therefore never changes what the application does, only how well it does it.

Images are public — no registry login is needed to pull them — and each tag holds an amd64 and an arm64 build. Each release
also has a git tag `v<version>` and a GitHub Release with its
[changelog](../CHANGELOG.md) section.

**Pinning a version.** To stay on a release until you decide to move, replace
`:latest` with the version in all four `image:` lines of your
`docker-compose.yml`:

```yaml
    image: ghcr.io/alessiobravi/routerfleet:0.8.0
```

All four images must carry the same version.

## Choosing a compose file

| File | Use it when | Containers |
|---|---|---|
| `docker-compose.yml` | Default: RouterFleet runs its own PostgreSQL | 5 |
| `docker-compose-no-postgres.yml` | SQLite, or a PostgreSQL server you already run | 4 |
| `docker-compose-dev.yml` | Building the images from a checkout instead of pulling | 5 |
| `docker-compose-build.yml` | Only defines builds, used by `build_and_push.sh` | — |

The application **refuses to start on a mismatch** between the compose file and
the database settings, with a message saying which file to use:

| `DATABASE_ENGINE` | `POSTGRES_HOST` | Required file |
|---|---|---|
| `postgres` | empty | `docker-compose.yml` |
| `postgres` | set | `docker-compose-no-postgres.yml` |
| `sqlite` | — | `docker-compose-no-postgres.yml` |

## Standard installation

```bash
mkdir routerfleet && cd routerfleet
wget -O docker-compose.yml https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/docker-compose.yml
wget -O .env https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/.env.example
```

Edit `.env`. The minimum is:

```env
SERVER_ADDRESS=routerfleet.example.com
POSTGRES_PASSWORD=a-long-random-password
TIMEZONE=Europe/Rome
```

Start it:

```bash
docker compose up -d
```

On first start the application migrates the database, generates its secrets
in the `app_secrets` volume, and nginx generates a self-signed certificate.
Open `https://<SERVER_ADDRESS>`, accept the certificate warning, and create the
first account — it becomes an administrator (level 50).

The directory name (`routerfleet` above) becomes the Compose project name and
prefixes the volume names: `routerfleet_app_secrets`, and so on.

## Variant: SQLite

Suited to small fleets and trials. The database is a file in the
`sqlite_volume` volume.

```bash
wget -O docker-compose.yml https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/docker-compose-no-postgres.yml
```

```env
DATABASE_ENGINE=sqlite
SERVER_ADDRESS=routerfleet.example.com
TIMEZONE=Europe/Rome
```

The `POSTGRES_*` variables are ignored.

## Variant: external PostgreSQL

```bash
wget -O docker-compose.yml https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/docker-compose-no-postgres.yml
```

```env
DATABASE_ENGINE=postgres
POSTGRES_HOST=db.internal.example.com
POSTGRES_PORT=5432
POSTGRES_DB=routerfleet
POSTGRES_USER=routerfleet
POSTGRES_PASSWORD=...
```

The database and user must exist; the application creates the tables on first
start. The `routerfleet` container must be able to reach the host and port.

## Configuration reference

### `.env` variables

| Variable | Required | Default | Accepted | Effect |
|---|---|---|---|---|
| `SERVER_ADDRESS` | **yes** | — | hostname or IP, optionally `:port` | The address users reach the installation at. Becomes `ALLOWED_HOSTS` and the CSRF trusted origins. Include the port if you changed the published HTTPS port. A wrong value gives `400 Bad Request` or CSRF errors |
| `TIMEZONE` | no | `Etc/UTC` | an [IANA name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) | Backup times, schedule times, daily report time, displayed timestamps. Passed to every container as `TZ` |
| `DEBUG_MODE` | no | `False` | `True` / `False` | Django debug mode. **Never `True` in production** — it exposes internals on errors |
| `DATABASE_ENGINE` | no | `postgres` | `postgres`, `sqlite` | Which database. Must match the compose file |
| `POSTGRES_DB` | with postgres | — | name | Database name |
| `POSTGRES_USER` | with postgres | — | name | Database user |
| `POSTGRES_PASSWORD` | with postgres | — | string | Database password. With the bundled database it is also what the database is created with on first start; changing it later requires changing it inside PostgreSQL too |
| `POSTGRES_HOST` | external only | empty | hostname | Set only for an external server; empty means the bundled container |
| `POSTGRES_PORT` | no | `5432` | port | External server's port |
| `HTTPS_REDIRECT_POLICY` | no | `always` | `always`, `never` | `always` redirects port 80 to HTTPS. `never` serves the application on plain HTTP as well — passwords then cross the network in cleartext |

`localhost`, `127.0.0.1`, `[::1]` and `routerfleet` are always accepted in
addition to `SERVER_ADDRESS`.

### Set by the compose file — do not change

| Variable | Value | Purpose |
|---|---|---|
| `COMPOSE_VERSION` | `08a` | The image refuses to start with an older compose file that lacks a required volume or variable |
| `COMPOSE_TYPE` | `with-postgres` / `no-postgres` | Lets the image check the database settings match the compose file |

### Application settings

Not environment variables: they live in `routerfleet/settings.py`, and
`production_settings.py` is regenerated on every container start, so a
permanent override needs an image of your own.

| Setting | Default | Effect |
|---|---|---|
| `UPDATE_CHECK_URL` | empty | Endpoint asked whether a newer version exists. **Empty: no request is made, no update is ever reported** |
| `UPDATE_CHANGELOG_URL` | empty | Endpoint the "Update Available" button reads a changelog from |
| `PROJECT_REPOSITORY_URL` | this repository | Where the interface's project links point |
| `PROJECT_SUPPORT_URL` | this repository's issues | Where "Request Device Support" points |
| `SCHEDULE_LATE_LIMIT_MINUTES` | `2` | How late a command schedule may still run |

### Generated on first start

| Item | Where | Notes |
|---|---|---|
| `SECRET_KEY` | regenerated **every** start | Signs sessions — a restart signs everyone out |
| Encryption key | `app_secrets/encryption_key` | Encrypts stored device passwords. Created once, never rotated. **Losing it loses the passwords** |
| Monitoring key | `app_secrets/monitoring_key` | Authenticates the monitoring container. Created once |
| TLS certificate | `https_cert/nginx.pem`, `nginx.key` | Self-signed, RSA 4096, CN `localhost`, valid 10 years. Created only if missing |

## Ports

Only nginx publishes ports. To use others, change the left-hand side of the
mapping in `docker-compose.yml` and put the HTTPS port in `SERVER_ADDRESS`:

```yaml
    ports:
      - "8080:80"
      - "8443:443"
```

```env
SERVER_ADDRESS=routerfleet.example.com:8443
```

Never publish the application container's port 8001: see
[Behind your own reverse proxy](#behind-your-own-reverse-proxy).

## TLS certificates

nginx terminates TLS with `nginx.pem` and `nginx.key` from the `https_cert`
volume. To use your own certificate, replace both files and restart nginx:

```bash
docker run --rm -v routerfleet_https_cert:/cert -v "$PWD":/src alpine \
  sh -c 'cp /src/fullchain.pem /cert/nginx.pem && cp /src/privkey.pem /cert/nginx.key'
docker compose restart routerfleet-nginx
```

`nginx.pem` should contain the full chain. The nginx configuration also carries
commented-out settings for Cloudflare authenticated origin pulls.

## Behind your own reverse proxy

You can put another proxy in front of nginx. Two rules:

1. **Keep `/cron/` to yourself.** Those endpoints carry no authentication and
   drive the installation. The shipped nginx answers `404` for them, and the
   application refuses requests that carry `X-Real-IP`. Do not publish port
   8001 or proxy straight to the application container. Details in
   [Security](security.md#the-scheduler-endpoints).
2. **Set `SERVER_ADDRESS` to the public name** the browser sees.

## Volumes and what to back up

| Volume | Contents | Back up |
|---|---|---|
| `postgres_data` | The database (bundled PostgreSQL) | **yes** |
| `sqlite_volume` | The database (SQLite) | **yes**, when using SQLite |
| `app_secrets` | Encryption and monitoring keys | **yes — without it the stored passwords are unrecoverable** |
| `media_root` | Binary backup files | **yes** |
| `https_cert` | TLS certificate and key | if you supplied your own |
| `static_volume` | Collected static files | no, regenerated on start |

Backup commands are in [Upgrading](upgrading.md#before-you-start).

## Verifying the installation

```bash
docker compose ps                                  # running; postgres and routerfleet "healthy"
docker compose logs routerfleet --tail 50          # migrations, gunicorn started
docker compose logs routerfleet-cron --tail 20     # JSON replies every minute
docker compose logs routerfleet-monitoring --tail 20
```

In the interface, the **status page** shows when the scheduler and the
monitoring loop last reported in. If either is stale, that container is not
working, and the matching background work has stopped.

## Building the images yourself

To run local changes, build from a checkout:

```bash
git clone https://github.com/alessiobravi/routerfleet_improved.git
cd routerfleet_improved
cp .env.example .env    # edit it
docker compose -f docker-compose-dev.yml up -d --build
```

`build_and_push.sh` builds the four images and pushes them to a registry
tagged with the current version and `latest`. It is the manual alternative to
CI, which does this on every push to `main`.

## Uninstalling

```bash
docker compose down          # stops and removes the containers, keeps the data
docker compose down -v       # also deletes every volume: database, backups, keys
```

`down -v` cannot be undone. Take the backups above first if there is any chance
you will want the data.
