#!/usr/bin/env bash
# Create the portable Linux tarball for Teacher's Teammate.
#
# Usage (from repo root):
#   ./tools/packaging/make_tarball.sh <version>
#   e.g.: ./tools/packaging/make_tarball.sh 0.1.0
#
# Prerequisites:
#   * The folder build in dist/teachers-teammate/  (run tools/build/build_standalone.py first)
#
# Output: dist/teachers-teammate-<version>-linux-x86_64.tar.xz
#         with the top folder teachers-teammate-<version>/
#
# This replaces the single-file download: the folder unpacks once and runs from wherever it
# was unpacked, and the Qt libraries stay separate files that can be replaced.

set -euo pipefail

VERSION="${1:?usage: make_tarball.sh <version>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

BUILD="${ROOT}/dist/teachers-teammate"
TOP="teachers-teammate-${VERSION}"
OUT="${ROOT}/dist/${TOP}-linux-x86_64.tar.xz"

if [[ ! -x "${BUILD}/teachers-teammate" ]]; then
    echo "ERROR: ${BUILD}/teachers-teammate not found." >&2
    echo "       Run 'python tools/build/build_standalone.py' first." >&2
    exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

mkdir -p "${STAGE}/${TOP}"
cp -a "${BUILD}/." "${STAGE}/${TOP}/"

cat > "${STAGE}/${TOP}/README.txt" <<EOF
Teacher's Teammate ${VERSION}
=============================

Unpack this folder anywhere and run:

    ./teachers-teammate

The application needs a graphical desktop. If it does not start, the system
libraries it links against are missing; on Debian and Ubuntu:

    sudo apt install libegl1 libgl1 libxcb-cursor0 libxkbcommon-x11-0

The Qt libraries are separate files under _internal/PySide6/Qt/lib and can be
replaced with a compatible build. The licenses of everything bundled here are in
third_party_license.txt and in the application's "Third-Party Licenses" dialog;
third_party_host_libraries.txt names the libraries that came from the build
machine, with their Debian copyright files.
EOF

cp "${ROOT}/NOTICE" "${STAGE}/${TOP}/NOTICE"
cp "${ROOT}/third_party_licenses/third_party_license.txt" "${STAGE}/${TOP}/third_party_license.txt"
# The libraries the bundle took from the build machine, written by check_linux_bundle.py
# --host-libs into _internal/.  It sits next to NOTICE too, where a reader looks for it.
if [[ -f "${BUILD}/_internal/third_party_host_libraries.txt" ]]; then
    cp "${BUILD}/_internal/third_party_host_libraries.txt" "${STAGE}/${TOP}/"
fi

# xz, not gzip: the same folder is about 30% smaller (169 MB against 244 MB on the Linux build),
# and every distribution that runs the app has xz. -T0 uses every core.
XZ_OPT="-6 -T0" tar -C "${STAGE}" -cJf "${OUT}" "${TOP}"
echo "Created: ${OUT}"
