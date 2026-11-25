#!/bin/bash
# Builds the six images and pushes them to a registry of YOUR OWN - a fork, a
# private mirror. The official images (ghcr.io/alessiobravi) are published by
# CI only, after the very bytes have been tested and with a signature: this
# script refuses that registry.
#
#   REGISTRY=registry.example.net/me ./build_and_push.sh            # the version tag
#   REGISTRY=registry.example.net/me ./build_and_push.sh --latest   # and latest
set -euo pipefail

cd "$(dirname "$0")"
VERSION=$(python3 scripts/version.py)
OFFICIAL=ghcr.io/alessiobravi
if [ -z "${REGISTRY:-}" ]; then
  echo "Set REGISTRY to your own registry, for example REGISTRY=registry.example.net/me" >&2
  exit 2
fi
if [ "${REGISTRY%/}" = "$OFFICIAL" ]; then
  echo "The official images are published by CI only (tested, then signed). Use a registry of your own." >&2
  exit 2
fi
LATEST=false
[ "${1:-}" = "--latest" ] && LATEST=true
IMAGES=(routerfleet routerfleet-cron routerfleet-monitoring routerfleet-nginx routerfleet-postgres18 routerfleet-postgres)

if [ -n "${REGISTRY_USERNAME:-}" ] && [ -n "${REGISTRY_TOKEN:-}" ]; then
  echo "$REGISTRY_TOKEN" | docker login "${REGISTRY%%/*}" -u "$REGISTRY_USERNAME" --password-stdin
fi

echo "Building version ${VERSION}..."
docker compose -f docker-compose-build.yml build

for IMAGE in "${IMAGES[@]}"; do
  docker tag "${OFFICIAL}/${IMAGE}:latest" "${REGISTRY}/${IMAGE}:${VERSION}"
  docker push "${REGISTRY}/${IMAGE}:${VERSION}"
  if [ "$LATEST" = true ]; then
    docker tag "${OFFICIAL}/${IMAGE}:latest" "${REGISTRY}/${IMAGE}:latest"
    docker push "${REGISTRY}/${IMAGE}:latest"
  fi
done

echo "Pushed ${VERSION} to ${REGISTRY}$( [ "$LATEST" = true ] && echo ' (and latest)')."
