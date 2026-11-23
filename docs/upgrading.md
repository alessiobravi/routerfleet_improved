# Upgrading

## Before you start

Back up **two** things. The database alone is not enough.

```bash
# 1. The database
docker exec -e PGPASSWORD=your_password routerfleet-postgres \
  pg_dump -U routerfleet -d routerfleet > routerfleet-$(date +%Y-%m-%d-%H%M%S).sql

# 2. The app_secrets volume — holds the key your device passwords are
#    encrypted with. Compose prefixes the volume with the project name, which
#    is the name of your directory; `docker volume ls` shows the real name.
docker run --rm -v <project>_app_secrets:/secrets -v "$PWD":/backup alpine \
  tar czf /backup/app_secrets-$(date +%Y-%m-%d-%H%M%S).tar.gz -C /secrets .
```

A database backup without `app_secrets` cannot restore the stored passwords.

If you may want to go back, also keep the images you are running now — only
the newest release is published, so the one you have will not be downloadable
again (see [Rolling back](#rolling-back)):

```bash
docker save $(docker compose config --images) | gzip > routerfleet-images-$(date +%F).tar.gz
```

## The upgrade

```bash
cd path/to/routerfleet
docker compose down
wget -O docker-compose.yml https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/docker-compose.yml
docker compose pull
docker compose up -d
```

Migrations run automatically on start. Watch them:

```bash
docker compose logs routerfleet --tail 100
```

Then check the web interface and the status page.

## Coming from an earlier RouterFleet

RouterFleet Improved descends from two earlier projects, and a database from
either upgrades in place. Point this version at your existing database and the
migrations carry it forward — there is no export or re-import step.

| Your current installation | Path | What happens |
|---|---|---|
| **RouterFleet Improved** (this project) | In place | Only new migrations apply |
| **RouterFleet Extended** | In place | 39 migrations apply: three encrypt the SSH keys and tokens it stored as text; the others add the table of device host keys, the tables of second factors, the setting and link for keeping only changed backups, widen the schedule defaults column, add the system commands (two: the tag, then the commands), add the table of HTTPS settings, add the console's sessions table and the devices' Telnet switch and port, correct the spelling of the Configuration Manager level, add email notifications (the mail server settings and email channels, with two new events off for existing channels), add the ntfy server and priority to channels, switch off CallMeBot channels (no longer supported; kept with their history), record when each device was last enabled or disabled (filled in from the change log), add the display settings (tables keep opening with 10 rows) and the record of updates approved in System settings, let each device decide about unchanged backups (every device keeps following its profile), turn device types into configuration (the shipped types, Cisco, Juniper and Huawei added; Mikrotik (Branded) devices become RouterOS), take RouterOS and OpenWrt text backups from command output where their commands were not edited, add named text backups, the enable password, and the firmware repository, add indexes for the backup list, the message queue and history, notifications, the change log, sign-ins, downtime and console sessions, add an optional phone number to users, make unedited RouterOS backups text only (no binary .backup), and remove the WireGuard WebAdmin integration |
| **RouterFleet** (the original project) | In place | 52 additional migrations apply; the ones that touch data are described below |

This holds because the migration history is a strict superset: all 67 of the
original project's migrations and all 80 of RouterFleet Extended's are present
here, byte-identical. Nothing was renumbered, squashed or rewritten, which is
what makes the upgrade a no-drama `migrate`. The test suite holds a fingerprint of every
released migration and fails if one is ever edited, renamed or removed; it also
checks the counts in this table against the real migration plan.

### What the 52 migrations do when coming from the original

```mermaid
flowchart TD
    db[("Original project's database")] --> m1["Encrypt Router.password,<br/>SSH private keys and tokens<br/>in place"]
    m1 --> m2["Encrypt import task passwords,<br/>mask the stored CSV column"]
    m2 --> m3["Backfill config_change_detected<br/>across every device's history"]
    m3 --> m4["Add the audit log tables"]
    m4 --> m5["Add command verification fields,<br/>schedule exclusions, schedule defaults,<br/>available-version tracking,<br/>per-user address-link setting"]
    m5 --> done[("Ready")]
```

These touch existing data:

- **Credentials are encrypted.** Device passwords, SSH private keys and
  notification channel tokens are read, encrypted with the key generated in
  `app_secrets` on this version's first start, and written back. The encryption function is idempotent, so a migration that is
  interrupted and re-run cannot double-encrypt a value.
- **Import passwords are encrypted** and the password column of any stored
  import CSV is replaced with `********`.
- **`config_change_detected` is backfilled** for every existing backup, walking
  each device's history in order and applying the same rule the application
  applies going forward: the first backup of a device is not a change, a backup
  with no configuration text is skipped without resetting the comparison, and a
  differing hash is a change.

You will see this in the log while the password migrations run:

```
Could not decrypt a stored value. Either it is not encrypted yet (normal while
the upgrade that encrypts it is running), or it was encrypted with a different
key than the configured ENCRYPTION_KEY.
```

**On an upgrade from the original project this is expected and harmless** — the
values genuinely are not encrypted yet, which is precisely what the migration
is about to fix. It is only worth investigating if you see it *after* the
upgrade, during normal operation, which would mean `app_secrets` was replaced.

### Afterwards

Existing data is adopted rather than reset, so after the upgrade:

- Every backup has its change flag filled in, so the **Last Config Change**
  column and the change markers in the backup list are immediately populated
  from your existing history.
- Commands that already existed are upgraded by data migrations. Every RouterOS
  command whose payload uses the package update gains a verification and a 600 s
  verify timeout; a payload that installs a package also gains the
  `expected-version` line and the matching expectation. **A payload you wrote
  yourself is never replaced** — the missing line is inserted into it — and a
  variant that already defines its own verification is left alone.
- No device has a stored SSH host key yet. Each one's key is stored on its next
  connection and checked on every one after that — see [Device host
  keys](security.md#device-host-keys). A device that answers with a key that
  later changes is refused from then on.
- Device inventory has no *available version* until each device is next read,
  so version columns show as "no information" until the inventory cycle comes
  round. Use **Update Information** on the device list to force it.

## Moving the bundled PostgreSQL to a newer major version

The default compose file runs `routerfleet-postgres`, which is the official
`postgres:16` with every available update installed, and it stays on 16: the files a
PostgreSQL major version writes cannot be opened by the next one, so changing
the image of an existing installation would leave it unable to start. Patch
releases of 16 arrive with `docker compose pull`. To move to a newer major
version, carry the data across with a dump:

```bash
# 1. Dump everything from the running 16
docker exec -e PGPASSWORD=your_password routerfleet-postgres \
  pg_dump -U routerfleet -d routerfleet --format=custom > routerfleet.dump

# 2. Stop the stack and set the old data aside (rename, do not delete, until
#    the new one is confirmed to work)
docker compose down
docker volume create routerfleet_postgres_data_16_old
docker run --rm -v routerfleet_postgres_data:/from -v routerfleet_postgres_data_16_old:/to alpine \
  sh -c 'cp -a /from/. /to/'
docker volume rm routerfleet_postgres_data

# 3. Change the database "image:" line in docker-compose.yml to the new major
#    version (for example postgres:17), then
#    start only the database and restore into it
docker compose up -d routerfleet-postgres
docker exec -i -e PGPASSWORD=your_password routerfleet-postgres \
  pg_restore -U routerfleet -d routerfleet --no-owner < routerfleet.dump

# 4. Start the rest
docker compose up -d
```

From PostgreSQL 18 on, the official image keeps its data under
`/var/lib/postgresql` rather than `/var/lib/postgresql/data`: change the volume
line of the database service accordingly, or the new server writes into a
directory that is not on the volume. Read the image's notes for the version you
move to.

Once the installation has run on the new version for a while, remove
`routerfleet_postgres_data_16_old`. Substitute your project name for
`routerfleet` in the volume names — `docker volume ls` lists them.

## The database image

The compose file runs `ghcr.io/alessiobravi/routerfleet-postgres` instead of
the official `postgres:16` the earlier projects use. It is the same PostgreSQL 16 on the same
Debian base with every available update installed, so an existing database is
used as it is: nothing to dump, nothing to restore. A compose file that still
names `postgres:16` keeps working too.

## What you may notice after upgrading

- **The WireGuard WebAdmin integration is gone.** Its menu entry and pages are
  removed, and an upgrade drops its table together with the token stored in it.
  Nothing else depended on it.
- **The home page is the status page**; the former welcome text is under
  **About**, at the bottom of the menu.
- **Second factors are available** - an authenticator app and passkeys, in
  *My Settings*. Nobody is asked for one until they turn it on.
- **The in-app updater is removed** (0.23.1): no container holds the Docker
  socket. If your compose file mounts it into `routerfleet-cron`, take the
  current compose file or remove those lines ([Updating](updating.md)).
- **RouterOS backups become text only** (0.23.0): where the RouterOS backup
  commands are as shipped, the binary `.backup` is no longer taken; the text
  still comes from `/export`. Edited commands are kept. Stored `.backup` files
  stay until you delete them in *Tools → Backup clean-up*.
- **Device types become configuration** (0.22.0). Every device keeps its type
  and behaves as before:
  - **Mikrotik (Branded)** is gone: its devices become *Mikrotik (RouterOS)*
    (the commands were the same; only the connection test told them apart).
    Fleet Commander variants for it fold into the RouterOS ones; where a
    command had only a Branded variant, that one becomes its RouterOS variant.
  - **RouterOS** backups whose system commands were never edited take the text
    from `/export` output instead of an exported file on the device; the
    `.backup` file is still downloaded. Edited ones keep exactly what they did:
    the downloaded `.rsc` is still the text backup.
  - **OpenWrt** took its text from the output of the first "create" line; that
    line becomes the "backup - text" command, edited or not.
  - Cisco IOS, Juniper Junos and Huawei VRP are added as new types; nothing
    uses them until you choose them for a device.
- **CallMeBot channels are switched off** (0.18.0): CallMeBot is no longer
  supported. The channels and their history are kept; *System Settings →
  Notifications* lists them, to be recreated as email, ntfy or Telegram.
  Every other channel keeps working as before.
- **HTTPS does not change.** The new [System settings → HTTPS](https.md) page
  starts on *As configured in .env*: `HTTPS_REDIRECT_POLICY` and the
  certificate already deployed in the volume keep working exactly as before.
  To use the page, a compose file of your own needs `- https_cert:/certificate`
  under the `routerfleet` service's volumes (the shipped files have it).
- **Backup storage does not change.** Profiles that exist when you upgrade
  keep every backup in full, as before (*Keep unchanged backups* is on for
  them). Switch a profile's setting off to keep only changes from then on; the
  *Backup clean-up* tool can then free the duplicates already stored. Profiles
  created after the upgrade start with keeping only changes.
- **Retention never deletes a device's newest stored configuration**, however
  old it is.

- **Device host keys are checked.** Nothing is needed on upgrade: keys are
  learned on each device's next connection. A device you reset or replace later
  must have its key forgotten on its page before it can be reached again.
- **Sign-in attempts are limited**: 5 refused attempts for an account or from an
  address within 15 minutes, then 15 minutes of refusals.
- **Secure cookies** need the current compose file, which passes
  `HTTPS_REDIRECT_POLICY` to the application. An older compose file keeps
  working, without them.
- **Bookmarked action links no longer act.** Every action is a button that
  POSTs; an address such as `...?action=delete&confirmation=delete` only
  shows the page.

## Only the newest release is published

Publishing a release deletes every older one: its GitHub Release, git tag and
images. An installation whose compose file names a version that no longer
exists upgrades like any other — set the five `image:` lines back to `:latest`
and pull.

## Version requirements

The entrypoint refuses to start if `COMPOSE_VERSION` in your compose file is not
`08a`. That is the guard against running a new image with an old compose file
that lacks a required volume or variable — if you see

```
ERROR: Please upgrade your docker compose file. Exiting.
```

download the current compose file as shown above.

## Rolling back

Only the newest release can be downloaded, so going back means using the images
you saved before upgrading ([Before you start](#before-you-start)). Stop the
stack, restore the database dump and the `app_secrets` archive, load the saved
images and point the five `image:` lines at them, then start it again:

```bash
docker load < routerfleet-images-YYYY-MM-DD.tar.gz
docker image ls ghcr.io/alessiobravi/routerfleet   # the version you came from
```

Note that migrations are **not** automatically reversed by downgrading the
image — restore the database rather than relying on reverse migrations.

The two data migrations that encrypt passwords define a no-op reverse, which
means reversing them leaves the values encrypted. This is another reason to roll
back by restoring the dump rather than by migrating backwards.
