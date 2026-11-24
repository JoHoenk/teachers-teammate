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
# fresh install never takes: the old version leaves its settings and an add-on folder, and the
# new one opens both (--after-upgrade). Then the xcb plugin, the launcher, the smoke test, the
# app walkthrough and the remove/purge pair. Not a real desktop (no window manager): that stays on
# the VM list, and the walkthrough brings the one font its window needs.
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
    -v "${ROOT}/LICENSE:/LICENSE:ro" \
    -v "${ROOT}/tools/packaging/check_desktop_entry.py:/check_desktop_entry.py:ro" \
    -v "${ROOT}/tests/e2e/walkthrough.py:/walkthrough.py:ro" \
    "${IMAGE}" bash -c '
    set -euo pipefail
    # The Ubuntu images drop /usr/share/doc (all but copyright) at install time, which would
    # hide the notice files this job checks for; a desktop install keeps them.
    rm -f /etc/dpkg/dpkg.cfg.d/excludes
    apt-get update -qq
    # desktop-file-validate reads the launcher the package installs, python3 runs
    # check_desktop_entry.py (the bare images have none, and the bundle needs none), and
    # xvfb, xauth and a font are the display the app walkthrough opens its window on.
    apt-get install -y -qq --no-install-recommends desktop-file-utils python3 \
        xvfb xauth fonts-dejavu-core

    # ── L3: the upgrade from the newest published release ──
    old_deb=$(find /old -name "teachers-teammate_*_amd64.deb" | head -n 1)
    apt-get install -y -qq --no-install-recommends "$old_deb"
    # Every file and symlink the old package owns, so the upgrade can be checked for
    # leftovers. Directories are left out on purpose: one the old package made can be shared
    # with another package (hicolor-icon-theme owns the hicolor folders) and stay behind
    # rightly.
    dpkg-deb --fsys-tarfile "$old_deb" | tar -t | grep -v "/$" \
        | sed -e "s|^\./|/|" | sort -u > /tmp/old-files

    # What the old version leaves behind, for L3b: its settings file in the folder
    # TEACHERS_TEAMMATE_TMPDIR names, and the name-detection add-on installed the way that
    # version installed it — with its own pip and no constraints, so the packages it shares with
    # the app are the newest ones, not the ones this app was built with. An update over that
    # folder makes it outdated, which is the branch only the installed app can reach.
    mkdir -p /tmp/old-data/Ausgabe
    cat > /tmp/old-data/ocr.toml <<"TOML"
ollama_url = "http://127.0.0.1:9"
ocr_engine = "ppocr"
language = "English"
correction_enabled = false
evaluation_enabled = false
anonymization_enabled = false
docx_enabled = true
text_enabled = false
text_anonymized_enabled = false
output = "/tmp/old-data/Ausgabe"
TOML
    # Both versions keep their add-ons in the data folder platformdirs names, and this one must
    # find the folder the old app filled.
    teachers-teammate --_pip_install_mode install \
        --target "${HOME}/.local/share/teachers_teammate/packages" --prefer-binary spacy

    # A test build is older than anything published: apt has to be told, as the Windows
    # installer needs /ALLOWDOWNGRADE.
    apt-get install -y -qq --no-install-recommends --allow-downgrades \
        /dist/teachers-teammate_*_amd64.deb
    # The shell remembers where it found the command before the upgrade; the old package put it
    # elsewhere than the new one does.
    hash -r
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

    # ── L3b: the new app opens what the old version left ──
    # The data of the old version, and an add-on folder its own pip filled: the new app runs the
    # same three steps a teacher takes after an update, over that settings file and add-on folder.
    if ! xvfb-run -a teachers-teammate --_run_script /walkthrough.py /smoke/e2e-upgrade.json \
        --after-upgrade /tmp/old-data --screenshots /smoke/e2e-upgrade-shots; then
        cat /smoke/e2e-upgrade.json || true
        exit 1
    fi
    cat /smoke/e2e-upgrade.json

    # Our own license (the DEP-5 grant), the notice and the third-party texts.
    grep -qxF "License: GPL-3+" /usr/share/doc/teachers-teammate/copyright
    grep -qF "either version 3 of the License, or (at your option) any later version" \
        /usr/share/doc/teachers-teammate/copyright
    grep -qF "/usr/share/common-licenses/GPL-3" /usr/share/doc/teachers-teammate/copyright
    test -s /usr/share/doc/teachers-teammate/NOTICE
    test -s /usr/share/doc/teachers-teammate/third_party_license.txt

    # The smoke result of the installed copy, kept for the artifact upload.
    QT_QPA_PLATFORM=offscreen teachers-teammate --_smoke_test /smoke/smoke.json
    cat /smoke/smoke.json

    # The bootloader puts _internal on LD_LIBRARY_PATH, which is how the libraries the bundle
    # carries are found; a bare ldd would report them as missing. Before the walk, because a
    # missing one stops Qt before the script writes its result: this names the plugin where a
    # bare exit code would not.
    if LD_LIBRARY_PATH=/opt/teachers-teammate/_internal \
        ldd /opt/teachers-teammate/_internal/PySide6/Qt/plugins/platforms/libqxcb.so \
        | grep "not found"; then
        echo "the xcb platform plugin is missing libraries" >&2
        exit 1
    fi

    # ── L1: the launcher, its Exec, its Icon and the icon size ──
    python3 /check_desktop_entry.py

    # The installed app does what a teacher does, in its own window on the xcb plugin and with
    # the models the .deb carries. The container runs as root: the walk isolates itself into a
    # temporary folder under /tmp and touches nothing else here.
    if ! xvfb-run -a teachers-teammate --_run_script /walkthrough.py /smoke/e2e.json \
        --screenshots /smoke/e2e-shots; then
        cat /smoke/e2e.json || true
        exit 1
    fi
    cat /smoke/e2e.json

    # ── Wayland: the same walk on the platform a modern desktop runs ──
    # Qt picks its platform plugin from QT_QPA_PLATFORM, and the bundle claims Wayland when it
    # carries that plugin. The Weston headless backend is a compositor with no screen, the way
    # xvfb is an X server with none; its log and the result go beside the other walk results.
    plugins=/opt/teachers-teammate/_internal/PySide6/Qt/plugins/platforms
    wayland_plugin=$(find "${plugins}" -name "libqwayland*.so" -print -quit)
    if [ -z "${wayland_plugin}" ]; then
        echo "the bundle carries no Wayland platform plugin: the Wayland walk is skipped" >&2
    else
        # Before weston: it installs the libraries the Wayland plugin needs, which would hide
        # one that the Depends line of the package lacks. The check the xcb plugin gets above.
        if LD_LIBRARY_PATH=/opt/teachers-teammate/_internal \
            ldd "${wayland_plugin}" | grep "not found"; then
            echo "the Wayland platform plugin is missing libraries" >&2
            exit 1
        fi
        apt-get install -y -qq --no-install-recommends weston
        export XDG_RUNTIME_DIR=/tmp/xdg-wayland
        mkdir -p "${XDG_RUNTIME_DIR}"
        chmod 700 "${XDG_RUNTIME_DIR}"
        weston --backend=headless-backend.so --renderer=pixman \
            --socket=wayland-teachers --idle-time=0 > /smoke/weston.log 2>&1 &
        weston_pid=$!
        ready=0
        for _ in $(seq 1 50); do
            if [ -S "${XDG_RUNTIME_DIR}/wayland-teachers" ]; then
                ready=1
                break
            fi
            sleep 0.2
        done
        if [ "${ready}" -eq 0 ]; then
            cat /smoke/weston.log >&2
            echo "weston did not start" >&2
            exit 1
        fi
        if ! WAYLAND_DISPLAY=wayland-teachers QT_QPA_PLATFORM=wayland \
            teachers-teammate --_run_script /walkthrough.py /smoke/e2e-wayland.json \
            --screenshots /smoke/e2e-wayland-shots; then
            cat /smoke/e2e-wayland.json || true
            # The log of the compositor says what the window of the app met there. It is
            # uploaded anyway; printing it puts it beside the failure in the job.
            cat /smoke/weston.log || true
            exit 1
        fi
        cat /smoke/e2e-wayland.json
        # The window must have been on Wayland, and not fallen back to another plugin.
        python3 -c "
import json, sys
result = json.load(open(\"/smoke/e2e-wayland.json\"))
sys.exit(0 if result[\"platform\"] == \"wayland\" else \"ran on \" + result[\"platform\"])
"
        kill "${weston_pid}" || true
    fi

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
