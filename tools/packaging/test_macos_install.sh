#!/usr/bin/env bash
# Install the built dmg over the newest published release, the way a teacher does, and walk
# through the installed app.
#
# Usage (from the repo root, on macOS):
#   bash tools/packaging/test_macos_install.sh <new.dmg> <old.dmg> <smoke.json> [<e2e.json>]
#
# CAUTION: it replaces /Applications/teachers-teammate.app. Run it on a CI runner or a
# throwaway Mac, not on a machine where the app is in use.
#
# The newest release is installed first, so the replacement is what this script tests (Finder's
# "Replace" removes the old bundle and copies the new one, which is what the second step does).
# Its own smoke test is not run: an old build on a newer macOS is not what this is about. This
# tests the dmg, not Gatekeeper: a downloaded artifact carries no quarantine flag, so the
# "cannot be verified" dialog never appears here (that stays on the VM list).
#
# CI runs this script (build_artifacts.yml, job install-macos).

set -euo pipefail

NEW_DMG="${1:?usage: test_macos_install.sh <new.dmg> <old.dmg> <smoke.json> [<e2e.json>]}"
OLD_DMG="${2:?usage: test_macos_install.sh <new.dmg> <old.dmg> <smoke.json> [<e2e.json>]}"
SMOKE_OUT="${3:?usage: test_macos_install.sh <new.dmg> <old.dmg> <smoke.json> [<e2e.json>]}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="/Applications/teachers-teammate.app"
# Where the walkthrough writes its result and its pictures; both are uploaded by CI.
E2E_OUT="${4:-$(pwd)/e2e.json}"
E2E_SHOTS="$(dirname "${E2E_OUT}")/e2e-shots"

WORK="$(mktemp -d)"
MOUNT=""
cleanup() {
    if [[ -n "${MOUNT}" && -d "${MOUNT}" ]]; then
        hdiutil detach "${MOUNT}" >/dev/null 2>&1 || true
    fi
    rm -rf "${WORK}"
}
trap cleanup EXIT

# ── M2: a teacher who already has the app drags the new one over it ──
OLD_MOUNT="${WORK}/old-dmg"
mkdir -p "${OLD_MOUNT}"
hdiutil attach "${OLD_DMG}" -nobrowse -mountpoint "${OLD_MOUNT}"
old_app="$(find "${OLD_MOUNT}" -maxdepth 1 -name "*.app" | head -n 1)"
rm -rf "${APP}"
cp -R "${old_app}" "${APP}"
hdiutil detach "${OLD_MOUNT}"

# ── The new dmg: its licenses, and the app replaced in /Applications ──
MOUNT="${WORK}/dmg"
mkdir -p "${MOUNT}"
hdiutil attach "${NEW_DMG}" -nobrowse -mountpoint "${MOUNT}"
# Our own license, and the LGPL's notice and license texts, in every download.
test -s "${MOUNT}/Licenses/LICENSE.txt"
test -s "${MOUNT}/Licenses/NOTICE.txt"
test -s "${MOUNT}/Licenses/third_party_license.txt"
# Finder replaces the bundle rather than merging into it. What stands in /Applications
# afterwards must be the dmg's bundle and nothing else. The lists are of paths (symlinks
# included, not followed), so a dropped file or link shows.
rm -rf "${APP}"
cp -R "${MOUNT}/teachers-teammate.app" /Applications/
diff <(cd "${MOUNT}/teachers-teammate.app" && find . | sort) \
     <(cd "${APP}" && find . | sort)

# ── M1: the version the build was stamped with, the bundle identifier, the floor
#        (MACOS_MINIMUM, the same number the Mach-O check uses) and the icon the plist names ──
version="$(basename "${NEW_DMG}" | sed -e 's/^teachers-teammate-//' -e 's/-macos\.dmg$//')"
python3 "${ROOT}/tools/build/check_macos_bundle.py" --plist --version "${version}" "${APP}"

# ── The installed app starts ──
"${APP}/Contents/MacOS/teachers-teammate" --_smoke_test "${SMOKE_OUT}"

# ── The installed app does what a teacher does, in the real window and with the bundle's own
#    models. The script sits outside the .app, so this cannot change anything in the bundle;
#    the build job verifies the signature of the one the dmg carries. ──
if ! "${APP}/Contents/MacOS/teachers-teammate" --_run_script "${ROOT}/tests/e2e/walkthrough.py" \
    "${E2E_OUT}" --screenshots "${E2E_SHOTS}"; then
    cat "${E2E_OUT}" || true
    exit 1
fi
cat "${E2E_OUT}"
