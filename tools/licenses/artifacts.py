"""What a finished package carries, held against the repository's license files and inventory.

An installed wheel carries the notice and the license list the app shows, and the versions inside
an artifact decide which sources we owe.  Both are read here once, from the artifact and from the
repository, so the checks that run on the real artifacts ask the same questions the tests ask of
the tree: a bump that updates the lock but leaves the texts behind fails the build, not the
teacher.  The copies a download carries (the installer's ``NOTICE.txt`` and
``third_party_license.txt``) are checked for presence by ``tools/packaging/test_*_install.*``,
which run against the installed program.  Nothing in this module touches an artifact itself — the
callers read their files and pass them in, which is what makes it testable with a fixture folder.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from pathlib import Path

INVENTORY = "third_party_licenses/inventory.csv"
"""The one list of third-party parts, written by ``tools/licenses/inventory.py``."""

PACKAGE_FILES = (
    "teachers_teammate/assets/LICENSE.txt",
    "teachers_teammate/assets/third_party_licenses.md",
    "teachers_teammate/assets/third_party_inventory.csv",
)
"""What the installed package carries, for **Help → Licenses**, by repository path."""

SMOKE_REPORTED = {"PySide6": "pyside6-essentials", "cv2": "opencv-python-headless"}
"""What a bundle's smoke result reports as a module, and the inventory row it must match."""

INSTALLED_REPORTED = {
    "PySide6-Essentials": "pyside6-essentials",
    "opencv-python-headless": "opencv-python-headless",
}
"""What a pip environment installs as a distribution, and the inventory row it must match."""


def locked_versions(tree: Path) -> dict[str, str]:
    """Return the committed inventory of *tree* as component -> version."""
    with (tree / INVENTORY).open(encoding="utf-8", newline="") as handle:
        return {row["Component"]: row["Version"] for row in csv.DictReader(handle)}


def problems(
    carried: Mapping[str, bytes], tree: Path, names: Iterable[str] = PACKAGE_FILES
) -> list[str]:
    """Return what the artifact got wrong: a file it lacks, or one that differs from the tree.

    *carried* maps the repository path of each file the artifact holds to its bytes.
    """
    found: list[str] = []
    for name in names:
        expected = (tree / name).read_bytes()
        actual = carried.get(name)
        if actual is None:
            found.append(f"{name} is missing")
        elif actual != expected:
            found.append(f"{name} differs from {tree / name}")
    return found


def version_problems(
    reported: Mapping[str, str],
    locked: Mapping[str, str],
    mapping: Mapping[str, str] | None = None,
) -> list[str]:
    """Return the versions an artifact reported that the inventory does not agree with.

    *reported* maps what the artifact calls a part (a distribution or a module) to its version,
    and *mapping* says which inventory row each of those names is; by default the distributions
    a pip environment installs.
    """
    mapping = INSTALLED_REPORTED if mapping is None else mapping
    found: list[str] = []
    for name, component in mapping.items():
        expected = locked.get(component)
        version = reported.get(name)
        if expected is None:
            found.append(f"the inventory has no row for {component}")
        elif version is None:
            found.append(f"{name} reported no version")
        elif version != expected:
            found.append(f"{name} is {version}, the inventory says {component} {expected}")
    return found
