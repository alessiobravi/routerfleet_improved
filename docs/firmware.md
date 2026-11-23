# Firmware repository

*System settings → Firmware repository* (Configuration Manager) keeps firmware
files per [device type](device-types.md) and installs them on devices.

## Uploading

An image has a device type, the **version** it installs (as the device reports
it afterwards), and the file - up to 1 GB, the only page where nginx accepts a
large upload. Optionally:

- **models** - a regular expression matched against the inventory's model and
  model version; empty: every model;
- **architecture** - e.g. `arm64`; empty: every architecture;
- **vendor checksum** - the SHA-256 from the vendor's download page. A file that
  does not match is refused: it is not the file the vendor published.

RouterFleet computes and shows the SHA-256 of every file. File names are made
safe for paths and commands on devices. An image can be switched off (no longer
offered) or deleted when no rollout refers to it.

With the device type's **firmware repository** update check on, a device is
*behind* when the repository holds a newer enabled image for it.

## Updating a device that has an update

A device with an update available carries the amber arrow in the router list.
For the Configuration Manager the arrow opens **Update** - so does *Update* on
the device page, and **Update firmware** for the devices selected in the list.
The page offers every update the device can take, **in this order**:

1. **A file of the repository made for it** - its device type, and the model
   and architecture the image is for - newest first. The newest is suggested.
2. **The device's own online update**, one option per **release channel** its
   device type lists, each with the newest version the vendor publishes on that
   channel - for RouterOS: long-term, stable, testing, development. The device
   downloads and installs the release itself; it must reach the vendor. Without
   a repository file, the channel the device is on is suggested.

Only versions **newer** than the running one are offered, and only of the
**same major version**: a jump such as RouterOS 6 to 7 needs a procedure of its
own and is not offered. For several devices, each option says how many of them
it is for; a device already in a running update is left out.

The update then runs like every rollout (below): a backup first, the update,
and the device must come back on **exactly** the version it was offered - a
beta is not another beta, nor its final release. Several devices: one first,
alone; the rest a few at a time; any failure stops the others.

How each step is done is configuration of the device type, nothing is built
in: the channels and the addresses publishing their newest version (*Online
update: release channels*), and the system command **firmware - online
update**, which gets `{{ channel }}` and `{{ firmware_version }}`. For RouterOS:

```
/system package update set channel={{ channel }}
/system package update check-for-updates once
/system package update install
```

| RouterOS | Channel addresses (as RouterOS itself asks) |
|---|---|
| 7 | `https://upgrade.mikrotik.com/routeros/NEWESTa7.<channel>` - long-term, stable, testing, development |
| 6 | `https://upgrade.mikrotik.com/routeros/NEWEST6.<channel>` - long-term, stable (6 *testing* answers 7.x: left out) |

The addresses are read by the server - https only, never on RouterFleet's own
networks - and kept for an hour. A device type without channels or without the
online update command offers no online update; other device types can be given
both.

## Updating devices from an image

*Update devices* on an image lists the enabled devices of its type and model,
those running an older version selected. One device is marked **first**.

For every device, in order:

1. **Backup** - a fresh backup through the normal pipeline; the update waits
   for it to succeed.
2. **Upload** - the file is copied by SCP to the device type's upload path.
3. **Verify** - *firmware - verify the upload* prints the file's SHA-256 or
   size on the device; anything but the repository's value stops here, before
   anything is installed.
4. **Install** - *firmware - install* (usually ending in a reboot; a connection
   that drops is expected).
5. **Check** - the inventory is read again until the device reports the new
   version (up to 20 minutes).

The first device goes **alone**. The others follow only once it is back on the
new version, a few at a time (*Devices at a time*). **Any failure stops the
rollout**: devices not started are skipped, and the page says what happened.
**Abort** skips the devices not started; a device already installing finishes
on its own.

Starting a rollout takes the Configuration Manager level and the confirmation
sum. Every image and rollout is in the change log.

## Install procedures

| Type | Upload to | Verified by | Install |
|---|---|---|---|
| RouterOS | `/{{ firmware_name }}` | size in bytes | `/system reboot` (RouterOS installs a `.npk` found at boot) |
| OpenWrt | `/tmp/{{ firmware_name }}` | SHA-256 | `sysupgrade {{ firmware_path }}` (keeps the configuration) |
| Cisco, Juniper, Huawei, airOS | - | - | none shipped |

The shipped procedures of the network operating systems are deliberately empty:
their install steps depend on the platform (IOS-XE install mode or bundle mode,
Junos `request system software add`, VRP `startup system-software`). Define
them for your platform in the device type, and try them on one device first.

Each step runs from the scheduler, one step per device per minute; an upload
must complete within five minutes per scheduler call (a slow link is retried
for up to 40 minutes).
