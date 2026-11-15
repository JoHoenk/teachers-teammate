#!/usr/bin/env python3
"""Fail the build when a distribution in the environment is not pinned.

``tools/build/make_constraints.py`` writes the constraints file; this script proves that pip
followed it. Anything installed but not pinned — a transitive dependency that appeared, a
package whose pin was dropped — would otherwise reach the bundle unnoticed, and the license
list would no longer describe what ships.

Usage (from the repo root, inside the build environment):

    python tools/build/check_pins.py

``--platform <wheel tag>`` checks another platform without installing anything: pip resolves
PyInstaller and the app's dependencies for that platform (``pip install --dry-run --report``)
and every distribution in the answer must be pinned. A PyInstaller release that adds a
dependency for Windows or macOS (``pefile``, ``macholib``) therefore fails on the Linux job
instead of on the runner that needs it:

    python tools/build/check_pins.py --platform win_amd64
    python tools/build/check_pins.py --platform macosx_14_0_arm64
"""

from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent.parent
CONSTRAINTS = ROOT / "pyinstaller-work" / "constraints.txt"

# Never shipped and never a dependency of the app: pip installs itself into every venv, and
# wheel is the packaging toolchain. The project itself is the thing being built.
_EXEMPT = frozenset({"pip", "wheel", "teachers-teammate"})


def normalize(name: str) -> str:
    """Return the PEP 503 form of a distribution name (``PySide6_Essentials`` → ``pyside6-essentials``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_constraints(path: Path) -> dict[str, str]:
    """Return ``{lowercase name: version}`` from a pip constraints file."""
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, _, version = stripped.partition("==")
        if version:
            pins[normalize(name.strip())] = version.strip()
    return pins


def unpinned(pins: dict[str, str]) -> list[str]:
    """Return the installed distributions that *pins* does not cover, as ``name==version``."""
    problems: list[str] = []
    for distribution in metadata.distributions():
        name = (distribution.metadata["Name"] or "").strip()
        if not name or normalize(name) in _EXEMPT:
            continue
        version = distribution.version
        pinned = pins.get(normalize(name))
        if pinned is None:
            problems.append(f"{name}=={version} is not pinned")
        elif pinned != version:
            problems.append(f"{name}=={version} is pinned at {pinned}")
    return sorted(problems)


def unpinned_in_report(report: dict, pins: dict[str, str]) -> list[str]:
    """Return what a ``pip install --report`` answer holds that *pins* does not cover."""
    problems: list[str] = []
    for item in report.get("install", []):
        name = str(item["metadata"]["name"])
        version = str(item["metadata"]["version"])
        pinned = pins.get(normalize(name))
        if pinned is None:
            problems.append(f"{name}=={version} is not pinned")
        elif pinned != version:
            problems.append(f"{name}=={version} is pinned at {pinned}")
    return sorted(problems)


#: What PEP 508 markers say on the target platform. pip's ``--platform`` only picks wheel
#: tags; it still evaluates ``sys_platform == "win32"`` against the machine it runs on, so
#: Windows-only and macOS-only dependencies never show up in its report.
_MARKER_ENVIRONMENTS: dict[str, dict[str, str]] = {
    "win": {
        "sys_platform": "win32",
        "platform_system": "Windows",
        "os_name": "nt",
        "platform_machine": "AMD64",
    },
    "macosx": {
        "sys_platform": "darwin",
        "platform_system": "Darwin",
        "os_name": "posix",
        "platform_machine": "arm64",
    },
}


def marker_environment(platform: str) -> dict[str, str]:
    """Return the marker values for a wheel platform tag (``win_amd64``, ``macosx_14_0_arm64``)."""
    for prefix, environment in _MARKER_ENVIRONMENTS.items():
        if platform.startswith(prefix):
            return environment
    return {}


def skipped_dependencies(report: dict, pins: dict[str, str], platform: str) -> list[str]:
    """Return requirements that apply on *platform* but that pip skipped for the host's markers.

    Only the first level is checked: every distribution in the report names what it needs, and
    whatever applies on the target but is neither in the report nor pinned is a missing pin.
    """
    environment = marker_environment(platform)
    resolved = {normalize(str(item["metadata"]["name"])) for item in report.get("install", [])}
    problems: list[str] = []
    for item in report.get("install", []):
        owner = str(item["metadata"]["name"])
        for text in item["metadata"].get("requires_dist", []):
            requirement = Requirement(text)
            if requirement.marker is None or not requirement.marker.evaluate(
                {**environment, "extra": ""}
            ):
                continue
            name = normalize(requirement.name)
            if name not in resolved and name not in pins:
                problems.append(
                    f"{requirement.name} (needed by {owner} on {platform}) is not pinned"
                )
    return sorted(set(problems))


def resolve_for_platform(platform: str, constraints: Path) -> dict:
    """Return pip's resolution of the build's requirements for *platform* (no installation)."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [
        *pyproject["project"]["dependencies"],
        "pyinstaller",
        "pyinstaller-hooks-contrib",
    ]
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--quiet",
        "--report",
        "-",
        "--only-binary",
        ":all:",
        "--python-version",
        "3.12",
        "--platform",
        platform,
        "-c",
        str(constraints),
        *requirements,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pip could not resolve for {platform}:\n{result.stderr.strip()}")
    return json.loads(result.stdout)


def main(argv: list[str] | None = None) -> int:
    """Check the environment, or ``--platform``'s resolution, against the constraints file."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--platform",
        help="check pip's resolution for this wheel platform tag instead of the environment",
    )
    args = parser.parse_args(argv)
    if not CONSTRAINTS.is_file():
        print(f"{CONSTRAINTS.relative_to(ROOT)} is missing; run make_constraints.py first")
        return 1
    pins = read_constraints(CONSTRAINTS)
    if args.platform:
        try:
            report = resolve_for_platform(args.platform, CONSTRAINTS)
            problems = unpinned_in_report(report, pins) + skipped_dependencies(
                report, pins, args.platform
            )
        except RuntimeError as exc:
            print(exc)
            return 1
    else:
        problems = unpinned(pins)
    if problems:
        print("The build environment does not match the constraints:")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\nAdd the pin to tools/build/requirements_platform.txt (a build tool or a "
            "platform-only package) or regenerate requirements_test_lock.txt."
        )
        return 1
    print("Every installed distribution is pinned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
