#!/usr/bin/env bash
# Create a macOS .dmg installer for Teacher's Teammate.
#
# Prerequisites: macOS (uses hdiutil — built-in).
# Usage (from the repo root):
#   bash tools/packaging/make_dmg.sh [version]
#
# Outputs: dist/teachers-teammate-<version>.dmg

set -euo pipefail

VERSION="${1:-0.1.0}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# PyInstaller BUNDLE produces "teachers-teammate.app" (from the spec name field).
APP_PATH="${ROOT}/dist/teachers-teammate.app"
DMG_PATH="${ROOT}/dist/teachers-teammate-${VERSION}-macos.dmg"

VOLNAME="Teacher's Teammate ${VERSION}"

if [[ ! -d "${APP_PATH}" ]]; then
    echo "ERROR: '${APP_PATH}' not found." >&2
    echo "       Run 'python tools/build/build_standalone.py' first." >&2
    exit 1
fi

echo "Staging .app bundle…"
STAGING="$(mktemp -d)"
trap 'rm -rf "${STAGING}"' EXIT

cp -R "${APP_PATH}" "${STAGING}/"
# Symlink to /Applications for drag-install UX
ln -s /Applications "${STAGING}/Applications"

echo "Creating DMG: ${DMG_PATH}…"
hdiutil create \
    -volname "${VOLNAME}" \
    -srcfolder "${STAGING}" \
    -ov -format UDZO \
    "${DMG_PATH}"

echo "Created: ${DMG_PATH}"
