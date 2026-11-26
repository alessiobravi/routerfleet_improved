# Updating

*System settings → Updates* (Administrator) shows the running release and the
newest one. RouterFleet asks for a new release about once an hour
(`UPDATE_CHECK`, or your own `UPDATE_CHECK_URL`); **Check now** asks at once
and shows the answer, at most once every 30 seconds.

Updating is done on the host, in the folder of the compose file:

```bash
docker compose pull && docker compose up -d
```

Back up the database first - see [Upgrading](upgrading.md#before-you-start).

## Why RouterFleet does not update itself

Installing images and restarting containers takes access to Docker, and
access to the Docker socket is root on the host: whoever runs code in a
container that holds it controls the machine. No RouterFleet container is
given it - not the application, not the scheduler.

Releases 0.21 to 0.23.0 offered an in-app updater that ran in the scheduler
container with the Docker socket. It was removed in 0.23.1 for that reason; the
current compose files do not mount the socket anywhere. An installation still
running an older compose file should remove from the `routerfleet-cron`
service the lines

```yaml
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - .:/compose:ro
      - update_requests:/update
      - update_private:/update-private
```

and from `routerfleet` the line `- update_requests:/update`, then run
`docker compose up -d`. The two volumes it leaves behind can be removed with
`docker volume rm <project>_update_requests <project>_update_private`.

## What's new

The update icon in the top bar - and *Update available* in the status bar -
opens **What's new**: what each release ahead of the installation brings, from
the project's changelog, newest first, then how to update; below, what the
installed release brought. Any signed-in user can read it. The changelog of
newer releases is fetched only while the update check is on (`UPDATE_CHECK`),
through the same outbound path, and kept for an hour; with the check off,
nothing leaves the installation and the page shows the installed release only.
`UPDATE_NOTES_URL` (a setting) points it at another copy of the changelog.

## Verifying a release image

Every release image is signed by the release workflow of this repository with
Sigstore. To check an image before running it, with
[cosign](https://docs.sigstore.dev/cosign/system_config/installation/):

```bash
cosign verify ghcr.io/alessiobravi/routerfleet:latest \
  --certificate-identity https://github.com/alessiobravi/routerfleet_improved/.github/workflows/build_and_push_images.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Each image also carries the **build provenance** of the workflow that made it
(which commit, which workflow run), recorded with GitHub's attestations. With
the [GitHub CLI](https://cli.github.com/):

```bash
gh attestation verify oci://ghcr.io/alessiobravi/routerfleet:latest --repo alessiobravi/routerfleet_improved
```

An image is published only after those very bytes have run: built once, started
as an installation on amd64 and on arm64, then tagged, signed and attested.
