#!/usr/bin/env bash
# Generate teachers_teammate/assets/teachers_teammate.icns from teachers_teammate_icon.png.
#
# Prerequisites: macOS (uses sips and iconutil — both built-in).
# Usage (from the repo root):
#   bash tools/packaging/make_icns.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="${ROOT}/teachers_teammate/assets/teachers_teammate_icon.png"
ICONSET="${ROOT}/teachers_teammate/assets/teachers_teammate.iconset"
OUT="${ROOT}/teachers_teammate/assets/teachers_teammate.icns"

if [[ ! -f "${SRC}" ]]; then
    echo "ERROR: source image not found: ${SRC}" >&2
    exit 1
fi

echo "Creating iconset from ${SRC}…"
rm -rf "${ICONSET}"
mkdir -p "${ICONSET}"

# Generate all required resolutions
for size in 16 32 64 128 256 512; do
    sips -z "${size}" "${size}" "${SRC}" --out "${ICONSET}/icon_${size}x${size}.png" > /dev/null
    double=$((size * 2))
    sips -z "${double}" "${double}" "${SRC}" --out "${ICONSET}/icon_${size}x${size}@2x.png" > /dev/null
done

echo "Running iconutil…"
iconutil -c icns "${ICONSET}" -o "${OUT}"

# Clean up intermediate iconset directory
rm -rf "${ICONSET}"

echo "Created: ${OUT}"
