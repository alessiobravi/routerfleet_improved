#!/usr/bin/env bash
# Installs a pinned Trivy release, verified against the checksums published
# with it, into the directory given (default ~/.local/bin).
#
#     scripts/install_trivy.sh [DIR]          TRIVY_VERSION overrides the pin
set -euo pipefail

VERSION="${TRIVY_VERSION:-0.76.2}"
DEST="${1:-$HOME/.local/bin}"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)          PLATFORM=Linux-64bit ;;
  Linux-aarch64)         PLATFORM=Linux-ARM64 ;;
  Darwin-arm64)          PLATFORM=macOS-ARM64 ;;
  Darwin-x86_64)         PLATFORM=macOS-64bit ;;
  *) echo "unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

ARCHIVE="trivy_${VERSION}_${PLATFORM}.tar.gz"
BASE="https://github.com/aquasecurity/trivy/releases/download/v${VERSION}"
WORK=$(mktemp -d)
trap 'rm -rf -- "${WORK}"' EXIT

curl -fsSL -o "${WORK}/${ARCHIVE}" "${BASE}/${ARCHIVE}"
curl -fsSL -o "${WORK}/checksums.txt" "${BASE}/trivy_${VERSION}_checksums.txt"

EXPECTED=$(awk -v f="${ARCHIVE}" '$2 == f {print $1}' "${WORK}/checksums.txt")
if command -v sha256sum > /dev/null; then
  ACTUAL=$(sha256sum "${WORK}/${ARCHIVE}" | awk '{print $1}')
else
  ACTUAL=$(shasum -a 256 "${WORK}/${ARCHIVE}" | awk '{print $1}')
fi
if [ -z "${EXPECTED}" ] || [ "${EXPECTED}" != "${ACTUAL}" ]; then
  echo "checksum mismatch for ${ARCHIVE}" >&2
  exit 1
fi

mkdir -p "${DEST}"
tar -xzf "${WORK}/${ARCHIVE}" -C "${DEST}" trivy
"${DEST}/trivy" --version | head -1
