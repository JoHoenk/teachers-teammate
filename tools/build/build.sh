#!/usr/bin/env bash
# Build the standalone binary for Teacher's Teammate in a CLEAN, ISOLATED venv.
#
# Why a dedicated venv (not the active one)?
#   PyInstaller freezes whatever is importable in the running interpreter. If the
#   build runs inside a dev venv that also has the optional extras installed
#   (langchain, spaCy, …), those get swept into the binary — bloating it and,
#   worse, pulling in code whose licence the bundle does not carry.
#   Building in a fresh venv with ONLY the base runtime deps (pip install -e .)
#   guarantees the bundle matches third_party_licenses/ (the same set that
#   tools/licenses/inventory.py enumerates). Optional OCR/LLM/privacy
#   features are installed by users at runtime via the in-app addon installer
#   (which is why pip is bundled with --collect-all).
#
# Why the constraints file?
#   Every version comes from requirements_test_lock.txt plus
#   tools/build/requirements_platform.txt, so two builds of the same commit produce
#   the same bundle and the same license list. check_pins.py proves pip followed it.
#
# Usage (run from repo root):
#   ./tools/build/build.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$REPO_ROOT/.build_venv"
CONSTRAINTS="$REPO_ROOT/pyinstaller-work/constraints.txt"

cd "$REPO_ROOT"

PYTHON="${PYTHON:-python3.14}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "ERROR: $PYTHON not found." >&2
    echo "       The bundle is built with Python 3.14 (see .python-version)." >&2
    exit 1
fi

echo "==> Clearing stale build artifacts"
rm -rf "$VENV_DIR" build dist pyinstaller-work teachers_teammate.egg-info

echo "==> Writing the pinned constraints"
"$PYTHON" tools/build/make_constraints.py

echo "==> Creating clean build venv at $VENV_DIR (base runtime deps only)"
"$PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade -c "$CONSTRAINTS" pip
"$VENV_DIR/bin/pip" install --quiet -c "$CONSTRAINTS" -e . pyinstaller pyinstaller-hooks-contrib

echo "==> Checking that every installed package is pinned"
"$VENV_DIR/bin/python" tools/build/check_pins.py

echo "==> Building standalone binary"
"$VENV_DIR/bin/python" tools/build/build_standalone.py
