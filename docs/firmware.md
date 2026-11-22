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

## Updating devices

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
