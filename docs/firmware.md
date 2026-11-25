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

A device with a system update available carries the **amber arrow** in the
device list. For the Configuration Manager the arrow opens the update page - so
does *Update system* on the device page, and **Update system** for the devices
selected in the list. (The bootloader firmware has its own purple arrow and
process - below.)
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

### The bootloader firmware (RouterOS: RouterBOOT) - a process of its own

A MikroTik keeps its bootloader, RouterBOOT, apart from RouterOS:
`current-firmware` is what it boots with, `upgrade-firmware` what the installed
RouterOS brings. The inventory reads both. Upgrading it is **independent of
system updates**: its own **purple arrow** in the device list (the amber one is
the system), **Update bootloader** on the device page and for selected hosts,
its own page and its own rollout - a backup, the upgrade, a reboot, and the
device must come back reporting the new bootloader firmware. A system update
never touches it.

The procedure is the system command **firmware - bootloader upgrade**
(RouterOS: `/system routerboard upgrade`, then `/system reboot`). MikroTik
documents a confirmation for the upgrade on an interactive terminal; over
RouterFleet's connection it runs as a script does - try it on one device first.

### Watching an update

The rollout page shows the update the way a device page shows a device: what
is updated and how far it is, a tile per state, every device with its step, and
the **timeline** - every step of every device: the backup, the upload or the
download, the install, and after each reboot every change of reachability: the
device gone, back over SSH with the version it reports, back on the new version.
It updates itself while the update runs.

When a device is done - back online on its new version, or failed - the
channels with **Firmware update finished** switched on get a message
([Notifications](notifications.md)). The event is off for existing channels.

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

What ships with each device type. Everything is configuration: change it in
the device type and its system commands, and try a change on one device first.
They follow the vendors' documentation; airOS rests on field reports.

| Type | Upload to | Verified by | Install | Online update |
|---|---|---|---|---|
| RouterOS | `/{{ firmware_name }}` | size in bytes (RouterOS has no SHA-256 on the device) | `/system reboot` - RouterOS installs a `.npk` found at boot | `/system package update set channel=…`, `check-for-updates once`, `install` (it reboots by itself); channels long-term, stable, testing, development |
| OpenWrt | `/tmp/{{ firmware_name }}` | SHA-256 (`sha256sum`) | `sysupgrade {{ firmware_path }}` - keeps the configuration, reboots by itself | `owut upgrade -V {{ firmware_version }}` - stable and oldstable from `downloads.openwrt.org/.versions.json`, any major version. `owut` ships on official images for devices with enough flash; elsewhere `apk add owut` (or `opkg install owut`) first |
| Juniper Junos | `/var/tmp/{{ firmware_name }}` | SHA-256 (`file checksum sha-256`) | `request system software add … reboot` | none |
| Cisco IOS-XE | `flash:{{ firmware_name }}` (the device needs `ip scp server enable`) | size (`dir`) - IOS offers MD5 and SHA-512, not SHA-256 | install mode: `install add file … activate commit prompt-level none` - no confirmation, reloads | none |
| Ubiquiti airOS | `/tmp/fwupdate.bin` | size (`wc -c`) | `/sbin/fwupdate -m` - reboots by itself (field reports, not a vendor document) | none |
| Cisco IOS (classic) - a preset | `flash:{{ firmware_name }}` (`ip scp server enable`, and AAA exec authorization for SCP) | size (`dir`) | `configure terminal`, `no boot system`, `boot system flash:…`, `end`, `write memory`, `reload <<< y` | none |
| Huawei VRP | `{{ firmware_name }}` - the root of the default storage (`sftp server enable` or `scp server enable`) | size (`dir`; `52,434,816` is read too) | `startup system-software {{ firmware_name }}`, `reboot fast <<< y` | none |

**Answering the device.** `<<< y` at the end of a command answers every
question that command asks - *Continue? [Y/N]*, *[confirm]*, *[yes/no]* - with
`y`, at most three times; without it a question stops the step. Classic IOS and
Huawei VRP document no reboot that does not ask, so their procedures answer it.

- **Cisco IOS (classic)** is a preset: create a device type from it (Device
  types, New, start from *Cisco IOS (classic)*) and move bundle-mode devices
  (ISR G2, Catalyst 2960) to it. The shipped *Cisco IOS / IOS-XE* type keeps
  the IOS-XE install-mode procedure. `write memory` saves the running
  configuration with the boot variable - unsaved changes are saved too; the
  backup taken before the update holds the configuration as it was.
- **Huawei VRP** keeps the startup software apart from the configuration, so
  nothing is saved: `reboot fast` restarts without saving, and unsaved changes
  are gone after the update (the backup taken before it holds them). Devices
  with two main boards need the software set on both (`startup system-software
  … all` on the models that take it): adapt the procedure. The OS version is
  read as the release (`V200R010C00SPC600`), so a repository file is entered
  with that version.

Sources: Huawei's command references for `startup system-software`, `reboot`
(`reboot fast` does not ask to save, still asks to confirm), `sftp server
enable` and `scp server enable`; Cisco's references for `boot system`, `reload`
(always confirms, also `reload in`) and `ip scp server enable`. Huawei's pages
could only be read in search excerpts; try the procedure on one device first.

- **airOS, Junos, IOS-XE and VRP have no public address** that publishes the
  newest version, so no online update; MikroTik and OpenWrt have one.

Each step runs from the scheduler, one step per device per minute; an upload
must complete within five minutes per scheduler call (a slow link is retried
for up to 40 minutes).
