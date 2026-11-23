#!/usr/bin/env python3
"""What a pip resolve installs that the add-on lock does not pin.

pip's ``--report`` says what a dry run would install.  The add-on lock
(``requirements_addons_lock.txt``) is compiled on Linux, so a dependency that only Windows or
macOS needs is missing from it, and the installed app would bring that one unpinned.

:func:`unpinned` names them: ``tools/build/platform_lock.py`` writes them into ``addon_lock.txt``, and ``tools/build/check_addon_resolution.py --report`` fails
while one of them has no pin.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent

# The build environment installs the project, but a developer running this from a plain checkout
# has not: the repo root makes ``tools.build`` importable either way.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build.make_constraints import normalize  # noqa: E402


def report_pins(report: Mapping[str, Any]) -> dict[str, str]:
    """Return ``{normalised name: version}`` of what a pip ``--report`` payload installs."""
    return {
        normalize(str(item["metadata"]["name"])): str(item["metadata"]["version"])
        for item in report.get("install", ())
    }


def unpinned(report: Mapping[str, Any], pins: Mapping[str, str]) -> list[str]:
    """Return the ``name==version`` *report* installs that *pins* does not hold, sorted.

    A package the pins hold at another version counts: the lock does not pin what this resolve
    brings.
    """
    return sorted(
        f"{name}=={version}"
        for name, version in report_pins(report).items()
        if pins.get(name) != version
    )
