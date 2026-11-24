#!/usr/bin/env bash
# Build the standalone binary for Teacher's Teammate in a clean venv.
#
# The venv recipe — a fresh venv with the pinned install, and why not the active one — is
# tools/build/make_venv.py, which build.bat and the CI action call too.
#
# Usage (run from repo root):
#   ./tools/build/build.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$REPO_ROOT/.build_venv"

cd "$REPO_ROOT"

PYTHON="${PYTHON:-python3.14}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "ERROR: $PYTHON not found." >&2
    echo "       The bundle is built with Python 3.14 (see .python-version)." >&2
    exit 1
fi

echo "==> Clearing stale build artifacts"
rm -rf build dist pyinstaller-work teachers_teammate.egg-info

echo "==> Creating the build venv from the pins"
"$PYTHON" tools/build/make_venv.py "$VENV_DIR" --for build

echo "==> Building standalone binary"
"$VENV_DIR/bin/python" tools/build/build_standalone.py
