"""The three lock files agree on the version of every package they share.

``requirements_lock.txt`` is what the tests and the bundle run, ``requirements_addons_lock.txt``
is what an add-on install brings, and ``requirements_dev_lock.txt`` is the tooling. Three separate
resolutions of one ``pyproject.toml``, so a package that appears in two of them at two versions is a
conflict pip-compile could not see across the files — and one that only shows when a teacher
installs an add-on.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

#: The locks, in the order a mismatch is reported.
LOCKS = (
    "requirements_lock.txt",
    "requirements_addons_lock.txt",
    "requirements_dev_lock.txt",
)

ALLOWED: dict[str, str] = {
    "tenacity": "google-genai caps it below the test lock's version, so the add-on lock resolves lower",
}
"""A package two locks may disagree on, with the reason the difference is intended."""

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\]+)")

#: The checkout as Bazel's runfiles see it, or the source tree when the test runs directly.
_ROOT = (
    (Path(os.environ["TEST_SRCDIR"]) / os.environ["LOCKS_ROOT"]).parent
    if os.environ.get("TEST_SRCDIR") and os.environ.get("LOCKS_ROOT")
    else Path(__file__).resolve().parents[2]
)


def _pins(name: str) -> dict[str, str]:
    """Return ``normalised name → version`` of one lock file."""
    pins: dict[str, str] = {}
    for line in (_ROOT / name).read_text(encoding="utf-8").splitlines():
        found = _PIN.match(line)
        if found:
            pins[re.sub(r"[-_.]+", "-", found.group(1)).lower()] = found.group(2)
    return pins


def test_no_package_is_pinned_to_two_versions() -> None:
    """
    Given  the three lock files of the repository
    When   the version each pins of a package they share is compared
    Then   they agree, apart from the packages named in ALLOWED with their reason
    """
    pinned: dict[str, dict[str, str]] = {name: _pins(name) for name in LOCKS}
    packages = {package for pins in pinned.values() for package in pins}
    disagreements: dict[str, dict[str, str]] = {}
    for package in sorted(packages):
        seen = {name: pins[package] for name, pins in pinned.items() if package in pins}
        if len(set(seen.values())) > 1 and package not in ALLOWED:
            disagreements[package] = seen

    assert disagreements == {}, "these packages are pinned to two versions"


def test_a_pin_with_an_extra_is_read() -> None:
    """
    Given  lock lines that pin a package with an extra, and one without
    When   the lines are matched
    Then   the name is the package's and the version is the pin's
    """
    lines = ["smart-open[http]==9.0.6 \\", "tenacity==9.1.2 \\", "    --hash=sha256:ab"]

    found = [_PIN.match(line) for line in lines]

    assert [(m.group(1), m.group(2)) for m in found if m] == [
        ("smart-open", "9.0.6"),
        ("tenacity", "9.1.2"),
    ]
