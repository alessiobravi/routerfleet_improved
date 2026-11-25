# Device types

Everything RouterFleet knows about a kind of device is **configuration**, in
*System settings → Device types* (Administrator). Nothing about a vendor is
written into the code that talks to devices.

A device type says:

| Part | What it decides |
|---|---|
| **Connection** | one SSH command per channel (RouterOS, OpenWrt, Linux) or an **interactive shell** with a prompt (Cisco, Juniper, Huawei); what is sent once after login (paging off); an optional enable step |
| **Recognition** | the connection test commands and the pattern their output must match |
| **Inventory** | the commands, and where each value - model, serial number, OS version, firmware, architecture, CPU, the version offered - is found |
| **Backups** | named text backups from command output; optional files made on the device and downloaded (over the command channel, SCP only where that is impossible); lines to ignore (timestamps); export options, with other words for OS versions below a given one (RouterOS 6) |
| **Update checks** | which of the device itself, the vendor's release list and the firmware repository say which version it could run |
| **Firmware** | where a firmware file is uploaded, how the upload is verified, how it is installed; the release channels of online updates - `<major or *> <channel>: https://… [| pattern]` - and whether a newer major version is offered |
| **Links** | what the address in the device list opens besides SSH (Winbox for RouterOS) |

Devices, Fleet Commander variants and CSV imports refer to a type by its
**short name** (`routeros`, `cisco-ios`), which never changes.

## Shipped types

| Short name | Connection | Text backup | Files | Update check |
|---|---|---|---|---|
| `routeros` — Mikrotik (RouterOS) | channel | `configuration: /export` | - | the device |
| `openwrt` — OpenWrt | channel | `configuration: uci export` | sysupgrade archive | vendor release list |
| `ubiquiti-airos` — Ubiquiti airOS | channel | `cat /tmp/system.cfg` | - | none |
| `cisco-ios` — Cisco IOS / IOS-XE | shell | `running-config: show running-config` | - | none |
| `juniper-junos` — Juniper Junos | shell | `configuration: show configuration`, `set: show configuration \| display set` | - | none |
| `huawei-vrp` — Huawei VRP | shell | `current-configuration: display current-configuration` | - | none |
| `monitoring` — Monitoring only | - | - | - | - |

Every shipped type can be edited, and **Restore preset** brings back its
shipped settings (its commands are restored one by one). The Cisco, Juniper and
Huawei types are built from the vendors' documented CLIs and tested against
recorded sessions; try them on your own device before relying on them (below).

## The commands

The commands of a type are its [system commands](fleet-commander.md#system-commands)
in Fleet Commander: connection test, inventory, backup - text, create, files to
download, remove, clean leftovers, and firmware - verify, install, online update, bootloader upgrade. On an existing
type they are edited one at a time, each with a review of the change. The
wizard for a new type takes them all at once, since nothing runs them yet.

### Text backups

*backup - text* has one command per line, `name: command`:

```
configuration: show configuration
set: show configuration | display set
```

Each named text is stored, compared and downloaded on its own; a change in any
of them is a configuration change. Lines matching **Lines to ignore** (a
`Building configuration...` header, `! Last configuration change at ...`) are
dropped first, so they never count as a change. Nothing is written on the
device.

### Inventory sources

Each inventory field lists sources, one per line; the first that gives a value
wins:

- `key:NAME` - the value of `NAME: value` or `NAME=value` lines;
- `re:PATTERN` - the first group of a regular expression over the whole output,
  e.g. `re:Version ([\w.()]+)`.

Reading the inventory fails when none of model, model version and OS version is
found.

## Interactive shell devices

For a shell type RouterFleet opens one terminal, waits for the **prompt**
(a regular expression matched against the last line), sends the **setup** lines
(`terminal length 0`, `set cli screen-length 0`, `screen-length 0 temporary`),
and for every command waits for the prompt again. A `--More--` pager that still
appears is answered. With an **enable command** and an *enable password* on the
device, privileged mode is entered first; a refused password stops the session.

The enable password is stored encrypted, never shown again, and - like the
device password - only sent to the address it was stored for.

## Update checks

A type can use any of:

- **the device itself** - the inventory field *Version offered by the device*
  (RouterOS asks MikroTik's update server; the inventory waits up to 15 seconds
  for the answer);
- **the vendor's release list** - a JSON document over https, the keys holding
  release numbers in order of preference, and optionally a confirmation address
  that must answer for a release to count (OpenWrt: images exist for the
  device's board). Cached for six hours; never an address on RouterFleet's own
  networks;
- **the firmware repository** - the newest enabled image for the device's type,
  model and architecture ([Firmware repository](firmware.md)).

The newest version any of them offers counts. A type with none shows *no update
check configured* instead of a guess, and its devices are not counted as
missing information.

## A new type: the wizard

*New device type* walks through identity, connection, recognition, inventory,
backups, update checks, firmware, and **Try it**. *Start from* copies a
shipped type into the form, to change what differs.

**Try it** connects to a device with the address and credentials typed there,
runs the connection test, the inventory and the text backups of the form -
saved or not - and shows what each found, field by field. Nothing is stored
except the device's host key, as for any connection, and an address of
RouterFleet itself or its internal network is refused.

## Deleting a type

A type can be deleted when no device uses it and the repository holds no
firmware for it; its command variants go with it.
