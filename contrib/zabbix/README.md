# Zabbix monitoring for RouterFleet

Monitor a RouterFleet installation with Zabbix: per-router backup health,
fleet-wide counters, and a collector that reports its own failures.

Everything here is self-contained - a template, a collector script, an agent
configuration fragment and a SQL grant file. No Zabbix server modules, no
agent patches, no network access from Zabbix to the database.

## What it monitors

| Item | Key | Meaning |
|---|---|---|
| Backup age (per router) | `routerfleet.backup.age[<uuid>]` | Seconds since the last **successful** backup |
| Backup result (per router) | `routerfleet.backup.result[<uuid>]` | 1 if the most recent **attempt** succeeded |
| Monitored routers | `routerfleet.routers` | Enabled routers that have a backup profile |
| Failing routers | `routerfleet.failing` | Fleet-wide count of routers whose last attempt failed |
| Collector probe | `routerfleet.probe` | 1 when the collector reached the database |

Routers are discovered automatically. Adding a device to RouterFleet puts it
under monitoring within the discovery interval; removing one retires its items
after the discovery lifetime.

## How it fits together

```mermaid
flowchart LR
    subgraph host["Docker host running RouterFleet"]
        subgraph stack["RouterFleet stack"]
            APP[routerfleet]
            CRON[routerfleet-cron]
            DB[(PostgreSQL<br/>routerfleet)]
        end
        AGENT[Zabbix agent]
        SCRIPT[zbx-routerfleet.sh]
    end
    SERVER[Zabbix server]

    CRON -->|schedules backups| APP
    APP -->|writes results| DB
    SERVER -->|polls item keys| AGENT
    AGENT -->|UserParameter| SCRIPT
    SCRIPT -->|SELECT as zbx_monitor<br/>ten columns only| DB
    SCRIPT -->|values| AGENT
    AGENT -->|values| SERVER
```

The collector runs **on the Docker host**, not inside a container, and reaches
the database with `docker exec`. The database therefore needs no published
port and no network exposure for monitoring to work.

## Prerequisites

- RouterFleet deployed with Docker Compose, PostgreSQL variant
- Zabbix server 7.0 or later (the template targets the 8.0 export schema)
- Zabbix agent or agent 2 installed on the Docker host
- The agent's user able to run `docker exec` against the database container
- `UnsafeUserParameters` left at its default of `0`

## Installation

### 1. Create the read-only database role

Edit `sql/zbx_monitor.sql` and replace the placeholder password, then:

```bash
docker exec -i routerfleet-postgres \
  psql -U routerfleet -d routerfleet < sql/zbx_monitor.sql
```

The role is granted `SELECT` on the few columns the collector reads, and
nothing else - not the device passwords, not the backed-up configurations. See
[design note 5](docs/design-notes.md) for why that matters.

### 2. Install the collector

```bash
sudo install -m 0755 scripts/zbx-routerfleet.sh /usr/local/sbin/zbx-routerfleet.sh
sudo /usr/local/sbin/zbx-routerfleet.sh probe      # expect: 1
sudo /usr/local/sbin/zbx-routerfleet.sh discovery  # expect: JSON with your routers
```

If your container or database names differ from the defaults, create
`/etc/default/zbx-routerfleet`:

```sh
RF_CONTAINER=routerfleet-postgres
RF_DB=routerfleet
RF_USER=zbx_monitor
```

### 3. Let the agent run it

```bash
sudo install -m 0644 agent/routerfleet.conf /etc/zabbix/zabbix_agent2.d/routerfleet.conf
sudo systemctl restart zabbix-agent2
```

For Zabbix agent 1, use `/etc/zabbix/zabbix_agentd.d/` and `zabbix-agent`.

The agent runs as the `zabbix` user, which must be able to reach the Docker
socket. Granting that is equivalent to granting root on the host, so prefer a
narrow `sudoers` rule over adding the user to the `docker` group:

```
zabbix ALL=(root) NOPASSWD: /usr/local/sbin/zbx-routerfleet.sh
```

and prefix the UserParameter commands with `sudo`.

### 4. Import the template

In the Zabbix frontend: **Data collection → Templates → Import**, and select
`templates/routerfleet-by-zabbix-agent.yaml`.

Then link **RouterFleet by Zabbix agent** to the host that represents your
Docker host.

### 5. Adjust the thresholds

| Macro | Default | Meaning |
|---|---|---|
| `{$ROUTERFLEET.BACKUP.MAX_AGE}` | `48h` | Backup counts as stale |
| `{$ROUTERFLEET.BACKUP.MAX_AGE.CRIT}` | `7d` | Stored configuration no longer a usable recovery point |

Set `MAX_AGE` to roughly twice your backup interval, so a single missed run
does not alarm but two consecutive ones do.

## Verifying the installation

```bash
# the collector, directly
/usr/local/sbin/zbx-routerfleet.sh probe       # 1
/usr/local/sbin/zbx-routerfleet.sh routers     # number of monitored routers
/usr/local/sbin/zbx-routerfleet.sh failing     # 0 if all is well

# through the agent, from the Zabbix server
zabbix_get -s <docker-host> -k routerfleet.probe
zabbix_get -s <docker-host> -k routerfleet.backup.discovery
```

In the frontend, **Monitoring → Latest data** filtered to the host should show
one age item and one result item per router within an hour of linking.

## What alarms, and what deliberately does not

**Alarms**

- a router with no successful backup inside `MAX_AGE` (warning) or
  `MAX_AGE.CRIT` (high)
- a router whose most recent attempt failed (average)
- the collector unable to reach the database (high)
- no data from the agent for 30 minutes (average)

**Deliberately silent**

- *Routers without a backup profile.* They are not meant to be backed up, so a
  permanent staleness alarm would be noise. They are not discovered at all.
- *A single failed attempt that a retry then fixes.* The result item records
  it, and it is visible in history, but the staleness triggers are what
  escalate - the question that matters is whether a usable backup exists, not
  whether every individual attempt succeeded.
- *Backup content changes.* This integration monitors whether backups happen,
  not what is in them.

## Troubleshooting

| Symptom | Cause |
|---|---|
| All items unsupported | Agent cannot run `docker exec`; check the sudoers rule |
| `probe` returns 0 | Role missing, wrong password, or container name differs |
| Discovery returns `{"data":[]}` | No enabled router has a backup profile assigned |
| Age is `999999999` | No successful backup on record for that router - see [design note 3](docs/design-notes.md) |
| Result always `1`, never `0` | You are running the old query - see [design note 2](docs/design-notes.md) |

## Design notes

The non-obvious decisions, and one bug worth knowing about, are documented in
[docs/design-notes.md](docs/design-notes.md). The most important: **backup age
is the authoritative signal**, and the obvious way to query "did the last
backup fail?" silently never reports a failure.

## Layout

```
templates/routerfleet-by-zabbix-agent.yaml   importable Zabbix template
scripts/zbx-routerfleet.sh                   collector, runs on the Docker host
agent/routerfleet.conf                       UserParameter definitions
sql/zbx_monitor.sql                          read-only database role
docs/design-notes.md                         why it is built this way
```

## Licence

MIT, like the rest of RouterFleet Improved.
