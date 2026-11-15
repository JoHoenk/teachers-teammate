#!/usr/bin/env python3
"""Write the pip constraints file the standalone build installs against.

The versions come from ``requirements_test_lock.txt`` — the lock the tests run against,
resolved with the Python of `.python-version` — plus ``tools/build/requirements_platform.txt`` for the packages
that lock cannot hold (Windows-only ones) and the build tools themselves.

Usage (from the repo root):

    python tools/build/make_constraints.py

Output: ``pyinstaller-work/constraints.txt``, which the build then passes to pip:

    pip install -c pyinstaller-work/constraints.txt -e . pyinstaller pyinstaller-hooks-contrib

``tools/build/check_pins.py`` runs afterwards and fails when anything in the environment is
not pinned here, so a build never silently picks up a newer version.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
LOCK = ROOT / "requirements_test_lock.txt"
PLATFORM = Path(__file__).resolve().parent / "requirements_platform.txt"
OUTPUT = ROOT / "pyinstaller-work" / "constraints.txt"


def normalize(name: str) -> str:
    """Return the PEP 503 form of a distribution name (``PySide6_Essentials`` → ``pyside6-essentials``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(text: str) -> list[str]:
    """Return the ``name==version`` pins of a pip-compile file, in file order.

    Hashes, ``# via`` comments and blank lines are dropped; a trailing ``\\`` of a
    continued line goes with them. Extras are dropped too (``smart-open[http]==9`` becomes
    ``smart-open==9``): pip refuses a constraints file that has them.
    """
    pins: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().rstrip("\\").strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("--"):
            continue
        name, separator, version = stripped.partition("==")
        if not separator or not version:
            continue
        pins.append(f"{name.partition('[')[0].strip()}=={version.strip()}")
    return pins


def merge(*pin_lists: list[str]) -> dict[str, str]:
    """Merge pin lists into ``{normalized name: version}``; a later list wins."""
    merged: dict[str, str] = {}
    for pins in pin_lists:
        for pin in pins:
            name, _, version = pin.partition("==")
            merged[normalize(name)] = version
    return merged


def main() -> int:
    """Write the constraints file and say how many pins it holds."""
    pins = merge(
        parse_pins(LOCK.read_text(encoding="utf-8")),
        parse_pins(PLATFORM.read_text(encoding="utf-8")),
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "".join(f"{name}=={version}\n" for name, version in sorted(pins.items())),
        encoding="utf-8",
    )
    print(f"Wrote {len(pins)} pins to {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
