#!/usr/bin/env python3
"""The add-on lock with the pins this platform's own resolve needs.

``requirements_addons_lock.txt`` is compiled on Linux, so a package only Windows or macOS needs
(``click`` brings ``colorama`` there) is missing from it.  :func:`write_platform_lock` writes
``addon_lock.txt`` and then adds what each add-on's dry run installs here that neither the lock
nor the bundle's constraints pin, so the installed app brings nothing unpinned.  A build and the
weekly resolution check both run it; the dry run of one add-on at a time, as a teacher installs
them.

Usage (from the repo root, inside the build environment, after ``addon_constraints.py``):

    python tools/build/platform_lock.py
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent.parent

# The build environment installs the project, but a developer running this from a plain checkout
# has not: the repo root makes ``tools.build`` importable either way.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build import addon_lock, addon_pins, check_addon_resolution  # noqa: E402

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ResolveError(RuntimeError):
    """An add-on could not be resolved here, so its pins are unknown."""


def platform_pins(
    lock: Path,
    constraints: Path,
    python: Path,
    addons: Mapping[str, Sequence[str]],
    *,
    run: Runner = subprocess.run,
) -> list[str]:
    """Return the ``name==version`` pins the dry runs of *addons* need and nothing pins.

    Raises:
        ResolveError: an add-on's dry run failed or timed out — a lock without its pins would
            ship them loose, so the caller must not go on.
    """
    pins = addon_lock.read_lock_pins(constraints) | addon_lock.read_lock_pins(lock)
    missing: dict[str, str] = {}
    for name, requirements in addons.items():
        with tempfile.TemporaryDirectory(prefix="addon_pins_") as folder:
            report = Path(folder) / "report.json"
            command = check_addon_resolution.python_dry_run_command(
                python, constraints, Path(folder) / "target", list(requirements), lock, report
            )
            try:
                result = run(
                    command,
                    capture_output=True,
                    text=True,
                    cwd=ROOT,
                    check=False,
                    timeout=check_addon_resolution.TIMEOUT_S,
                )
            except subprocess.TimeoutExpired as exc:
                raise ResolveError(f"{name}: pip did not answer in time") from exc
            if result.returncode != 0 or not report.is_file():
                reason = (result.stderr or "").strip().splitlines()[-1:] or ["no report"]
                raise ResolveError(f"{name}: {reason[0]}")
            for pin in addon_pins.unpinned(json.loads(report.read_text(encoding="utf-8")), pins):
                package, _, version = pin.partition("==")
                missing[package] = version
    return sorted(f"{package}=={version}" for package, version in missing.items())


def write_platform_lock(
    python: Path | None = None,
    *,
    addons: Mapping[str, Sequence[str]] | None = None,
    run: Runner = subprocess.run,
) -> tuple[int, list[str]]:
    """Write ``addon_lock.txt`` with this platform's pins; return the pin count and the extras.

    Raises:
        ResolveError: see :func:`platform_pins`.
    """
    addon_lock.write_lock()
    extra = platform_pins(
        addon_lock.OUTPUT,
        addon_lock.BUNDLE,
        python or Path(sys.executable),
        check_addon_resolution.addons_to_check() if addons is None else addons,
        run=run,
    )
    if extra:
        return addon_lock.write_lock(extra_pins=extra), extra
    return len(addon_lock.read_lock_pins(addon_lock.OUTPUT)), extra


def main() -> int:
    """Write the add-on lock and say what this platform added to it."""
    try:
        count, extra = write_platform_lock()
    except ResolveError as exc:
        print(f"Cannot resolve the add-ons here: {exc}")
        return 1
    print(f"Wrote {count} add-on pins to {addon_lock.OUTPUT.relative_to(ROOT)}")
    if extra:
        print(f"{len(extra)} of them are this platform's own: {' '.join(extra)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
