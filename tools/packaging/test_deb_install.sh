#!/usr/bin/env bash
# Install, upgrade, remove and purge the built .deb in a clean container.
#
# Usage (from the repo root, with Docker):
#   bash tools/packaging/test_deb_install.sh <image> [<dist-dir> [<old-dir> [<smoke-dir>]]]
#   e.g.: bash tools/packaging/test_deb_install.sh ubuntu:24.04
#
#   <image>      the container image to test on (ubuntu:24.04, ubuntu:26.04, debian:13, ...)
#   <dist-dir>   holds the new teachers-teammate_*_amd64.deb            (default: dist)
#   <old-dir>    holds the newest published release's .deb              (default: old)
#                (python tools/packaging/latest_release.py download --pattern '*_amd64.deb' --dir old)
#   <smoke-dir>  where the installed copy's smoke test result is written (default: smoke)
#
# apt resolves Depends, so a library missing from the control file fails here rather than on a
# user's machine. The upgrade from the newest release comes first, because it is the one path a
# fresh install never takes; then the launcher, the smoke test and the remove/purge pair. Not a
# real desktop (no window, no X11/Wayland, no fonts): that stays on the VM list.
#
# CI runs this script (build_artifacts.yml, job install-linux); nothing else is in the workflow.

set -euo pipefail

IMAGE="${1:?usage: test_deb_install.sh <image> [<dist-dir> [<old-dir> [<smoke-dir>]]]}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIST="$(cd "${2:-${ROOT}/dist}" && pwd)"
OLD="$(cd "${3:-${ROOT}/old}" && pwd)"
SMOKE="${4:-${ROOT}/smoke}"
mkdir -p "${SMOKE}"
SMOKE="$(cd "${SMOKE}" && pwd)"

echo "==> ${IMAGE}"
docker run --rm \
    -v "${DIST}:/dist:ro" \
    -v "${OLD}:/old:ro" \
    -v "${SMOKE}:/smoke" \
    -v "${ROOT}/tools/packaging/check_desktop_entry.py:/check_desktop_entry.py:ro" \
    "${IMAGE}" bash -c '
    set -euo pipefail
    # The Ubuntu images drop /usr/share/doc (all but copyright) at install time, which would
    # hide the notice files this job checks for; a desktop install keeps them.
    rm -f /etc/dpkg/dpkg.cfg.d/excludes
    apt-get update -qq
    # desktop-file-validate reads the launcher the package installs; python3 runs
    # check_desktop_entry.py (the bare images have none, and the bundle needs none).
    apt-get install -y -qq --no-install-recommends desktop-file-utils python3

    # ── L3: the upgrade from the newest published release ──
    old_deb=$(find /old -name "teachers-teammate_*_amd64.deb" | head -n 1)
    apt-get install -y -qq --no-install-recommends "$old_deb"
    # Every file and symlink the old package owns, so the upgrade can be checked for
    # leftovers. Directories are left out on purpose: one the old package made can be shared
    # with another package (hicolor-icon-theme owns the hicolor folders) and stay behind
    # rightly.
    dpkg-deb --fsys-tarfile "$old_deb" | tar -t | grep -v "/$" \
        | sed -e "s|^\./|/|" | sort -u > /tmp/old-files
    # A test build is older than anything published: apt has to be told, as the Windows
    # installer needs /ALLOWDOWNGRADE.
    apt-get install -y -qq --no-install-recommends --allow-downgrades \
        /dist/teachers-teammate_*_amd64.deb
    dpkg -L teachers-teammate | sort -u > /tmp/new-files
    left=0
    while read -r path; do
        [ -z "$path" ] && continue
        if ! grep -qxF "$path" /tmp/new-files && { [ -e "$path" ] || [ -L "$path" ]; }; then
            echo "left behind by the upgrade: $path" >&2
            left=1
        fi
    done < /tmp/old-files
    [ "$left" -eq 0 ]

    # The LGPL needs the notice and the license texts in every download.
    test -s /usr/share/doc/teachers-teammate/NOTICE
    test -s /usr/share/doc/teachers-teammate/third_party_license.txt

    # The smoke result of the installed copy, kept for the artifact upload.
    QT_QPA_PLATFORM=offscreen teachers-teammate --_smoke_test /smoke/smoke.json
    cat /smoke/smoke.json

    # The bootloader puts _internal on LD_LIBRARY_PATH, which is how the libraries the bundle
    # carries are found; a bare ldd would report them as missing.
    if LD_LIBRARY_PATH=/opt/teachers-teammate/_internal \
        ldd /opt/teachers-teammate/_internal/PySide6/Qt/plugins/platforms/libqxcb.so \
        | grep "not found"; then
        echo "the xcb platform plugin is missing libraries" >&2
        exit 1
    fi

    # ── L1: the launcher, its Exec, its Icon and the icon size ──
    python3 /check_desktop_entry.py

    # ── L2: remove and purge leave nothing behind ──
    apt-get remove -y -qq teachers-teammate
    test ! -e /opt/teachers-teammate
    test ! -e /usr/share/applications/teachers-teammate.desktop
    test ! -e /usr/bin/teachers-teammate
    test -z "$(find /usr/share/icons/hicolor -name "teachers-teammate.*")"
    apt-get install -y -qq --no-install-recommends /dist/teachers-teammate_*_amd64.deb
    apt-get purge -y -qq teachers-teammate
    test ! -e /opt/teachers-teammate
    test ! -e /usr/share/applications/teachers-teammate.desktop
    test ! -e /usr/bin/teachers-teammate
    test -z "$(find /usr/share/icons/hicolor -name "teachers-teammate.*")"
    echo "remove and purge leave nothing behind"
'
