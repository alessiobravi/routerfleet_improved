#!/bin/bash
# Manual alternative to the CI workflow: builds the four images and pushes them
# tagged with the current version and as latest. CI does this on every push to
# main; use this only when you need to publish from a workstation.
set -euo pipefail

cd "$(dirname "$0")"
VERSION=$(python3 scripts/version.py)
REGISTRY=ghcr.io/alessiobravi
IMAGES=(routerfleet routerfleet-cron routerfleet-monitoring routerfleet-nginx)

if [ -n "${GHCR_USERNAME:-}" ] && [ -n "${GHCR_TOKEN:-}" ]; then
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
else
  echo "Log in to ghcr.io:"
  docker login ghcr.io
fi

echo "Building version ${VERSION}..."
docker compose -f docker-compose-build.yml build

for IMAGE in "${IMAGES[@]}"; do
  docker tag "${REGISTRY}/${IMAGE}:latest" "${REGISTRY}/${IMAGE}:${VERSION}"
  docker push "${REGISTRY}/${IMAGE}:${VERSION}"
  docker push "${REGISTRY}/${IMAGE}:latest"
done

echo "Pushed ${VERSION}. Note: CI also creates the git tag and GitHub Release; this script does not."
