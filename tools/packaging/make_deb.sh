#!/usr/bin/env bash
# Create a Debian/Ubuntu .deb package for Teacher's Teammate.
#
# Usage (from repo root):
#   ./tools/packaging/make_deb.sh <version>
#   e.g.: ./tools/packaging/make_deb.sh 0.1.0
#
# Prerequisites:
#   * The folder build in dist/teachers-teammate/  (run tools/build/build_standalone.py first)
#   * dpkg-deb                                    (installed on any Debian/Ubuntu system)
#
# Layout:
#   /opt/teachers-teammate/            the folder build, Qt libraries as separate files
#   /usr/bin/teachers-teammate         symlink into it, so the desktop entry and the shell agree
#   /usr/share/applications/           desktop entry
#   /usr/share/icons/hicolor/          icon
#   /usr/share/doc/teachers-teammate/  DEP-5 copyright, NOTICE, third_party_license.txt
#
# dpkg drops the files an installed version no longer carries, so a change of layout needs no
# maintainer script.

set -euo pipefail

VERSION="${1:?usage: make_deb.sh <version>}"
ARCH="amd64"
PKG_NAME="teachers-teammate"
MAINTAINER="Teacher's Teammate Contributors"
DESCRIPTION="OCR pipeline for handwritten documents"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

BUILD="${ROOT}/dist/teachers-teammate"
PKG_DIR="${ROOT}/dist/deb/${PKG_NAME}_${VERSION}_${ARCH}"
DOC_DIR="${PKG_DIR}/usr/share/doc/${PKG_NAME}"

# The libraries the bundle does not carry, measured from this build rather than written by
# hand: every DT_NEEDED entry of every bundled ELF file that is not itself in the bundle,
# mapped to Debian packages on this machine, plus libc6 at the glibc floor the bundle needs.
# CI installs those libraries first (see the Qt system libraries step); the `install-linux`
# job of build_artifacts.yml is what checks the package names on the other releases.

if [[ ! -x "${BUILD}/teachers-teammate" ]]; then
    echo "ERROR: ${BUILD}/teachers-teammate not found." >&2
    echo "       Run 'python tools/build/build_standalone.py' first." >&2
    exit 1
fi

DEPENDS="$(python3 "${ROOT}/tools/build/check_linux_bundle.py" --depends)"
if [[ -z "${DEPENDS}" ]]; then
    echo "ERROR: could not measure the Depends: line." >&2
    exit 1
fi
# The bundle links no CA store: every HTTPS request (update check, model download, provider) reads
# the system's, which a minimal install does not have. The smoke test checks it.
DEPENDS="${DEPENDS}, ca-certificates"
echo "Depends: ${DEPENDS}"

# ── Clean and create package directory structure ─────────────────────────────

rm -rf "${PKG_DIR}"
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/opt"
mkdir -p "${PKG_DIR}/usr/bin"
mkdir -p "${PKG_DIR}/usr/share/applications"
mkdir -p "${DOC_DIR}"

# ── The application folder ───────────────────────────────────────────────────

cp -a "${BUILD}" "${PKG_DIR}/opt/${PKG_NAME}"
ln -s "/opt/${PKG_NAME}/teachers-teammate" "${PKG_DIR}/usr/bin/teachers-teammate"

# ── Icon ─────────────────────────────────────────────────────────────────────

# The hicolor folder names the icon's size, so it is measured from the file rather than
# written by hand: a 776x776 PNG in 256x256/apps is a lie the theme's cache believes, and
# check_desktop_entry.py (the install-linux job) fails on it.
ICON_SRC="${ROOT}/teachers_teammate/assets/teachers_teammate_icon.png"
if [[ -f "${ICON_SRC}" ]]; then
    ICON_SIZE="$(python3 - "${ICON_SRC}" <<'PY'
import struct
import sys

with open(sys.argv[1], "rb") as handle:
    header = handle.read(24)
width, height = struct.unpack(">II", header[16:24])
print(f"{width}x{height}")
PY
)"
    mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/${ICON_SIZE}/apps"
    cp "${ICON_SRC}" "${PKG_DIR}/usr/share/icons/hicolor/${ICON_SIZE}/apps/teachers-teammate.png"
fi

# ── .desktop entry ───────────────────────────────────────────────────────────

# Exactly one main category: desktop-file-validate rejects two, and the install-linux job runs
# it on the installed file.
cat > "${PKG_DIR}/usr/share/applications/teachers-teammate.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Teacher's Teammate
GenericName=OCR Tool
Comment=OCR pipeline for handwritten documents
Exec=teachers-teammate
Icon=teachers-teammate
Terminal=false
Categories=Office;
Keywords=OCR;handwriting;scan;
EOF

# ── Documentation: DEP-5 copyright, NOTICE, third-party licenses ─────────────

{
    cat <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ${PKG_NAME}
Source: https://github.com/JoHoenk/teachers-teammate

Files: *
Copyright: 2026 Teacher's Teammate contributors
License: GPL-3+
 This program is free software: you can redistribute it and/or modify it under the terms of
 the GNU General Public License as published by the Free Software Foundation, either version 3
 of the License, or (at your option) any later version.
 .
 This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
 without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 See the GNU General Public License for more details.
 .
 On Debian systems, the complete text of the GNU General Public License version 3 can be found
 in /usr/share/common-licenses/GPL-3.
Comment: The bundled third-party components and their license texts are listed in
 third_party_license.txt next to this file, and in the "Third-Party Licenses" tab of
 Help > Licenses in the application. third_party_host_libraries.txt names the libraries that
 came from the build machine, with their Debian copyright files. The Qt libraries are separate
 files in /opt/${PKG_NAME}/_internal/PySide6/Qt/lib and can be replaced with a compatible
 build.
EOF
} > "${DOC_DIR}/copyright"

cp "${ROOT}/NOTICE" "${DOC_DIR}/NOTICE"
cp "${ROOT}/teachers_teammate/assets/third_party_licenses.txt" "${DOC_DIR}/third_party_license.txt"
# The libraries the bundle took from the build machine, written by check_linux_bundle.py
# --host-libs into _internal/.
if [[ -f "${BUILD}/_internal/third_party_host_libraries.txt" ]]; then
    cp "${BUILD}/_internal/third_party_host_libraries.txt" "${DOC_DIR}/"
fi

# ── DEBIAN/control ───────────────────────────────────────────────────────────

# The whole package tree except the control files, as dpkg counts it.
INSTALLED_SIZE=$(du -sk --exclude=DEBIAN "${PKG_DIR}" | cut -f1)

# dpkg sorts 1.2.3-rc.1 after 1.2.3 (the part after the dash is a revision); 1.2.3~rc.1 sorts
# before it, so a pre-release upgrades to the final version.
TILDE='~'
DEB_VERSION="${VERSION/-/${TILDE}}"

cat > "${PKG_DIR}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${DEB_VERSION}
Architecture: ${ARCH}
Maintainer: ${MAINTAINER}
Installed-Size: ${INSTALLED_SIZE}
Depends: ${DEPENDS}
Section: utils
Priority: optional
Homepage: https://github.com/JoHoenk/teachers-teammate
Description: ${DESCRIPTION}
 A local, privacy-first OCR pipeline for handwritten lecture notes.
 Reads pages with a built-in reader or with Ollama, and proofreads
 them with Ollama or remote LLM providers. A PySide6 desktop app with
 live preview and statistics.
EOF

# ── DEBIAN/postinst, DEBIAN/postrm — refresh the desktop and icon caches ─────

for script in postinst postrm; do
    cat > "${PKG_DIR}/DEBIAN/${script}" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
EOF
    chmod 0755 "${PKG_DIR}/DEBIAN/${script}"
done

# ── Build the .deb ───────────────────────────────────────────────────────────

OUT="${ROOT}/dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group -Zxz "${PKG_DIR}" "${OUT}"
echo "Created: ${OUT}"
