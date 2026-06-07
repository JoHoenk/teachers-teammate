#!/usr/bin/env bash
# Create a Debian/Ubuntu .deb package for Teacher's Teammate.
#
# Usage (from repo root):
#   ./tools/packaging/make_deb.sh [version]
#   e.g.: ./tools/packaging/make_deb.sh 0.1.0
#
# Prerequisites:
#   * Binaries already in dist/   (run tools/build/build_standalone.py first)
#   * dpkg-deb                    (installed on any Debian/Ubuntu system)

set -euo pipefail

VERSION="${1:-0.1.0}"
ARCH="amd64"
PKG_NAME="teachers-teammate"
MAINTAINER="Teacher's Teammate Contributors"
DESCRIPTION="OCR pipeline for handwritten documents"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

PKG_DIR="${ROOT}/dist/deb/${PKG_NAME}_${VERSION}_${ARCH}"

# ── Clean and create package directory structure ─────────────────────────────

rm -rf "${PKG_DIR}"
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/usr/local/bin"
mkdir -p "${PKG_DIR}/usr/share/applications"
mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${PKG_DIR}/usr/share/doc/${PKG_NAME}"

# ── Copy binaries ────────────────────────────────────────────────────────────

if [[ ! -f "${ROOT}/dist/teachers-teammate" ]]; then
    echo "ERROR: dist/teachers-teammate not found." >&2
    echo "       Run 'python tools/build/build_standalone.py' first." >&2
    exit 1
fi
install -m 755 "${ROOT}/dist/teachers-teammate" \
    "${PKG_DIR}/usr/local/bin/teachers-teammate"

# ── Icon ─────────────────────────────────────────────────────────────────────

if [[ -f "${ROOT}/teachers_teammate/assets/teachers_teammate_icon.png" ]]; then
    cp "${ROOT}/teachers_teammate/assets/teachers_teammate_icon.png" \
        "${PKG_DIR}/usr/share/icons/hicolor/256x256/apps/teachers-teammate.png"
fi

# ── .desktop entry ───────────────────────────────────────────────────────────

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
Categories=Office;Utility;
Keywords=OCR;handwriting;scan;
EOF

# ── Copyright file ───────────────────────────────────────────────────────────

cat > "${PKG_DIR}/usr/share/doc/${PKG_NAME}/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ${PKG_NAME}
License: Apache-2.0
EOF

if [[ -f "${ROOT}/LICENSE" ]]; then
    cat "${ROOT}/LICENSE" >> "${PKG_DIR}/usr/share/doc/${PKG_NAME}/copyright"
fi

# ── DEBIAN/control ───────────────────────────────────────────────────────────

CLI_SIZE=$(du -sk "${PKG_DIR}/usr/local/bin/teachers-teammate" 2>/dev/null | cut -f1 || echo 0)
INSTALLED_SIZE=${CLI_SIZE}

cat > "${PKG_DIR}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Architecture: ${ARCH}
Maintainer: ${MAINTAINER}
Installed-Size: ${INSTALLED_SIZE}
Depends: tesseract-ocr
Recommends: tesseract-ocr-eng, tesseract-ocr-deu
Section: utils
Priority: optional
Homepage: https://github.com/JoHoenk/teachers-teammate
Description: ${DESCRIPTION}
 A local, privacy-first OCR pipeline for handwritten lecture notes.
 Supports Tesseract, PaddleOCR, Ollama, and remote LLM providers
 for both OCR extraction and text correction. Includes a CLI and a
 PySide6 GUI with live preview and statistics.
EOF

# ── DEBIAN/postinst — refresh icon/desktop caches ───────────────────────────

cat > "${PKG_DIR}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
EOF
chmod 0755 "${PKG_DIR}/DEBIAN/postinst"

# ── Build the .deb ───────────────────────────────────────────────────────────

OUT="${ROOT}/dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
dpkg-deb --build "${PKG_DIR}" "${OUT}"
echo "Created: ${OUT}"
