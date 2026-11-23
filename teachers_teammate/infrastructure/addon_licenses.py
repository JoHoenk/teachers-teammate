"""The licenses of the add-on packages, read from the inventory the build ships.

``tools/licenses/inventory.py`` writes ``third_party_licenses/inventory.csv`` and the copy of it
at ``assets/third_party_inventory.csv``, so the Add-ons page can say under which terms a package
arrives without asking the network.  The inventory holds every third-party part the app ships or
installs — the bundle's packages, the add-ons', the models — and this module is the name lookup
into it.

The app is not the distributor of an add-on — the teacher downloads it from its authors — but it
loads that code into its own process, so the terms are worth one hover.
"""

from __future__ import annotations

import csv
from functools import lru_cache
import logging
from pathlib import Path
import sys

#: Where the build puts the inventory, relative to the folder the bundle's files sit in.
LICENSES_RELATIVE = Path("teachers_teammate") / "assets" / "third_party_inventory.csv"

_logger = logging.getLogger(__name__)


def _root() -> Path:
    """Return the folder the package's files sit in (the bundle's, in the installed app)."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass is not None else Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=1)
def _licenses() -> dict[str, str]:
    """Return ``{normalised component name: license}``; empty when the list is not there."""
    try:
        with (_root() / LICENSES_RELATIVE).open(encoding="utf-8", newline="") as handle:
            return {
                row["Component"].strip().lower(): row["License"].strip()
                for row in csv.DictReader(handle)
                if row.get("Component") and row.get("License")
            }
    except (OSError, KeyError, csv.Error) as exc:
        _logger.debug("Cannot read the license inventory, no licenses to show: %s", exc)
        return {}


def addon_license(package: str) -> str | None:
    """Return the license of an add-on's package, or ``None`` when the list does not know it."""
    return _licenses().get(package.strip().lower())
