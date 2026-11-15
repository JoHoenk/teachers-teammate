#!/usr/bin/env bash
# Generate third-party license documentation.
#
# ┌────────────────────────────────────────────────────────────────────────────┐
# │  THIS IS THE DESTRUCTIVE FULL RUN and needs the argument --destructive.     │
# │                                                                            │
# │  For a normal release regeneration of the shipped artifacts, run instead:  │
# │      python tools/release/update_licenses.py --compile-only                │
# │  which reads the committed third_party_licenses/ store and needs no venv.  │
# │                                                                            │
# │  A full run installs the NEWEST packages (not the locked ones), sees only  │
# │  this platform, wipes third_party_licenses/ and rebuilds it from the       │
# │  wheels. Everything hand-made is lost: the python and pyinstaller rows and │
# │  texts, the platform-only packages, pypdfium2/deps, the numpy and pip      │
# │  vendored trees, the Qt attributions, cryptography/OPENSSL.txt. The list   │
# │  is in the module docstring of update_licenses.py.                         │
# └────────────────────────────────────────────────────────────────────────────┘
#
# Creates (or recreates) a dedicated virtualenv at .venv-licenses/ containing
# only the base runtime dependencies (pip install -e .) plus pip-licenses, then
# runs tools/release/update_licenses.py --destructive.
#
# Usage (from repo root):
#   bash tools/release/update_licenses.sh --destructive
#
# Outputs (both committed):
#   third_party_licenses/                              — per-package license store
#   teachers_teammate/assets/third_party_licenses.md   — bundled GUI file

set -euo pipefail

if [[ "${1:-}" != "--destructive" ]]; then
    echo "This wipes the hand-curated third_party_licenses/ store." >&2
    echo "Release path:  python tools/release/update_licenses.py --compile-only" >&2
    echo "Full run:      bash tools/release/update_licenses.sh --destructive" >&2
    echo "See the docstring of tools/release/update_licenses.py." >&2
    exit 2
fi

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
"$VENV_DIR/bin/python" tools/release/update_licenses.py --destructive
