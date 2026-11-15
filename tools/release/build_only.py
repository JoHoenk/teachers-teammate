"""Packages that are in the build environment but not in the bundle.

One list, used by both the license collector (``update_licenses.py``) and the inventory check
(``check_inventory.py``), so the two cannot drift apart.

Two absences are deliberate:

* ``pyinstaller`` — its bootloader is compiled into the exe, so it is listed in
  ``packages.csv`` like any other bundled component.
* ``pip`` — the bundle carries it for the in-app add-on installer.
"""

from __future__ import annotations

import re

#: In the build environment, not in the bundle.
BUILD_ONLY: frozenset[str] = frozenset(
    {
        "altgraph",  # PyInstaller's dependency-graph helper
        "macholib",  # PyInstaller's Mach-O reader, installed on macOS only
        "pefile",  # PyInstaller's PE reader, installed on Windows only
        "pip-licenses",  # the collector itself
        "prettytable",  # pip-licenses' own dependencies
        "wcwidth",
        "pyinstaller-hooks-contrib",
        "setuptools",  # excluded from the bundle by the spec
        "teachers-teammate",  # the project
        "wheel",
    }
)


def normalize(name: str) -> str:
    """Return the PEP 503 form of a distribution name (``PySide6_Essentials`` → ``pyside6-essentials``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def is_build_only(name: str) -> bool:
    """Return whether *name* is a build tool rather than something that ships."""
    return normalize(name) in BUILD_ONLY
