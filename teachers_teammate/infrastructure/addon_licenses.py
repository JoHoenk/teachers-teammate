"""The licenses of the add-on packages, read from the list the build ships.

``tools/build/addon_licenses.py`` writes ``assets/addon_licenses.csv`` from PyPI when the release
is built, so the Add-ons page can say under which terms a package arrives without asking the
network.  The list holds the add-ons' own packages and what they pull in, not the bundle's: those
are in ``assets/third_party_licenses.md``.

The app is not the distributor of an add-on — the teacher downloads it from its authors — but it
loads that code into its own process, so the terms are worth one hover.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
import sys

#: Where the build puts the list, relative to the folder the bundle's files sit in.
LICENSES_RELATIVE = Path("teachers_teammate") / "assets" / "addon_licenses.csv"


def _root() -> Path:
    """Return the folder the package's files sit in (the bundle's, in the installed app)."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass is not None else Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=1)
def _licenses() -> dict[str, str]:
    """Return ``{normalised package name: license}``; empty when the list is not there."""
    try:
        with (_root() / LICENSES_RELATIVE).open(encoding="utf-8", newline="") as handle:
            return {
                row["Package"].strip().lower(): row["License"].strip()
                for row in csv.DictReader(handle)
                if row.get("Package") and row.get("License")
            }
    except OSError, KeyError, csv.Error:
        return {}


def addon_license(package: str) -> str | None:
    """Return the license of an add-on's package, or ``None`` when the list does not know it."""
    return _licenses().get(package.strip().lower())


def license_tooltip(package: str) -> str:
    """Return the tooltip of an add-on's row: its license, or nothing when it is unknown."""
    license_text = addon_license(package)
    return f"License: {license_text}" if license_text else ""
