#!/usr/bin/env python3
"""Write the constraints file the installed app gives to pip for every add-on install.

The file lists ``name==version`` of every package in the build environment — exactly what the
bundle ships.  The app passes it as ``-c`` (``addon_pip.pip_policy_arguments``): an add-on
that needs another numpy or pydantic fails with pip's own explanation instead of putting a
second copy ahead of the bundled one on ``sys.path``.  After an app update the same file tells
the app that the packages folder was written by an older bundle
(``addon_folder.outdated_shared_packages``).

Usage (from the repo root, inside the build environment, before PyInstaller runs):

    python tools/build/addon_constraints.py

Output: ``teachers_teammate/_build/addon_constraints.txt`` (git-ignored), which the spec adds
to the bundle.  ``build_standalone.py`` calls :func:`write_constraints` itself.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "teachers_teammate" / "_build" / "addon_constraints.txt"

#: Installed in the build environment but not part of what ships: the project itself, the
#: packaging toolchain and PyInstaller with the packages only it needs.  Pinning them would
#: only constrain add-ons that never meet them.
BUILD_ONLY = frozenset(
    {
        "teachers-teammate",
        "pip",
        "wheel",
        "setuptools",
        "pyinstaller",
        "pyinstaller-hooks-contrib",
        "altgraph",
        "pefile",
        "macholib",
    }
)


def normalize(name: str) -> str:
    """Return the PEP 503 form of a distribution name (``PySide6_Essentials`` → ``pyside6-essentials``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def shipped_pins(distributions: list[tuple[str, str]]) -> list[str]:
    """Return sorted ``name==version`` lines for *distributions*, without the build-only ones.

    Args:
        distributions: ``(name, version)`` of what the environment holds.
    """
    pins = {
        normalize(name): version
        for name, version in distributions
        if name and normalize(name) not in BUILD_ONLY
    }
    return [f"{name}=={version}" for name, version in sorted(pins.items())]


def write_constraints(output: Path = OUTPUT) -> int:
    """Write the file for the running environment; return how many pins it holds."""
    installed = [
        ((dist.metadata["Name"] or "").strip(), dist.version) for dist in metadata.distributions()
    ]
    pins = shipped_pins(installed)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(f"{pin}\n" for pin in pins), encoding="utf-8")
    return len(pins)


def main() -> int:
    """Write the constraints file and say how many pins it holds."""
    count = write_constraints()
    print(f"Wrote {count} pins to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
