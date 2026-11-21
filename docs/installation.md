# Installation

Requires Docker with the Compose plugin. Everything is configured through a
`.env` file next to the compose file.

## Choosing a compose file

| File | Use when |
|---|---|
| `docker-compose.yml` | Default. Runs a Postgres container for you |
| `docker-compose-no-postgres.yml` | SQLite, or an external/remote Postgres |
| `docker-compose-dev.yml` | Builds the images locally instead of pulling them |
| `docker-compose-build.yml` | Build definitions for publishing images |

The entrypoint **validates that the compose file matches the database setting**
and refuses to start on a mismatch, rather than failing obscurely later:

| `DATABASE_ENGINE` | `POSTGRES_HOST` | Required compose file |
|---|---|---|
| `postgres` | unset | `docker-compose.yml` |
| `postgres` | set | `docker-compose-no-postgres.yml` |
| `sqlite` | — | `docker-compose-no-postgres.yml` |

## Steps

```bash
mkdir routerfleet && cd routerfleet
wget -O docker-compose.yml https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/docker-compose.yml
wget -O .env https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/.env.example
# edit .env
docker compose up -d
```

Open `https://<SERVER_ADDRESS>` and create the first account, which is given
permission level 50.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SERVER_ADDRESS` | — | **Required.** The address you reach the server at. Append `:port` if you changed the published HTTPS port. A wrong value causes CSRF errors |
| `DEBUG_MODE` | `False` | Never `True` in production |
| `TIMEZONE` | `Etc/UTC` | An [IANA timezone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones). Drives schedule times and displayed timestamps |
| `DATABASE_ENGINE` | `postgres` | `postgres` or `sqlite` |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | — | Required for Postgres |
| `POSTGRES_HOST` / `POSTGRES_PORT` | — | Set only for an external Postgres. Port defaults to 5432 |
| `HTTPS_REDIRECT_POLICY` | `always` | `never` also serves plain HTTP — sends passwords in cleartext, not recommended |

`localhost` and `127.0.0.1` are always accepted in addition to
`SERVER_ADDRESS`, so the installation answers on the machine it runs on.

## Application settings

These are not environment variables; they live in `routerfleet/settings.py` and
can be overridden from `production_settings.py`.

| Setting | Default | Meaning |
|---|---|---|
| `UPDATE_CHECK_URL` | `''` | Endpoint asked whether a newer version exists. **Empty means no request is made and no update is ever reported** |
| `UPDATE_CHANGELOG_URL` | `''` | Endpoint the "Update Available" button reads a changelog from |
| `PROJECT_REPOSITORY_URL` | this repository | Where the interface's project links point |
| `PROJECT_SUPPORT_URL` | this repository's issues | Where the "Request Device Support" button points |
| `SCHEDULE_LATE_LIMIT_MINUTES` | `2` | How late a command schedule may still run. Widen it if your scheduler ticks less than once a minute |

Note that `production_settings.py` is **regenerated on every container start**,
so permanent overrides belong in a mounted file or an image of your own.

## TLS certificates

A self-signed certificate is generated on first start. To use your own, replace
`nginx.pem` and `nginx.key` in the `https_cert` volume and restart nginx.

With the self-signed certificate you will have to accept the browser warning.

## Volumes

| Volume | Contents | Back up? |
|---|---|---|
| `postgres_data` | The database | **yes** |
| `app_secrets` | `encryption_key`, `monitoring_key` | **yes — critical** |
| `media_root` | Binary backup files | **yes** |
| `sqlite_volume` | SQLite database, if used | **yes**, when using SQLite |
| `static_volume` | Collected static files | no, regenerated |
| `https_cert` | TLS certificate and key | if you supplied your own |

`app_secrets` holds the key your device passwords are encrypted with. **A
database backup without it cannot restore those passwords.** See
[Security](security.md).

## Verifying it came up

```bash
docker compose ps
docker compose logs routerfleet --tail 50
docker compose logs routerfleet-cron --tail 20
```

The application does nothing on its own — if the cron container is not running,
backups, commands, notifications and inventory refreshes all stop while the web
interface keeps working. The status page shows when the scheduler and the
monitoring loop last reported in, which is the quickest way to notice.

## Behind your own reverse proxy

If you put another proxy in front, or publish the application port directly,
read [Security — the scheduler endpoints](security.md#the-scheduler-endpoints)
first. The `/cron/` paths carry no authentication and the shipped nginx is what
normally blocks them.
