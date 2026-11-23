#!/usr/bin/env python3
"""Write the add-on lock the installed app gives to pip: the versions this release was tested with.

The add-ons (spaCy, the LangChain provider packages) are installed from PyPI at runtime, so
without a lock a teacher gets whatever is newest that day — a version nobody ran.  The lock is
``requirements_addons_lock.txt``, the pip-compile lock of the ``llm`` and ``privacy`` extras
(``bazel run //:requirements_addons.update``, the same tooling as the test lock).  This script
turns it into the flat ``addon_lock.txt`` the app passes to pip as a second ``-c``, next to the
bundle's own constraints.

The bundle's packages are left out: ``addon_constraints.txt`` already pins them, and two
constraints files that disagree make pip refuse the install.

A bundle build passes the pins its own platform resolved and the lock does not hold — a
dependency only Windows or macOS needs (``tools/build/platform_lock.py``).  A plain checkout writes
the lock as it is.

Usage (from the repo root, inside the build environment, before PyInstaller runs):

    python tools/build/addon_constraints.py
    python tools/build/addon_lock.py

Output: ``teachers_teammate/_build/addon_lock.txt`` (git-ignored), which the spec adds to the
bundle.  ``build_standalone.py`` runs ``platform_lock.py``, which calls :func:`write_lock`.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
LOCK = ROOT / "requirements_addons_lock.txt"
BUNDLE = ROOT / "teachers_teammate" / "_build" / "addon_constraints.txt"
OUTPUT = ROOT / "teachers_teammate" / "_build" / "addon_lock.txt"

# The build environment installs the project, but a developer running this from a plain checkout
# has not: the repo root makes ``tools.build`` importable either way.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build.make_constraints import normalize, parse_pins  # noqa: E402


def lock_pins(text: str) -> dict[str, str]:
    """Return ``{normalised name: version}`` of a ``name==version`` file."""
    pins: dict[str, str] = {}
    for pin in parse_pins(text):
        name, _, version = pin.partition("==")
        pins[normalize(name)] = version
    return pins


def read_lock_pins(lock: Path) -> dict[str, str]:
    """Return the pins of *lock*, empty when the file is not there."""
    return lock_pins(lock.read_text(encoding="utf-8")) if lock.is_file() else {}


def addon_pins(lock_text: str, bundle_text: str, extra_pins: Iterable[str] = ()) -> list[str]:
    """Return the add-ons' ``name==version`` pins: the lock without the bundle's packages.

    *extra_pins* are the pins this platform resolved and the lock does not hold — a dependency
    only Windows or macOS needs (``tools/build/platform_lock.py``).  They replace the lock's own
    line for the same name, so the file pins one version per package.
    """
    pins = lock_pins(lock_text)
    for pin in extra_pins:
        name, _, version = pin.partition("==")
        pins[normalize(name)] = version
    bundle = lock_pins(bundle_text)
    return [f"{name}=={version}" for name, version in sorted(pins.items()) if name not in bundle]


def write_lock(output: Path = OUTPUT, extra_pins: Iterable[str] = ()) -> int:
    """Write the add-on lock, with *extra_pins* of this platform; return how many pins it holds."""
    pins = addon_pins(
        LOCK.read_text(encoding="utf-8"),
        BUNDLE.read_text(encoding="utf-8") if BUNDLE.is_file() else "",
        extra_pins,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(f"{pin}\n" for pin in pins), encoding="utf-8")
    return len(pins)


def main() -> int:
    """Write the add-on lock and say how many pins it holds."""
    count = write_lock()
    print(f"Wrote {count} add-on pins to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
