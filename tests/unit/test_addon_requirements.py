"""The add-ons the app installs at run time are the ones this release ships and pins.

A teacher installs an add-on from the Add-ons page or the Setup Assistant, so its requirement
reaches a pip command of its own.  This test holds the table in ``infrastructure/addon_list``
to ``pyproject.toml`` — the extras a ``pip install`` uses — and to
``requirements_addons_lock.txt``, the versions the release was tested with: the requirement is a
range like the extra, the pin lives in the lock.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import tomllib

from teachers_teammate.domain.local_models import PRIVACY_ADDON_ID
from teachers_teammate.infrastructure.addon_list import addon_requirements

#: The extra the add-ons are installed from; the privacy add-on has an extra of its own.
_LLM_EXTRA = "llm"
_PRIVACY_EXTRA = "privacy"

#: A pinned line of the lock: the name, ``==``, the version, then a hash or a continuation.
_PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)")

#: A requirement's package name, up to its version spec.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _runfile(variable: str, name: str) -> Path:
    """Return the runfile *variable* names, or the source checkout's copy of *name*."""
    root = os.environ.get("TEST_SRCDIR", "")
    relative = os.environ.get(variable, "")
    if root and relative:
        return Path(root) / relative
    return Path(__file__).resolve().parents[2] / name


def _extras() -> dict[str, list[str]]:
    """Return the optional dependencies of ``pyproject.toml``, by extra."""
    path = _runfile("PYPROJECT", "pyproject.toml")
    project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
    return project["optional-dependencies"]


def _locked() -> dict[str, str]:
    """Return the version of every pinned package in the add-on lock, by normalised name."""
    path = _runfile("ADDON_LOCK", "requirements_addons_lock.txt")
    pinned: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        found = _PIN.match(line)
        if found is not None:
            pinned[_normalise(found.group("name"))] = found.group("version")
    return pinned


def _normalise(name: str) -> str:
    """Return *name* in the form pip compares packages in."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _package_of(requirement: str) -> str:
    """Return the package *requirement* names, without its version spec."""
    found = _NAME.match(requirement)
    assert found is not None, requirement
    return _normalise(found.group())


def test_every_add_on_requirement_is_a_line_of_its_extra() -> None:
    """
    Given  the requirements the app hands to pip for each add-on
    When   they are looked up in the extras of pyproject.toml
    Then   every one is a line there, so installing the extra covers the same version
    """
    extras = _extras()
    added = addon_requirements()
    known = {
        addon: extras[_PRIVACY_EXTRA] if addon == PRIVACY_ADDON_ID else extras[_LLM_EXTRA]
        for addon in added
    }
    absent = {
        addon: [requirement for requirement in requirements if requirement not in known[addon]]
        for addon, requirements in added.items()
    }

    assert added, "the app names no add-on: has the table moved?"
    assert {addon: missing for addon, missing in absent.items() if missing} == {}


def test_every_add_on_package_is_pinned_in_the_add_on_lock() -> None:
    """
    Given  the packages the add-ons install
    When   each name is looked up among the pinned lines of the add-on lock
    Then   it is pinned there with a version, and the requirement itself stays a range
    """
    requirements = [
        requirement for packages in addon_requirements().values() for requirement in packages
    ]
    locked = _locked()

    assert requirements, "the app names no add-on package: has the table moved?"
    unpinned = [
        requirement for requirement in requirements if _package_of(requirement) not in locked
    ]
    assert unpinned == []
    assert [requirement for requirement in requirements if "==" in requirement] == []
