#!/usr/bin/env python3
"""Check that the license store describes what the build actually contains.

``--env`` compares the current environment — the build venv, after the pinned install — with
``third_party_licenses/packages.csv``: every distribution that is not a build tool must have a
row, and the row must carry the same version. CI runs it on all three OS jobs, so the
platform-only packages (``pywin32-ctypes``, ``SecretStorage``, …) are covered too, which the
Linux-only Bazel tests cannot do.

Usage (from the repo root, inside the build environment):

    python tools/release/check_inventory.py --env
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import argparse
import csv
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.release.build_only import is_build_only, normalize  # noqa: E402

STORE = _ROOT / "third_party_licenses" / "packages.csv"


def read_store(path: Path) -> dict[str, str]:
    """Return ``{normalized package name: version}`` from the license store's CSV."""
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            normalize(row["Package"]): row["Version"]
            for row in csv.DictReader(handle)
            if row.get("Package")
        }


def environment_problems(store: dict[str, str]) -> list[str]:
    """Return what the environment has and the store does not, as readable lines."""
    problems: list[str] = []
    for distribution in metadata.distributions():
        name = (distribution.metadata["Name"] or "").strip()
        if not name or is_build_only(name):
            continue
        listed = store.get(normalize(name))
        if listed is None:
            problems.append(f"{name}=={distribution.version} has no row in packages.csv")
        elif listed != distribution.version:
            problems.append(f"{name}=={distribution.version} is listed as {listed}")
    return sorted(problems)


def main() -> int:
    """Run the requested check; return 0 when the store matches."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--env",
        action="store_true",
        help="compare the installed distributions with the license store",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=STORE,
        help="the packages.csv to check against (default: third_party_licenses/packages.csv)",
    )
    args = parser.parse_args()
    if not args.env:
        parser.error("nothing to do: pass --env")

    problems = environment_problems(read_store(args.store))
    if problems:
        print("The license store does not describe this environment:")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\nRun tools/release/update_licenses.py to collect the new packages, or pin the "
            "version the store lists."
        )
        return 1
    print("The license store describes every installed package.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
