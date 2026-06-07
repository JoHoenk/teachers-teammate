"""Entry-point wrapper used by PyInstaller for the CLI executable.

When running from source (e.g. ``python tools/build/run_cli.py``), this script
adds the project root to ``sys.path`` so that the ``src`` package is importable.
In a frozen PyInstaller bundle the path is managed automatically.
"""

import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from teachers_teammate.cli import main  # noqa: E402

main()
