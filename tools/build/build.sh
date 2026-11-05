#!/usr/bin/env bash
# Build the standalone binary for Teacher's Teammate in a CLEAN, ISOLATED venv.
#
# Why a dedicated venv (not the active one)?
#   PyInstaller freezes whatever is importable in the running interpreter. If the
#   build runs inside a dev venv that also has the optional extras installed
#   (langchain, spaCy, PaddleOCR, PyMuPDF, PyQt6, …), those get swept into the
#   binary — bloating it and, worse, pulling in AGPL/GPL code (PyMuPDF, PyQt6).
#   Building in a fresh venv with ONLY the base runtime deps (pip install -e .)
#   guarantees the bundle matches third_party_licenses/ (the same set that
#   tools/release/update_licenses.sh enumerates). Optional OCR/LLM/privacy
#   features are installed by users at runtime via the in-app addon installer
#   (which is why pip is bundled with --collect-all).
#
# Usage (run from repo root):
#   ./tools/build/build.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$REPO_ROOT/.build_venv"

cd "$REPO_ROOT"

echo "==> Clearing stale build artifacts"
rm -rf "$VENV_DIR" build dist pyinstaller-work teachers_teammate.egg-info

echo "==> Creating clean build venv at $VENV_DIR (base runtime deps only)"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e . "pyinstaller>=6.0"

echo "==> Building standalone binary"
"$VENV_DIR/bin/python" tools/build/build_standalone.py
