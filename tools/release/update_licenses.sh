#!/usr/bin/env bash
# Generate third-party license documentation.
#
# ┌────────────────────────────────────────────────────────────────────────────┐
# │  THIS IS THE DESTRUCTIVE FULL RUN. Use it ONLY when dependencies change.    │
# │                                                                            │
# │  For a normal release regeneration of the shipped artifacts, run instead:  │
# │      python tools/release/update_licenses.py --compile-only                │
# │  which reads the committed third_party_licenses/ store and needs no venv.  │
# │                                                                            │
# │  A full run recreates .venv-licenses, then wipes and rebuilds              │
# │  third_party_licenses/ from the wheels. The hand-curated native/vendored   │
# │  license trees (pypdfium2/deps, numpy/numpy/…, pip/src/pip/_vendor/…,       │
# │  opencv-python/LICENSE-3RD-PARTY.txt, the PySide6 Qt attributions) are NOT  │
# │  reproduced by the collector and MUST be re-applied before committing.     │
# │  See the module docstring in update_licenses.py for the full list.         │
# └────────────────────────────────────────────────────────────────────────────┘
#
# Creates (or recreates) a dedicated virtualenv at .venv-licenses/ containing
# only the base runtime dependencies (pip install -e .) plus pip-licenses, then
# runs tools/release/update_licenses.py. The enumerated set (base + transitive)
# is exactly what PyInstaller bundles into the standalone binary.
#
# Usage (from repo root):
#   bash tools/release/update_licenses.sh
#
# Outputs (both committed):
#   third_party_licenses/                              — per-package license store
#   teachers_teammate/assets/third_party_licenses.md   — bundled GUI file

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv-licenses"

cd "$REPO_ROOT"

echo "==> Recreating $VENV_DIR"
rm -rf "$VENV_DIR"
python3 -m venv "$VENV_DIR"

echo "==> Installing base runtime deps (pip install -e .) + pip-licenses"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e . pip-licenses

echo "==> Running update_licenses.py"
"$VENV_DIR/bin/python" tools/release/update_licenses.py
