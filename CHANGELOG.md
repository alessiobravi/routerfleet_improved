# Changelog

Releases are numbered MAJOR.MINOR.PATCH. Every push to `main` is a release: a
push that fixes or maintains raises PATCH (0.11.5 → 0.11.6), a release that
brings a new feature raises MINOR (→ 0.12.0). Each release is tagged
`v<version>`, published as a GitHub Release, and its Docker images are tagged
with it. The release is `ROUTERFLEET_RELEASE` in `routerfleet/settings.py`;
`scripts/version.py bump patch|minor` raises it.

Only the newest release is published: each release deletes the older ones,
with their tags and images. Use `:latest`.

A release that changes how backup rows are written, or the database columns
the Zabbix kit reads, says so under **Monitoring**.

## [0.36.0] - 2026-11-25

### Added
- **Discovery** (Router Manager → Discovered): devices revealed by LLDP, CDP
  and MikroTik MNDP, added in one click - the device form filled in with name
  and address, the device type guessed from the announced platform.
  - **From the neighbour tables of managed devices**: a new system command,
    *neighbours*, read with every router information update: RouterOS (MNDP,
    CDP, LLDP), Cisco CDP and LLDP, Junos LLDP details, Huawei LLDP and CDP,
    OpenWrt where lldpd is installed (LLDP and CDP). airOS lists no
    neighbours, but announces itself by CDP and Ubiquiti discovery. Reaches
    every network they touch; nothing to install.
  - **Discover adjacent devices** listens three minutes on RouterFleet's own
    LAN (MNDP, LLDP, CDP, Ubiquiti discovery), with a listener inside the
    monitoring container, dormant - no socket open - until asked; it sends
    nothing; request and findings pass through a file on a shared volume.
  - Already managed devices are matched; others can be hidden.

- **Discovered devices are checked from RouterFleet's host** (their SSH port,
  in parallel, every ten minutes and on request); a reachable one is added by
  an administrator with the **Add to hosts** wizard, by its IP address: the
  device, signing in (with *Test connection*), backups and monitoring.
- **IPv4 and IPv6 everywhere**: nginx, the application and the console server
  listen on both where the container has IPv6 (IPv4 only where it is switched
  off); `SERVER_ADDRESS` takes an IPv6 address, bare or in brackets; devices
  by IPv6 address (brackets optional); links put IPv6 addresses in brackets;
  discovery reads IPv6 management addresses (never link-local ones) and hears
  MNDP over IPv6; the guard on RouterFleet's own networks knows its IPv6
  networks too; an IPv4 client on a dual-stack socket is recorded by its IPv4
  address.

### Changed
- **The monitoring container runs on the host's network** (every right
  dropped but the raw socket, which ping and the listener need), and reaches
  the application on the host's loopback: port 8001 is published on
  `127.0.0.1` only (`MONITORING_PORT` in `.env` changes it). Take the current
  compose file.

## [0.35.1] - 2026-11-24

### Changed
- **The private key is revealed and edited in the key's own page** -
  administrators only: *Reveal / edit* in the private-key box asks a small sum
  (checked by the server), then shows the key there, editable. Saving a
  replaced key, a new passphrase or its removal asks for another sum; *Cancel*
  keeps the stored key. The separate reveal page and the eye in the list are
  gone. Every reveal is still recorded in the change log.
- A protected key gets a new passphrase, or loses it, without being pasted
  again.

## [0.35.0] - 2026-11-24

### Added
- **SSH keys can have a passphrase** (optional, encrypted at rest). A
  protected key is given with it; an unprotected key given with a passphrase
  is stored protected by it. Keys are checked when saved - they must open -
  and the public key is derived when left empty. A red lock marks protected
  keys in the list.

### Changed
- **Private keys are shown only to administrators, on purpose**: never put
  back into the key form (left empty, the stored key is kept); *Show private
  key* (the eye in the list) asks for a small sum first, shows the key on a
  page of its own, and records it in the change log as *Secret shown*.
- **The sign-in page offers passkeys only where the HTTPS settings allow
  them** - HTTPS, reached by a name, a certificate browsers trust.
- **A backup shows a "Binary backup" tile only when it has a binary file**
  (OpenWrt archives, files a device type downloads); otherwise the place shows
  how long the backup took. RouterOS backups are text only.

## [0.34.0] - 2026-11-24

### Added
- **Password reset by email**: *Forgot your password?* on the sign-in page,
  offered only while the mail server works. A link to the account's email
  address, valid for an hour and once; the page answers the same whether an
  account matches or not, does not sign anybody in, and turns away an address
  asking too often.

### Changed
- **The mail server is checked before it is saved**: *Check and save*
  connects and logs in with the settings first; only settings the server
  accepts are saved, and that switches on email notifications and password
  reset. Refused settings are not saved, and the server's answer is shown (the
  password taken out). A server already switched on counts as checked.
- **Email is greyed out until the mail server works**, with the reason: the
  email switches in My Settings (the choices are kept) and the Email type of a
  channel. The email address stays editable - also where a reset link goes.
- **System settings are for administrators only**, and in the menu only for
  them. Their first section is **Media and messaging**: notification channels,
  the mail server, grouping and daily reports. Configuration Managers still
  start and follow device updates from the router list and the device page;
  the firmware repository itself is managed by administrators.
- **Message Center shows the messages only** - what was sent, to which
  channel, what failed; its settings moved to System settings.

## [0.33.2] - 2026-11-24

### Fixed
- **The console font on a phone held upright is small enough now**: the size
  is chosen by measuring the columns that really fit with the browser's own
  monospace font - phones' fonts are wider than the estimate 0.33.1 used - and
  goes down until about 72 columns fit (never under 7 px). Landscape keeps
  14 px.

## [0.33.1] - 2026-11-24

### Changed
- **Phones get denser rows**: less padding, smaller icons and slightly smaller
  text, so titles stay on one line - checked at phone width on every settings
  page, with no page wider than the screen. Titles that were too long are
  shorter (the HTTPS modes, the router address links, system commands without
  their "System:" prefix, a backup-profile switch); the full wording is in the
  line beneath or the tooltip.
- **The device console on a phone held upright** uses a smaller font, so a
  line of device output fits (about 60 columns, never under 8 px); turned to
  landscape, and on larger screens, it keeps 14 px. Turning the phone
  switches at once, and the device is told the new terminal size.

## [0.33.0] - 2026-11-24

### Changed
- **One look for list pages**: each list in a card - a title bar on top, tabs
  underlined, the column names in bold. The choice between two looks is gone
  from My Settings.
- **Adding something is the same everywhere**: the last row of its list - a
  coloured "+" with what it adds and what that means - for users, commands,
  device types, firmware and channels, instead of buttons beside the titles.
- **Colours that identify**: every section of the menu has a tone, and its
  icons carry it - Status sky, Router Manager blue, Backup teal, Fleet
  Commander green, User Manager rose, Message Center orange, Tools indigo,
  System settings purple, Audit Log amber, My Settings cyan - also in the
  menu itself. Grey icons are gone; meanings keep their colour (green up to
  date, amber to be set up, red locked or failing).
- **System settings in collapsible sections**, like My Settings, each with a
  coloured badge and what it holds.
- **My Settings**: the account - username and email - on top.
- **The message filter** is the first row of the message list.
- **The database on Tools**, smaller and clearer: its size on one row, the
  VACUUM FULL switch with a one-line summary (the details open on demand) and
  "Optimize now" as a row of the same group.

## [0.32.0] - 2026-11-24

### Added
- **Change your own password** in *My Settings → Security settings →
  Password*: the current one first, then the new one twice, checked against
  the password rules. Other devices signed in with the account sign in again.
- **Start an update from the device page**: next to the available OS version
  (orange) and the bootloader firmware that can be installed (purple), an arrow
  opens that update - for Configuration Managers and Administrators.

### Changed
- **My Settings in collapsible sections**, closed at first (what you open is
  remembered while you browse): *Security settings* first - authenticator
  app, password, passkeys, session expiration time - then the account,
  *Visualization options* (light or dark, list pages), *Router address links*
  and *Email notifications*. Passkeys are shown greyed out, with the reason,
  where the browser can not use them.
- **The Grouped list style is clearly its own**: tabs as a switch, a tinted
  header band and airy rows; both styles are shown small where you choose.
- Fleet Commander's **System commands** are a collapsible section, closed at
  first, with red locks.
- **Optimizing the database says what it did**: a plain PostgreSQL VACUUM
  makes deleted space reusable but does not shrink the files (its first run
  even adds small maps of free space), so it is no longer reported as "from X
  to Y". The VACUUM FULL option explains what it does, what waits meanwhile,
  what follows and when it is worth it.

## [0.31.0] - 2026-11-24

### Added
- **How long a sign-in lasts is each user's choice** (*My Settings →
  Security settings → Session expiration time*): 1 hour to 2 weeks, **24 hours by default**, from the last use.
  Pages refreshing by themselves and the scheduler's calls do not count, so a
  tab left open does not keep a sign-in alive. Until now every sign-in lasted
  two weeks from signing in.
- **Two styles for the list pages** (*My Settings → Visualization options*): **Cards** - a
  title bar on each list - or **Grouped** - a section title and a rounded
  group, like the settings pages. Each user chooses; the choice follows them to every device.

## [0.30.0] - 2026-11-24

### Added
- **Firmware updates for Huawei VRP**: the shipped type gets its procedure
  where still unset - `startup system-software`, then `reboot fast` with its
  confirmation answered; the upload checked by its size. Its OS version is now
  read as the release (`V200R010C00SPC600`) instead of the VRP number many
  releases share.
- **A Cisco IOS (classic) preset** for bundle-mode devices (ISR G2, Catalyst
  2960): the boot variable, `write memory`, `reload`. Create a device type from
  it; the shipped Cisco type keeps the IOS-XE install mode.
- **Commands can answer the device**: `reboot fast <<< y` answers each
  question that command asks (*[Y/N]*, *[confirm]*, *[yes/no]*), at most three.
- **The device form suggests a dedicated account** with the least rights
  RouterFleet needs, linking per-vendor examples (Devices, "A dedicated
  account").
- CI also starts the SQLite variant and boots the whole stack on arm64.

### Changed
- **Forms in the settings layout** (grouped rows, switches, titled sections):
  the device form, backup profile editor, schedule defaults, Fleet Commander
  commands, variants and schedules, groups, SSH keys, the import tool and the
  HTTPS page. Job history shows the state icons; a protected system command
  shows the red lock.
- **Sign-in lockout per account and address**: failing on purpose from one
  address no longer locks the owner out elsewhere. 5 failures for an account
  from one address lock it there; 5 from one address lock the address; 20 for
  an account from all addresses together lock it everywhere.
- **Passkey sign-in stores nothing until it succeeds**: the challenge is in a
  signed five-minute cookie instead of a session row per request, and stays
  single-use.

### Fixed
- **Versions are compared the way vendors number them**: Huawei
  `V200R010C00SPC600` / `SPC700` and Cisco `15.5(2)T1` / `15.5(3)M` compared as
  equal - a newer file was not offered, and a device could count as updated
  while still on the old release. Pre-releases rank below the release
  (7.21beta3 < 7.21rc1 < 7.21).
- The size check of an uploaded file failed on Cisco `dir` output (the date
  after the size ran into it) and did not read `52,434,816`.
- The test suite runs in about half the time (a fast password hasher in tests
  only).

## [0.29.0] - 2026-11-24

### Changed
- **States are shown as icons**: a green tick when all is well, the warning
  sign in yellow (worth a look), orange (needs attention soon) or red (act
  now), grey for information. On the status page the icon replaces the
  *OK* / *Look* / *Act* tags (pointing at it says it in words, as do screen
  readers); on object pages and in state badges it stands beside the words.
  Backup storage turns yellow from 75 %, orange from 85 %, red from 90 %;
  unmonitored devices are worth a look (yellow).

### Performance
- **Pages appear faster, the first one after signing in above all.** Static
  files carry the hash of their content in their names, so browsers keep them
  for a year and never run an old one after an update; nginx sends them
  compressed (pages themselves never, as they carry the CSRF token). The first
  page after signing in loaded about 1 MB of scripts and styles uncompressed,
  and pages asked for them again.
- The application serves several requests per worker on PostgreSQL (5 workers,
  4 threads each): the scheduler's work with devices, which can take up to a
  minute per job, no longer holds up a page clicked meanwhile. SQLite keeps one
  request per worker.
- The status page reckons the size of the backups at most every 10 minutes
  instead of reading every backup file at every load.
- The application's log shows how long each request took (Operations, "Pages
  are slow").

## [0.28.0] - 2026-11-24

### Changed
- **The move to PostgreSQL 18 happens by itself.** With the current compose
  file, `docker compose pull && docker compose up -d` is all: at its first
  start the new database finds the PostgreSQL 16 database beside it and moves
  it - a copy opened with PostgreSQL 16 (now carried in the database image),
  export, import of the database and its other roles, every table and the
  roles compared, the built-in ordering checked - and only then starts. The
  old volume is never changed and stays the way back. A move that does not
  complete keeps the database from starting, never on part of the data, and
  the log says why and how to try again. Free disk space is checked first.
- `RF_AUTO_MIGRATE=0` keeps the earlier behaviour: the database waits for
  `scripts/migrate-database.sh`, run at a moment you choose.
- The first start of the database may take up to 15 minutes before it counts
  as unhealthy (it moves the old database meanwhile).

## [0.27.2] - 2026-11-24

### Fixed
- **A first update to the current compose file is guided end to end**, after a
  field report where it took several tries:
  - the database's message, when it waits for the migration, lists every
    command - creating the `scripts` folder, downloading the script, running it
    on the host - and the ports to set when another web server holds 80 or 443;
  - the migration script checks the web ports **before** it changes anything,
    and stops with the `.env` line to add when another program holds them;
  - if RouterFleet does not start after a migration that succeeded, the script
    says the database is moved and what to fix;
  - the Updates page, while the database is still PostgreSQL 16, shows the
    commands before anybody takes the new compose file.
- Two checks of the CI migration test could never fail (a negated command does
  not stop a bash step); they test explicitly now.

## [0.27.1] - 2026-11-24

### Changed
- **"Update not checked" says why.** When a device's update check gives no
  version, what the device said is kept and shown - for RouterOS the status of
  its own check, such as `ERROR: could not resolve dns name` - in the version's
  tooltip and on the device page (inventory field *Update check status*).
- **Such a device is checked again after an hour** instead of a day.

## [0.27.0] - 2026-11-23

### Added
- **The rollout page is a timeline**, like a device page: the update and its
  progress, a tile per state, every device with its step, and every step of
  every device - the backup, the upload or download, the install, and after
  each reboot the reachability checks: gone, back over SSH with the version it
  reports, back on the new version. It updates itself.
- **A notification when a firmware update finishes**: a device back online on
  its new version, or an update that failed (event *Firmware update finished*,
  off for existing channels).
- **MikroTik bootloader firmware (RouterBOOT), as a process of its own**:
  a purple arrow in the router list when the device could install a newer
  bootloader firmware (the amber arrow stays the system update), *Update
  bootloader* on the device page and for selected hosts, its own page and
  rollout. The inventory reads the firmware the device can install.
- **Firmware procedures for more platforms**, from the vendors' documentation:
  OpenWrt online updates with `owut` (stable and oldstable channels, across
  major versions), Juniper Junos, Cisco IOS-XE install mode, Ubiquiti airOS.
  Classic IOS and Huawei VRP are not shipped: their reboot always asks for a
  confirmation. Channel lines can carry their own pattern, and a device type
  says whether a newer major version is offered.

### Changed
- A lock shows as a red lock icon - the job page, the router list's lock
  columns - instead of a "locked" tag.

### Fixed
- The rollout page did not update itself while the update ran.

## [0.26.0] - 2026-11-23

### Added
- **Update devices that have an update.** The amber arrow in the router list,
  *Update* on the device page and *Update firmware* for selected hosts open the
  options each device can take, in order: a file of the firmware repository made
  for it (type, model, architecture), newest first; then the device's own online
  update, with the newest version of **every release channel** - for RouterOS
  long-term, stable, testing and development. Only newer versions of the same
  major version are offered. The update runs as a rollout: a backup first, the
  update, the device back on exactly the version offered; one device first,
  then the rest.
- **Online updates are configuration**: each device type lists its release
  channels and where each publishes its newest version, and the system command
  *firmware - online update* says how the device installs it. RouterOS ships
  with both; other types can be given them.

## [0.25.2] - 2026-11-23

### Fixed
- **A refused backup could still count as successful.** 0.24.2 recognised a
  stored refusal by RouterOS's error text; field reports show one whose stored
  text was not that line, while its console recorded the device's exit code 1.
  On upgrade, a successful backup whose console shows a non-zero exit code for
  a command of its first phase becomes a failed one, with the reason (backups
  made from 0.24.2 on are failed at once).
- **Outages left open for disabled or unmonitored hosts end on upgrade.** 0.25.0
  ended them only for hosts disabled from then on. Each ends when its host was
  disabled, or where it began when that is not known - counting it to the
  upgrade would invent downtime.
- **An outage ended by disabling a host counted as no downtime at all**: its
  length was left at 0 (0.25.0). It is now counted.

### Documentation
- **Zabbix kit**: how to fetch it onto the Docker host (it is in the
  repository, not in the images); that the move to PostgreSQL 18 carries its
  role from 0.25.1 on, and a move made before needs the role's SQL again; and
  not to link Zabbix's generic PostgreSQL templates to the kit's role (they need
  `pg_monitor` and fill the database log with permission errors without it).

### Maintenance
- The CI migration test also checks that a role's `pg_monitor` membership comes
  along (`pg_ls_waldir()` readable after the move).
- A test checks that every column the kit's role is granted exists in the
  database, so a change that would break the kit's SQL is caught before a
  release.

## [0.25.1] - 2026-11-23

### Fixed
- **The move to PostgreSQL 18 carries other database roles along**: a role
  such as the Zabbix kit's `zbx_monitor` lives beside the database, not in its
  export, so the import stopped at its grants. The script now exports the roles
  (`pg_dumpall --roles-only`, password hashes included), creates them in the
  new database before the import, and compares them afterwards. CI migrates a
  database with the kit's role and checks it signs in and reads only what it
  was granted.
- The CI migration test uses random passwords for every run instead of fixed
  ones written in the workflow.

## [0.25.0] - 2026-11-23

### Added
- **Quick actions** in the router list: the column has a name, and **Back up
  now** starts an instant backup of that device without leaving the list.
- **Enable / Disable the selected hosts** in the router list (level 30). A
  disabled host is neither backed up nor monitored.

### Changed
- **Source data** on the device page is readable: each command with its
  output, real line breaks, in a scrollable box - instead of one line of JSON.

### Fixed
- **A disabled or unmonitored host could still be marked offline**: a status
  report already on its way opened an outage and sent a notification. It is
  ignored now, and disabling a host or switching its monitoring off ends an
  outage that was open, so it does not count as downtime for ever.

## [0.24.3] - 2026-11-23

### Maintenance
- **Every released migration is fingerprinted.** The test that keeps released
  migrations from being edited - what keeps databases of every earlier release
  upgradable - covered 91 of 121: the 30 added from 0.16 to 0.24.2 were not
  pinned. All are now, and a test fails if any migration lacks its
  fingerprint. Nothing changes for installations.

## [0.24.2] - 2026-11-23

### Fixed
- **RouterOS 6 backups failed** with *expected end of command (line 1 column
  10)*: *Include passwords and keys* added `show-sensitive`, a RouterOS 7
  word. Device types now hold the export words per OS version: RouterOS 7 is
  sent `show-sensitive` as before; RouterOS 6 is sent nothing to include
  passwords and keys, and `hide-sensitive` to leave them out.
- **Security: RouterOS 6 backups contained passwords and keys** even with
  *Include passwords and keys* off - RouterOS 6 exports them unless told
  `hide-sensitive`, which it was not. Backups taken before this release keep
  what they contain.
- **A refused command was stored as the backup.** The device's exit code was
  ignored, so its error message became the "configuration" of a successful
  backup. A refused command now fails the backup with the device's message, and
  it is retried. Backups already stored that way become failed ones on upgrade
  (only texts that are a single RouterOS command error are touched).
- **Device details say what a device did not report** instead of leaving the
  row empty: RouterOS on x86 or CHR has no RouterBOARD, so no model version,
  serial or firmware - the row now reads *not reported by the device*.
- The label *Architecture* was misspelled on the device page.
- RouterOS 6.49 (long-term, x86) is covered by the tests with real output:
  versions are read and the update offered.

## [0.24.1] - 2026-11-23

### Fixed
- **The upgrade guide gives the two ways explicitly**: keep your compose file
  (only the images change; the database stays on PostgreSQL 16), or take the
  current one and move the database with the migration script. Before, the
  general steps took the new compose file without saying that a PostgreSQL 16
  installation then needs the script - the database refused to start until it
  ran (no data was ever at risk).

## [0.24.0] - 2026-11-23

### Changed
- **The bundled database is PostgreSQL 18** on Alpine (`routerfleet-postgres18`),
  with the built-in `C.UTF-8` ordering, so text ordering never depends on the C
  library again. Existing installations move by export and re-import with
  `scripts/migrate-database.sh` - the old volume is never touched, and the new
  image refuses to start empty beside it. Compose files from before keep the
  PostgreSQL 16 image, still published, until they migrate.
- **The interface runs on AdminLTE 4 and Bootstrap 5.3**, replacing AdminLTE 3
  and Bootstrap 4, which no longer receives fixes. Pages look as before - same
  layout, colours, switches and dark mode, compared page by page - but no page
  loads jQuery any more: DataTables 3, Tom Select (in place of Chosen) and Font
  Awesome 7, all bundled. Page scripts and styles moved into static files.

### Security
- **A strict Content-Security-Policy on every page.** Only the installation's
  own scripts, styles and fonts load; no page carries inline script, inline
  handlers or inline styles any more, so markup that ever slipped past the
  escaping could not run. Values reach the page scripts as escaped attributes
  instead of being written into JavaScript. The device console alone allows
  inline style elements, which its terminal library needs
  ([Security](docs/security.md#response-headers)).

### Added
- **The ports are settings**: `HTTP_PORT` and `HTTPS_PORT` in `.env` (80 and 443
  by default), for a host where those are already taken - by another web
  server, for example. The compose file stays as shipped, and the HTTP
  redirect goes to the HTTPS port you chose instead of 443.

### Fixed
- **Export as CSV** in the router list works again: it relied on a table
  extension the pages never loaded. The file is written in the browser, and a
  cell that starts like a spreadsheet formula is written as text.
- The release notes of an update are shown as text, never as markup.
- **Forms work behind a published port other than 443** (`8443:443`): nginx
  passed the address on without its port, so Django saw `10.0.0.5` while the
  browser came from `https://10.0.0.5:8443`, and every form - the first one
  after creating the administrator - failed with *CSRF verification failed*.
  The device console was refused the same way. nginx now passes the address as
  the browser sent it, in every HTTPS mode; `SERVER_ADDRESS` needs no port.
- **`HTTPS_MODE_OVERRIDE` and `LETSENCRYPT_STAGING` in `.env` work.** They
  were documented since 0.16.0 but never reached the nginx container, so the
  emergency override and Let's Encrypt's test service did nothing.
- **`.env.example` explains every setting**, grouped (address and ports, HTTPS,
  database, general), with the optional ones commented out at their defaults.
  A test keeps it, the documentation and the compose files in agreement.
- A first installation no longer warns that `POSTGRES_HOST` and `POSTGRES_PORT`
  are not set: with the bundled database they are meant to be empty, and the
  compose files now say so. Nothing changes in how RouterFleet starts.

## [0.23.1] - 2026-11-23

### Security
- **No container has Docker access any more.** The in-app updater ran in the
  scheduler container with the Docker socket - root on the host: anyone running
  code in that container would own the machine. It is removed: the compose
  files mount the socket nowhere, and the scheduler image is back to cron and
  curl (no Docker client, no cosign, no updater script).

### Removed
- **Updates from System settings.** The Updates page keeps the running and the
  newest release, *Check now*, and the command to update on the host. Release
  images stay signed, and docs/updating.md shows how to verify one. An
  installation with the updater set up should drop its socket and update
  volumes from the compose file (docs/updating.md).

## [0.23.0] - 2026-11-23

### Added
- **Check for updates now** (System settings, Updates): asks the update source
  at once instead of waiting for the hourly check, and says what it found -
  update available, up to date, or that the source did not answer (the previous
  answer then stands). Administrators only; once every 30 seconds at most, so
  the button can not flood GitHub; follows UPDATE_CHECK and UPDATE_CHECK_URL.
  The page shows when the last check ran.

- **Database maintenance** (Tools, Database): the database's size, and
  *Optimize* - VACUUM and fresh statistics; on PostgreSQL optionally VACUUM
  FULL to give the space back to the disk. Also `manage.py optimize_database`.
  Statistics are refreshed after every backup clean-up.

- **An update mark in the device list**: an amber arrow after the operations
  of every device with a newer version available - by the same rule as the
  version colours and the status page's counts - whichever columns are shown;
  hovering it names both versions. The operations sit in fixed slots, so every
  icon is at the same place on every row, even where one does not apply.
- **Delete binary backups** (Tools, Backup clean-up): the stored binary
  files of chosen device types - RouterOS `.backup` by default - with their
  count and size shown first; every text backup stays. Confirmed with a sum,
  one entry in the change log.
- **Name, surname and phone** in a user's details (User Manager), shown in the
  user list; the phone is optional, checked, and in the change log.

### Changed
- **RouterOS backups are text only**: the `/export` output, nothing written on
  the device and nothing downloaded - the binary `.backup` is no longer taken.
  Installations whose RouterOS backup commands are as shipped are converted;
  edited ones keep theirs. Backups already stored keep their files until you
  delete them (above). A backup with nothing to download now finishes in one
  step instead of waiting for a second one.
- **Tools, User Manager, Message Center and Fleet Commander** use the layout of
  System settings. The message history can be filtered; grouping and daily
  reports explain each setting beside it; the user list and editor show each
  user's level and second factors.

### Fixed
- **The backup list was slow**: every row fetched its device with a query of
  its own and read the full configuration text and console log it never
  shows, for every backup ever taken. It now reads only the listed columns,
  with the device, in a fixed number of queries; shows the newest 500 (older
  ones by day, or "show all"); and the backup tables have indexes for the
  list, the queue, a device's history and retention. On 3,000 backups: from
  3,014 queries and 0.8 s to 15 queries and 0.04 s. A device's backup page no
  longer reads every earlier configuration text either.
- **Deleting duplicates was slow**: every duplicate was saved on its own, with
  its change detection and change-log work - 6,001 queries for 3,000. They are
  now freed in bulk (21 queries, 0.09 s), and the clean-up page no longer reads
  every configuration text to measure it.
- **Backup rows no longer load their configuration texts and console log**
  unless the code reads them: lists, counts, the scheduler and change detection
  stay light whatever the size of the configurations.
- **Indexes** for the message queue and history, notifications, the change log
  by object, sign-ins by user, downtime by device, and open console sessions.
- **Dark mode**: command payloads on a command's page were light text on a
  light background.

### Security
- **Try on a device** (device types) answers with a fixed sentence per kind of
  failure - refused login, changed host key, no prompt, no answer - and never
  with the error text itself; the cause goes to the log. Reported by CodeQL,
  with two smaller findings: a log line now quotes the device type it names,
  and the pager pattern is written so it is not mistaken for markup.
- **Scheduler image**: cosign is built with the fixed releases of the libraries
  that carried known vulnerabilities (go-chi, OpenTelemetry, x/crypto, x/net),
  and without a version stamp a scanner reads as an old cosign release. The
  four medium and unknown findings of 0.21.3 are gone.
- **Database image**: the GnuPG tools the official postgres image keeps after
  verifying its packages are removed - PostgreSQL never uses them - taking
  their findings (dirmngr, SQLite) with them. The image stays on Debian, so
  existing databases keep their text ordering; Debian's remaining findings have
  no published fix yet and are installed with every release as soon as they do.

### Documentation
- Every guide reflects what the release does: device types and the firmware
  repository in the README, the permission table, security (device types,
  firmware uploads and rollouts, outbound connections), the architecture
  (scheduler entries, data model, volumes), installation and devices.

## [0.22.0] - 2026-11-23

### Added
- **Device types are configuration** (System settings, Device types). How
  RouterFleet talks to a kind of device - one SSH command per channel or an
  interactive shell with a prompt, paging off, an enable step - how it
  recognises it, where each inventory value is found, which backups it takes,
  how it learns which version the device could run, how firmware is installed,
  and which links the device list offers: all edited per type, nothing written
  into the code. The shipped types can be edited and restored.
- **A wizard for new device types**, starting empty or from a shipped type,
  ending with **Try it**: the connection test, the inventory and the text
  backups of the form run against a device, showing what each found - before
  anything is saved.
- **Cisco IOS / IOS-XE, Juniper Junos and Huawei VRP**, through an interactive
  shell: `show running-config`; `show configuration` and its set format;
  `display current-configuration`. An optional enable password per device,
  encrypted and sent only to the stored address.
- **Text backups from command output**, nothing written on the device, with
  **several named texts** per backup where the device type defines them - each
  shown, compared and downloaded on its own, all counted for change detection.
  Lines the type marks as noise (timestamps) never count as a change.
- **Update checks modelled per device type**: the device itself, the vendor's
  release list (https only, an optional confirmation address, cached), and the
  firmware repository - the newest of them counts. A type with none shows "no
  update check configured" instead of a guess.
- **Firmware repository** (System settings): firmware per device type, limited
  to models and an architecture, SHA-256 computed and checked against the
  vendor's when given. **Rollouts**: a fresh backup of each device, upload by
  SCP, the upload verified on the device (SHA-256 or size) before anything is
  installed, the install, and the device must come back reporting the new
  version. One device goes first and alone, the others follow a few at a time;
  any failure stops the rest. RouterOS and OpenWrt procedures ship; the others
  are defined per platform.

### Changed
- RouterOS backups take the text from `/export` output instead of an exported
  file, where the backup commands were not edited (edited ones keep exactly what
  they did). OpenWrt's first "create" line becomes the text command.
- The Winbox link in the device list is a link of the RouterOS device type;
  other types can have their own.

### Removed
- **Mikrotik (Branded)**: its devices become Mikrotik (RouterOS) - the commands
  were the same; only the connection test told them apart. Fleet Commander
  variants for it fold into the RouterOS ones.

### Fixed
- The devices chart test depended on the time of day it ran at.

## [0.21.3] - 2026-11-22

### Fixed
- **The menu needed two taps on a phone** after the sidebar had been left
  collapsed: the first tap was spent on the hover that shows the labels. On
  touch screens the menu no longer expands on hover; one tap opens the page.

## [0.21.2] - 2026-11-22

### Changed
- **Comparing backups on a phone**: a new *Changes* view - every changed part
  in one column, long lines wrapped, removed lines red and added green, with
  old and new line numbers and a count of what changed. Phones open on it;
  wider screens keep side by side. *Side by side* and *Inline* are readable in
  dark mode.
- **The device's OS version card shows the version** it runs, and beside it
  whether it is up to date or which version is available - instead of only
  "Update waiting".

## [0.21.1] - 2026-11-22

### Changed
- **Lists on a phone**: the name, the selection and the quick actions stay on
  each row, side by side, instead of folding behind a "+"; columns that do not
  fit are left out (the name opens everything). The device list shows each
  device's status next to its name.

### Security
- 0.21.0 was stopped by the release gate: the cosign binary in the scheduler
  image bundled a `golang.org/x/net` with a known high-severity vulnerability.
  The image now builds cosign from its pinned source with the fixed library.
- The update request file is written owner-only. Reported by CodeQL.

## [0.21.0] - 2026-11-22

### Added
- **Updates from System settings.** An administrator approves the update with
  their password; the scheduler's updater installs it: only a newer release,
  only if every image is signed by the RouterFleet Improved release workflow
  (Sigstore, checked with cosign), after a database backup, and with the
  previous version and the backup put back if the new one does not start
  healthy. The web application has no Docker access. Needs the current compose
  file once; older ones keep updating by command. See docs/updating.md.
- Release images are signed in CI; their signatures are kept when older images
  are deleted.

- **Unchanged backups per device**: *Unchanged backups* in a device's settings
  follows its backup profile (as every device does after upgrading), or keeps
  every backup in full, or stores only changes - for that device alone. The
  backup clean-up follows the same decision.
- **Backup profiles explain themselves**: what daily, weekly and monthly
  backups are for, and that retention is how many days each is kept.
  *Retain backups on error* is now *Keep old backups while backups are
  failing*, with what it does.

- **Confirming with a small sum**: deleting, aborting, cleaning up and
  removing users ask how much two small numbers add up to, instead of typing a
  word. The server checks the confirmation as before.

### Fixed
- **Editing a user saved nothing but the level**: an email added in User
  Manager - or any change without a new password - was reported as saved but
  dropped. The password is left untouched.
- **Backup clean-up said "Nothing to clean" with thousands of duplicates**: it
  respects profiles that keep every backup, but did not say so. It now shows
  how many duplicates it left alone and how to clean them, and names each
  device's backup profile (with a link), so it is clear which profile decides.
- **Backup profile export options** are named and explained: *Include
  passwords and keys* (`show-sensitive`) and *One line per item* (`terse`).
- System settings mentions where unchanged backups are decided (the backup
  profile).

## [0.20.0] - 2026-11-22

### Added
- **System settings in sections**: Messaging, Display, Backups and jobs,
  Security - each setting of the installation in one place.
- **Default rows per table** (Display): 10, 25, 50 or 100 - what every list
  opens with, for everyone. Upgraded installations keep 10, as before; each
  list still offers the other sizes.

### Fixed
- The change log now records a channel's ntfy server, priority and the
  newer events (backup succeeded, Fleet Commander job with failures) when
  they change.

## [0.19.0] - 2026-11-22

### Added
- **Console: a way back when the connection closes.** *Connection closed*
  appears over the terminal with the reason, a button back to the devices list
  and one to the device; the output can be kept on screen to read or copy.
- **Devices remember when they were enabled or disabled**, on every save,
  single or bulk. Upgrading fills it in from the change log; a device already
  disabled with no record counts as off since its last change.

### Fixed
- **The devices chart showed wrong figures.** Online counted only devices whose
  status had changed before that day, so the line climbed as devices merely
  changed state; a device that changed today vanished from earlier days; and
  disabled devices were dated from change-log entries a bulk edit does not
  write per device, so a bulk disable appeared all on one day. Each day now
  counts each device once, from the day it was added: disabled from when it was
  switched off, offline during the offline periods the monitoring recorded,
  online otherwise.
- **Dark mode: command payloads and output were unreadable** (dark text on a
  dark background), as were other blocks styled for a light page.

## [0.18.1] - 2026-11-22

### Security
- **The old channel address carries over only a valid identifier**: the
  redirect from Message Center to System Settings passed the `uuid` value on
  as received; it now passes it only as a parsed UUID. Reported by CodeQL.

## [0.18.0] - 2026-11-22

### Changed
- **Notifications are configured in System Settings.** System Settings opens
  with HTTPS and Notifications; Notifications holds the mail server, the
  channels - email, ntfy, Telegram - each in its own editor, grouping and the
  daily reports, and the way to the message history, which stays in Message
  Center. The old channel and email pages lead there.
- **The channel editor explains each setting** for each type, with where the
  value comes from and realistic examples. A stored token is never shown again;
  leaving it empty keeps it.

### Added
- **ntfy with your own server**: server URL (the public ntfy.sh by default),
  an optional access token for protected topics - encrypted, and only ever sent
  over HTTPS - and a priority from 1 to 5. A server on RouterFleet's own
  internal networks is refused. Existing ntfy channels keep ntfy.sh.

### Removed
- **CallMeBot (WhatsApp) is no longer supported.** Upgrading switches existing
  CallMeBot channels off and keeps them, with their message history; System
  Settings lists them so they can be recreated as email, ntfy or Telegram.
  Messages still queued for them fail with that reason.

## [0.17.1] - 2026-11-22

### Added
- **Device console: a terminal on any device, in the browser.** SSH - the
  device's stored host key checked first - or Telnet where a device has nothing
  else, with the device's backup account (its secret never reaches the
  browser) or another account (the password goes once, encrypted, to the
  device login; never stored or logged). On a phone, with a bar of Esc, Tab,
  Ctrl-C, Ctrl-D and arrows. For Host Manager and above, from the device page
  or the device list. Security: a one-time ticket valid for 60 seconds, never
  in a URL and stored only as a hash; WebSockets refused from other sites and
  without a signed-in session; the level checked again every few seconds;
  addresses on RouterFleet's own internal networks, loopback and link-local
  refused, so a device address can not reach the database or the application;
  Telnet off per device until switched on, and no stored credential ever sent
  over it; one console per device, three per user. It closes at once when the
  page is closed or left, with Disconnect, or when the device ends it; within
  seconds of signing out; within about 40 seconds of a lost browser; after 15
  minutes idle or 4 hours. Each session is in the change log - who, the device,
  the account and when, never what was typed - and in the device's timeline.
  Served by uvicorn beside gunicorn inside the application container; nginx
  passes `/console/ws/` in every HTTPS mode. CI drives a real session to an
  SSH server through nginx on every push.
- **Email notifications.** Each user sets their address in My Settings and
  chooses what to receive: backup failed, backup succeeded (grouped), the daily
  backup and status reports, device offline and back online, and Fleet
  Commander jobs with failures. The mail server - host, port, STARTTLS, SSL/TLS
  or none, an optional login (the password encrypted, never shown again),
  sender address and name, a test email - is set by administrators in Message
  Center, Email. A shared mailbox is a channel of type Email. Administrators can
  set users' addresses in user management.
- **Two new notification events for every channel type**: backup succeeded
  (grouped like failures) and Fleet Commander job with failures. **Off for
  channels that existed**, so an upgrade sends nothing new.
- **Light and dark mode**: the moon in the top bar, or Automatic (the default,
  following the device), Light or Dark in My Settings; drawn in its theme from
  the start, without a white flash.

### Changed
- **Object pages**: the timeline is one vertical column beside the details,
  kept in view while scrolling; the detail panels start collapsed and stay as
  opened, also through live updates; events of the same minute keep their
  order.
- **The Live pill names what is running** ("Live: job running"); the status
  page refreshes quietly, without it.
- **My Settings in the style of a phone's settings**: grouped rows, the router
  address choice saved on tap, passkeys as rows, appearance and email
  notifications.
- **Devices status is a line graph again.**
- **The device form** has *Allow a Telnet console (unencrypted)* and the Telnet
  port.

### Fixed
- **"configuration Manager" is "Configuration Manager".**
- **The menu's icons and labels are aligned** (Backup's label sat further left).

### Monitoring
- No change to how backup rows are written. The application container also
  listens on port 8002 internally, for the console; nothing new is published.

## [0.16.3] - 2026-11-22

### Security
- **Files written to the certificate volume are readable by their owner
  only** (0600): the HTTPS settings and an uploaded certificate's chain, like
  its private key already was. Reported by CodeQL.

## [0.16.2] - 2026-11-22

### Added
- **System settings → HTTPS**, for administrators: a Let's Encrypt
  certificate obtained and renewed automatically (HTTP-01), a certificate you
  provide (checked before use: format, matching key, expiry, domain, trusted by
  browsers or not), a generated self-signed certificate, or HTTPS off - behind
  a reverse proxy that serves HTTPS, or plain HTTP. The page shows what nginx
  applied, the certificate's expiry and whether passkeys can work (HTTPS, a
  domain name, a trusted certificate), and warns before a change that would
  stop them for users who have one. Switching HTTPS off takes a red button and
  a sum to solve in a popup. nginx renders its configuration from fixed
  templates, tests it and reloads; a configuration it refuses is never used.
  The private key of an uploaded certificate never reaches the database.
  `manage.py https reset` and `HTTPS_MODE_OVERRIDE` in `.env` are the way back
  if a change locks you out. **On upgrade nothing changes**: HTTPS follows
  `HTTPS_REDIRECT_POLICY`, and the certificate already deployed stays.

### Changed
- **Object pages in the "Hero & timeline" layout**: a Fleet Commander job, a
  task, a device and a backup open with a hero - a progress ring (job
  progress, device availability over 7 days, backup phase) or a state icon,
  the name, a state chip, key facts and the actions - four tiles (a job:
  succeeded, running, waiting, failed; a device: last backup, failed backups,
  OS version, downtime; a backup: size, binary, change, retries), and a
  timeline of what happened. The details follow below. They update live.
- **"Devices online" is "Devices status"**: stacked by day - online (green),
  offline but enabled (red), disabled (purple, from the day the change log says
  the device was switched off). A part opens those devices.
- **On/off settings are iPhone-style switches** throughout the interface.
- **The logo on the project's GitHub page is 30% larger** (125 pixels, was 96).
- **A new sign-in page**: a central box on a dark navy background, in the
  colours of the logo, with the logo, the name and a short tagline at the top
  inside the box, and "RouterFleet Improved by Alessio Bravi" beneath it. The
  second-factor, first-administrator and signed-out pages share it. Fields
  carry labels for screen readers and the browser's autocomplete hints, the
  username field is focused on arrival, and the box fits a phone screen.
- **Notifications are a balloon at the top centre of the page**, gone after 5
  seconds - a bar shows the time left, and pauses while the pointer rests on
  it - or on a click. Several stack. They show text only: a device name in a
  notification can no longer insert markup, which the side toasts allowed.

- **Pages update themselves while something on them is running**: a
  Fleet Commander job (with a progress bar: tasks finished out of the total,
  and how many failed) and its tasks, the job list, a backup and the backup
  list, and a device's page while a backup, a command or an information read is
  under way. A "Live" pill shows while it updates; it stops by itself once
  everything has finished, pauses while the tab is hidden, never replaces a
  field being typed in, and stops on an expired session. The status page's
  cards refresh every 30 seconds.

### Monitoring
- No change to how backup rows are written. The compose files now also mount
  the certificate volume in the application container.

### Fixed
- **Two menu entries lit up at once** on a page whose address contains another
  section's name (a device's command page showed Router Manager and Fleet
  Commander). The menu now goes by the start of the address.
- **Saving a router said "it may take a few minutes until monitoring starts"
  whatever was changed**, also when monitoring had just been switched off. The
  message now says what the save means: monitoring off, router disabled,
  monitoring starting, or simply saved.
- **Error messages showed without their red colour** (Django's `error` tag had
  no matching style).

## [0.15.3] - 2026-11-22

### Fixed
- Wording of the 0.15.2 entry and of a test, which named the earlier projects
  outside the places the project's attribution policy allows.

## [0.15.2] - 2026-11-22

### Fixed
- **The upgrade guide's migration counts** for the two earlier projects it
  covers were each two short (11 and 24, not 9 and 22): the two
  system-command migrations of 0.15.0 were missing. Upgrades themselves
  were unaffected.

### Added
- **The upgrade rule is enforced by the test suite**: every released migration -
  all of the predecessors' among them - is pinned by its SHA-256, and a
  migration that is edited, renamed or removed fails the build. The counts in
  the upgrade guide are computed from the real migration plan and checked.

## [0.15.1] - 2026-11-22

### Monitoring
- **Backup rows are a documented contract for integrations** (Backups, "A
  contract for integrations"): every attempt is a row; a backup that kept only
  changes is still a success with its finish time; a failed attempt has no
  finish time; the latest attempt is the highest `id`. Tests enforce it. Future
  changes to it, or to the columns the Zabbix kit reads, are announced under
  this heading. Nothing in how rows are written changed in this release.

### Changed
- **Zabbix kit**: explains why keeping only changes leaves the backup-age
  signal intact, with a query to confirm it after an upgrade; recommends a
  `sudoers` rule scoped to the collector instead of `docker` group membership;
  documents what `-1` and the probe's `0` mean. The column-level grants and the
  `-1` handling stay as they were.

## [0.15.0] - 2026-11-22

### Added
- **System commands.** Everything RouterFleet itself sends to devices - the
  connection test, the inventory and versions read, and each step of a backup
  (create, files to download, remove from the device, clean leftovers) - is a
  Fleet Commander command now, tagged **System**, with one variant per device
  type. Their defaults are exactly what the code sent before. They can not be
  deleted, disabled, scheduled or run by hand. An administrator can edit a
  variant after reviewing the change line by line and confirming it with a red
  button and a sum to solve in the confirmation popup, checked on the server;
  *Restore default* goes back. Every change is recorded in the change log.
- **Test connection**, on the device form (the values typed in, nothing saved)
  and on the device's page (its saved settings).

### Changed
- **Saving a device never connects to it.** The credential test used to run on
  every save, so a device that did not answer could not be disabled, have its
  monitoring switched off, or be added in advance. The import tool does not
  connect either. The address must be a valid IP address or hostname; it no
  longer has to resolve.

### Security
- **The stored password is only sent to the stored address.** Testing a new
  address or port needs the password typed again.

## [0.14.0] - 2026-11-22

### Added
- **Execute a command on one device** from the router list - a terminal icon
  next to edit - or from the device's page, for operators (level 40). Only
  commands with a variant for the device's type are offered.
- **Every manual execution is confirmed first** - on one device, on a
  selection from the list, or on devices and groups from a command's page: a
  confirmation page shows, per device type, the exact commands that will be
  sent, the verification that follows and the devices they go to. Nothing runs
  until it is confirmed, and a command edited after the confirmation page was
  shown is shown again instead of run.
- **The update check is an icon in the top bar**, on every page, next to My
  Settings and sign out: green on the latest release, yellow when an update is
  available (its version in the tooltip, a click opens the release), grey when
  the check is off or has not run yet. It replaces the status page's card.

### Changed
- **The About page** opens with the logo, large and centred.
- **Tools** sits above Audit Log in the menu.
- **Phones**: tables that the list view does not already fold scroll sideways
  inside their card instead of widening the page; chart headers wrap.

### Fixed
- **An up-to-date OpenWrt device was counted as "without version
  information"**: when no newer release was offered, no version was stored to
  compare with. It now counts as up to date; a failed release-list download or
  a snapshot build stays unknown.
- **RouterOS update check waited a fixed 5 seconds** for the device to reach
  MikroTik's update server; a slower answer left the device without
  information. It now reads again every 3 seconds, for up to 15.
- **RouterOS "update available" compared versions as text**: a device ahead of
  its channel was flagged. Versions are compared as numbers, by the rule the
  list colours with.
- **airOS devices**, which can not be checked for updates over SSH, are no
  longer counted as missing information an information update could fill in.

## [0.13.0] - 2026-11-22

### Fixed
- **Retention could delete every backup of a device** offline for longer than
  its retention: an offline device is not attempted, so no failure stopped the
  sweep. A device's newest stored configuration is now never deleted, however
  old.

### Security
- **The first administrator's password is checked like every other.** The
  form that creates the first account - an administrator - accepted any
  password, `1` included; it now applies the same rules as the user manager
  (length, common passwords, entirely numeric, similarity to the username).
- The monitoring container refuses a device address that `fping` would read as
  an option (one starting with `-`), whatever the database holds.

### Added
- **Backup profiles keep only changes.** A scheduled backup whose
  configuration is the same as the device's last stored one is recorded - it
  succeeded, at its time, and counts for backup age - but its text and binary
  are not stored again; it points to the stored backup and is marked
  *unchanged*. The default for new profiles; **profiles that exist when you
  upgrade keep every backup, as before** - an upgrade does not change how
  backups are stored - and can be switched later. *Backup now* backups are
  always stored in full.
- **Tools**, a new section for administrators, with two tools: **Backup
  clean-up** audits the stored backups and frees the duplicates (as unchanged
  backups), removes files no backup refers to, and reconciles the database with
  the disk; **Export latest backups** downloads a zip with each device's latest
  configuration, one file per backup, with a manifest.
- **A redesigned status page.** Charts on top - backups and devices online per
  calendar day, over 7, 15 or 30 days; a click on a day's bar opens that day's
  backups. Below them, modern cards: a coloured edge and a chip saying what to
  do (OK, Look, Act), the number large, the change against the previous 24
  hours, and a sparkline wherever RouterFleet keeps the history - never an
  invented one. A new **Scheduler & monitoring** card turns red when either
  container has stopped working, which otherwise goes unnoticed.
- **Every status card opens what it counts.** Failed backups open the devices
  with a failed backup in the last 24 hours; offline, disabled, unmonitored,
  up to date, missing updates and without information open those devices; the
  backup cards open the last 24 hours of backups. The list says it is filtered,
  and holds exactly as many items as the card shows.
- **Zabbix kit: security and operations items.** Devices without a stored SSH
  host key, host keys learned in the last hour (a rebuilt, replaced - or
  impersonated - device), failed sign-ins over 15 minutes, recent configuration
  changes and failed Fleet Commander jobs, with triggers for the ones worth an
  alarm.
- **A Zabbix integration page** in the documentation (`docs/zabbix.md`): what
  the kit reports, what its role can and cannot read, what it needs, and the
  four setup steps - linked from the README and from its own group in the
  documentation index.

- **A logo.** A controller and three devices inside a hexagon whose rim closes
  on itself with an arrow: continuous improvement of the fleet. It is in the
  application's top-left corner next to the name, in the browser tab, and on
  the README.

### Changed
- **The About page is full width and rewritten**: what RouterFleet Improved by
  Alessio Bravi does, the supported devices, and where it comes from - the
  earlier RouterFleet projects it started from and was inspired by, with their
  authors credited. Its buttons are centred.
- **My Settings has its own menu entry**, at the bottom with About. It used to
  light up User Manager, which is administration.
- The sign-in button, and the code page's, are centred.
- **The Zabbix monitoring role reads only the columns it counts**, also in the
  new tables: whether a sign-in succeeded and when - not who, from where, or the
  keys of open sessions; when a change was recorded - not what it was.
- **A counter the collector cannot read reports `-1`, never `0`**, and one
  trigger fires on it: a missing grant used to look exactly like a healthy
  fleet. This also applies to the existing router and failing-backup counters.

## [0.12.3] - 2026-11-22

### Fixed
- **Command schedules failed with "Server Error (500)" on PostgreSQL.** The
  schedule defaults row is named `schedule_defaults` - 17 characters - in a
  column created for 16, so PostgreSQL refused to store it and every page that
  loads the defaults failed. A migration widens the column; installations
  repair themselves on upgrade, with nothing to do by hand. (The flaw came with
  the schedule defaults of the earlier projects' history.)
- **The change log listed a record's fields in the wrong order on
  PostgreSQL**, which stores JSON without key order. The order is rebuilt from
  the list of fields each model is recorded with, which also corrects the
  entries recorded before this fix.

### Added
- **CI runs the whole test suite on PostgreSQL 16 as well**, including the
  upgrade from both earlier projects' databases. The suite ran on SQLite only,
  which ignores length limits and keeps JSON key order - how both bugs above
  passed every test. A test also checks that every default and choice of every
  text field fits its field.

## [0.12.2] - 2026-11-22

### Fixed
- **Server errors are written to the container log.** With debug mode off,
  Django's default only e-mails a failed request's traceback, so a
  "Server Error (500)" left no trace anywhere an operator looks.
  `docker compose logs routerfleet` now shows the error and its traceback
  (the request line only - never request bodies).

## [0.12.1] - 2026-11-22

### Security
- A notification channel's token and destination are checked again every time a
  message is sent, not only when the channel is saved: a channel saved by an
  earlier version, never checked, could otherwise put an arbitrary path into
  the request to the notification service. Such a channel now fails with a
  message asking to edit it.
- The passkey endpoints answer with a fixed sentence when passkeys cannot be
  used, never with the text of an exception.

## [0.12.0] - 2026-11-22

### Added
- **Second factors.** Each user can turn on an **authenticator app** (TOTP,
  RFC 6238): signing in then asks for the password and a 6-digit code, each code
  is accepted once, and wrong codes count towards the sign-in lockout. And
  **passkeys**: sign in without a password, with the device's fingerprint, face
  or PIN. Both are managed in *My Settings*; an administrator can reset a user's
  second factors, and `manage.py reset_mfa` covers a locked-out sole
  administrator. Passkeys need the installation to be opened by a name with a
  trusted certificate - a browser rule - while the authenticator app works
  everywhere.
- **Update card on the status page:** green when this is the latest release,
  yellow with the version to update to when there is a newer one. The update
  check is now **on by default**: about once an hour it asks GitHub for the
  latest release, with nothing about the installation in the request.
  `UPDATE_CHECK=off` in `.env` turns it off.
- **Zabbix monitoring kit** in `contrib/zabbix/`: per-device backup age and
  result, fleet counters and a self-monitoring collector. Its database role can
  read only the ten columns it needs - not the device passwords, not the
  backed-up configurations - and CI runs it against a real database.

### Changed
- **The home page is the status page.** The former welcome text is the
  **About** page, at the bottom of the menu.
- Status cards are all the same size. **Failed Backups (24h)** and
  **Unmonitored Routers** (renamed from Not Monitored Routers) are green at 0.
  **Routers Up To Date** and **Routers Missing Updates** are hidden while they
  count nothing.
- The title is **RouterFleet Improved** throughout the interface.

### Removed
- **The WireGuard WebAdmin integration**, with its menu entry and pages. An
  upgrade drops its table and the token stored in it.

### Fixed
- A schedule could be saved with an end before its start; it is refused now.

## [0.11.5] - 2026-11-22 - First public release

The first public release of RouterFleet Improved: backup, inventory,
monitoring and remote command management for MikroTik RouterOS, OpenWrt and
Ubiquiti airOS fleets, published as five container images for amd64 and arm64.

An installation of an earlier RouterFleet project upgrades to it in place: its
database is migrated forward on first start, with no export or re-import. The
upgrade paths are tested on every push — see [Upgrading](docs/upgrading.md).

### Features
- **Backups**: daily, weekly, monthly and on demand, with retention per kind,
  text and binary, a line-by-line diff between any two, and a flag on every
  backup that changed the configuration.
- **Fleet Commander**: commands with a payload per device type, run by hand or
  on a schedule, with verified results. A command counts as successful only
  when a verification command proves it.
- **Inventory**: model, serial, OS and firmware version, architecture, CPU,
  and the version each device could be upgraded to.
- **Monitoring**: an independent container pings the fleet, records downtime
  and raises notifications on status changes.
- **Notifications**: Telegram, ntfy.sh and CallMeBot (WhatsApp), with
  concatenation and daily reports.
- **Audit log**: every sign-in attempt, and every configuration change field
  by field, in an append-only trail.
- **Bulk import** of devices from CSV, groups, per-user settings and five
  permission levels.

### Security
- Every stored credential is encrypted at rest — device and import passwords,
  SSH private keys, notification and integration tokens — with a key kept
  outside the database.
- Device SSH host keys are trusted on first use and checked on every
  connection; a different key is refused before any credential is sent.
- Every action that changes something is a POST carrying the CSRF token.
- Sign-in attempts are limited: 5 refused attempts for an account or from an
  address within 15 minutes.
- Session and CSRF cookies are `Secure` when HTTPS is enforced; responses carry
  `nosniff`, `X-Frame-Options: DENY` and a same-origin referrer policy; nginx
  does not announce its version.
- The scheduler endpoints are unreachable from outside; the debug endpoints
  need an administrator; the monitoring key never travels in a URL.
- Notification requests are built with encoded parameters, and a channel's
  token never appears in a stored error.
- No secret key in the repository; every outbound request has a timeout;
  nothing is loaded from another host by the browser.

### Release engineering
- Images based on Alpine (the database on Debian), installed with every
  available update, for amd64 and arm64.
- Every release is gated by a Trivy scan: no critical or high vulnerability in
  RouterFleet's images, none with a published fix in the database image, no
  committed secret. Each release's notes list what the scan still found.
- CodeQL code scanning on every push, on pull requests and weekly.
- Every action in CI is pinned to a commit.
- 490+ tests with a branch-coverage gate, the whole stack booted and checked
  in CI, and the upgrade from earlier databases tested on every push.
- Documentation with diagrams of every subsystem, in `docs/`.
