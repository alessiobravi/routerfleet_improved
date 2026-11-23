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

## Verifying a release image

Every release image is signed by the release workflow of this repository with
Sigstore. To check an image before running it, with
[cosign](https://docs.sigstore.dev/cosign/system_config/installation/):

```bash
cosign verify ghcr.io/alessiobravi/routerfleet:latest \
  --certificate-identity https://github.com/alessiobravi/routerfleet_improved/.github/workflows/build_and_push_images.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
