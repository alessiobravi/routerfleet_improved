<p align="center">
  <img src="docs/images/routerfleet-improved.svg" alt="RouterFleet Improved logo" width="125" height="125">
</p>

# RouterFleet Improved

*by Alessio Bravi*

Centralized backup, configuration management and fleet command execution for
network devices. RouterFleet keeps a configuration history for every device,
runs commands across a fleet on a schedule, watches whether devices are
reachable, and records who changed what.

Supported devices: **MikroTik RouterOS**, **OpenWrt**, **Ubiquiti airOS**,
**Cisco IOS / IOS-XE**, **Juniper Junos** and **Huawei VRP** - and any other
device reachable over SSH, since device types are configuration with a wizard
to add your own - plus a *monitoring only* type for anything that just needs
to be pinged.

> **Provided "as is", without warranty — you use it at your own risk.** It acts on
> your network devices with administrative rights. Read the
> [disclaimer](#disclaimer) before installing.

## Quick install

Needs Docker with the Compose plugin on an x86-64 or 64-bit ARM host, and two free ports: 80 and 443, or others set in `.env`.

```bash
mkdir routerfleet && cd routerfleet
wget -O docker-compose.yml https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/docker-compose.yml
wget -O .env https://raw.githubusercontent.com/alessiobravi/routerfleet_improved/main/.env.example
```

Set at least these three in `.env`:

```env
SERVER_ADDRESS=routerfleet.example.com
POSTGRES_PASSWORD=a-long-random-password
TIMEZONE=Europe/Rome
```

```bash
docker compose up -d
```

Open `https://<SERVER_ADDRESS>` and create the first account, which becomes an
administrator. Ports 80 and 443 taken? Set `HTTP_PORT` and `HTTPS_PORT` in
`.env` and open `https://<SERVER_ADDRESS>:<HTTPS_PORT>`. This starts **five containers** — the application, its
scheduler, the monitoring loop, nginx and PostgreSQL — from public images on
`ghcr.io/alessiobravi`.

SQLite or your own PostgreSQL, other ports, your own certificate, pinning a
version, every option: **[Installation — full specification](docs/installation.md)**.
Back up the `app_secrets` volume: it holds the key your device passwords are
encrypted with.

## What it does

- **Device console** — a terminal on any device, right in the browser: SSH (or
  Telnet where a device has nothing else), with its backup account or any other
  account, also on a phone. One-time tickets, host keys checked, closed as soon
  as the page closes or you sign out, every session in the audit log.
  [More](docs/console.md)
- **Device types as configuration** — how RouterFleet talks to each kind of
  device (SSH command channel or an interactive CLI shell), recognises it,
  reads its inventory, backs it up and learns its version, all edited in
  System settings; a wizard adds new types and tries them on a real device
  before saving. RouterOS, OpenWrt, airOS, Cisco IOS, Juniper Junos and Huawei
  VRP ship ready. [More](docs/device-types.md)
- **Backups** — daily, weekly, monthly and on-demand, per-device retention,
  text taken straight from command output (several named texts where a device
  type has them, such as Juniper's configuration and its set format) plus
  binary files where a type makes them, with a line-by-line diff - readable on
  a phone too - and a flag on every backup that changed the configuration. A
  profile, or a single device, can **keep only changes**.
- **Fleet Commander** — commands with a payload per device type, run by hand or
  on a schedule, with **verified results**: a command counts as successful only
  when a verification command proves it, so an update that reboots a device
  mid-run is judged on its outcome rather than on an exit code it never sent.
  Run on one device from the device list, on a selection, or on groups - always
  after a confirmation page showing the exact commands. What RouterFleet itself
  sends to devices (connection test, inventory, backup steps) is visible and
  editable as protected **system commands**.
- **Device inventory** — model, serial, OS and firmware version, architecture,
  CPU, and the version each device could run - from the device itself, the
  vendor's release list or the firmware repository, as its type says.
- **Firmware repository** — firmware per device type, checksummed, installed
  one device first and then the rest in small batches, each with a backup
  before and its new version confirmed after; any failure stops the rollout.
  [More](docs/firmware.md)
- **Update check** — the newest release and what it changes, checked hourly
  or on demand; signed release images. RouterFleet never updates itself: no
  container is given Docker access. [More](docs/updating.md)
- **Monitoring** — an independent container pings the fleet and records
  downtime; status changes raise notifications.
- **HTTPS** — Let's Encrypt with automatic renewal, your own certificate,
  self-signed, or behind a reverse proxy, chosen in System settings, with a
  live check of what passkeys need.
- **Tools** — free the space of duplicate backups, and export the latest
  configuration of every device as a zip.
- **A live interface** — a status page with charts and cards that open what
  they count, object pages with progress and a timeline, updating themselves
  while something runs.
- **Notifications** — email (each user picks what to receive; your own SMTP
  server), ntfy (ntfy.sh or your own server) and Telegram, configured in
  System Settings, with
  concatenation so a flapping link does not send fifty messages.
- **Audit log** — every sign-in attempt, and every configuration change field
  by field, in an append-only trail.
- **Secrets encrypted at rest** — device passwords are stored with Fernet
  symmetric encryption, never in cleartext.
- **Hardened** — device SSH host keys are trusted on first use and checked on
  every connection, sign-in attempts are limited, and every action needs a POST
  with the CSRF token.
- **Zabbix integration** (optional) — a template and collector that report
  backup age, failed backups, host-key changes and failed sign-ins to Zabbix,
  through a read-only database role that cannot see any secret.

## Documentation

| Page | What it covers |
|---|---|
| [Architecture](docs/architecture.md) | The five containers, the scheduler, the data model |
| [Installation](docs/installation.md) | Full specification: containers, images, every option and variant |
| [Upgrading](docs/upgrading.md) | Backups before upgrading, upgrade paths, rolling back |
| [Updating](docs/updating.md) | The update check, updating on the host, verifying release images |
| [Devices](docs/devices.md) | Credentials, inventory, version states, the device list |
| [Device types](docs/device-types.md) | Types as configuration: connection, recognition, inventory, backups, update checks; the wizard |
| [Firmware repository](docs/firmware.md) | Firmware per device type; updates one device first, then the rest |
| [Backups](docs/backups.md) | Profiles, scheduling, the backup pipeline, retention, diffs |
| [Fleet Commander](docs/fleet-commander.md) | Commands, variants, verification, jobs, schedules |
| [Monitoring](docs/monitoring.md) | Reachability checks and downtime records |
| [Notifications](docs/notifications.md) | Channels, concatenation, daily reports |
| [Audit log](docs/audit-log.md) | Login history and the change log |
| [Users and permissions](docs/users.md) | The five permission levels, and what each one reaches |
| [Import tool](docs/import-tool.md) | Bulk device import from CSV |
| [Device console](docs/console.md) | A terminal on any device in the browser: SSH or Telnet, security, when it closes |
| [HTTPS](docs/https.md) | Let's Encrypt, own certificate, reverse proxy - in System settings |
| [Tools](docs/tools.md) | Backup clean-up and export of the latest backups |
| [Security](docs/security.md) | Secrets at rest, host keys, sign-in limits, the scheduler endpoints |
| [Operations](docs/operations.md) | Management commands, housekeeping, troubleshooting, running the tests |
| [Zabbix integration](docs/zabbix.md) | Optional: what it reports, what it can read, what it needs |

## Versions

Releases are numbered MAJOR.MINOR.PATCH: fixes and maintenance raise the last
number (0.11.5 → 0.11.6), a new feature raises the middle one (→ 0.12.0). Every
push to `main` is a release, and **only the newest release is offered**: older
releases, tags and images are deleted when it is published. Use `:latest`. What
changed is in the [changelog](CHANGELOG.md).

## Security

Report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## Disclaimer

**RouterFleet Improved is provided "as is", without warranty of any kind,**
express or implied, including but not limited to the warranties of
merchantability, fitness for a particular purpose and non-infringement. In no
event shall the authors or copyright holders be liable for any claim, damages
or other liability, whether in an action of contract, tort or otherwise,
arising from, out of or in connection with the software or its use. **You use
it entirely at your own risk**, and any consequence of using it is yours.

In practical terms:

- **It acts on your network devices.** It logs in to them with administrative
  credentials and runs commands, updates firmware and reads configurations. A
  command or update sent through it can interrupt service, change a
  configuration you did not intend, or leave a device unusable. Try every
  command on a device you can afford to lose before running it on a fleet.
- **Backups can fail or be incomplete** without the software noticing, and a
  backup that has never been restored is not proven. Test restoring before you
  rely on one.
- **Monitoring and notifications can be late, wrong or missing.** Do not make it
  the only thing standing between you and an unnoticed outage.
- **Securing the installation is your responsibility**: who can reach it, its
  certificates, its credentials, the `app_secrets` volume, and keeping it
  updated.
- **Compliance is your responsibility**: the licences, terms of service and
  laws that apply to your devices, your networks and the data they carry.
- **No affiliation.** This project is not affiliated with, endorsed by or
  supported by MikroTik, Ubiquiti, the OpenWrt project or any other vendor.
  Product names are trademarks of their owners.

The [LICENSE](LICENSE) is the legal text; this section restates it in practical
terms and does not limit it.

## License

MIT — see [LICENSE](LICENSE). RouterFleet Improved is a derivative work; the
authors it builds on are credited there.
