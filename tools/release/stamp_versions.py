#!/usr/bin/env python3
"""Stamp the release version into the package's __init__.py."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

PEP440_PATTERN = r"[0-9][0-9]*(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+|\.post[0-9]+|\.dev[0-9]+)?"

# SemVer pre-release labels → PEP 440 equivalents
# e.g. 0.1.2-rc.1 → 0.1.2rc1,  0.1.2-pre → 0.1.2rc0,  0.1.2-alpha.2 → 0.1.2a2
_SEMVER_PRE_MAP = {"alpha": "a", "beta": "b", "rc": "rc", "pre": "rc"}


def normalize_version(raw: str) -> str:
    """Normalize and validate a release version string.

    Accepts both PEP 440 and SemVer pre-release notation, with or without a dot before the
    pre-release number:
      v0.1.2-rc     → 0.1.2rc0
      v0.1.2-rc1    → 0.1.2rc1
      v0.1.2-rc.1   → 0.1.2rc1
      v0.1.2-alpha.2 → 0.1.2a2
    """
    version = raw.lstrip("v")
    m = re.match(r"^(\d+(?:\.\d+)*)-([a-z]+)(?:\.?(\d+))?$", version)
    if m:
        base, pre_type, pre_num = m.group(1), m.group(2), m.group(3) or "0"
        pep_pre = _SEMVER_PRE_MAP.get(pre_type)
        if pep_pre:
            version = f"{base}{pep_pre}{pre_num}"
    if not re.fullmatch(PEP440_PATTERN, version):
        msg = f"'{raw.lstrip('v')}' is not a valid PEP 440 version or SemVer pre-release"
        raise ValueError(msg)
    return version


def stamp_init_version(content: str, version: str) -> str:
    """Stamp __version__ in teachers_teammate/__init__.py content."""
    updated, count = re.subn(
        r'^__version__ = "[^"]*"',
        f'__version__ = "{version}"',
        content,
        flags=re.MULTILINE,
    )
    if count != 1:
        msg = "expected exactly one __version__ field in __init__.py"
        raise ValueError(msg)
    return updated


def stamp_init_file(version: str, init_path: Path) -> None:
    """Write a normalized version into __init__.py."""
    content = init_path.read_text(encoding="utf-8")
    init_path.write_text(stamp_init_version(content, version), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version (v-prefixed accepted)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the version and print the normalised form; write nothing",
    )
    parser.add_argument(
        "--init-file",
        default="teachers_teammate/__init__.py",
        help="Path to teachers_teammate/__init__.py",
    )
    parser.add_argument(
        "--github-output",
        help="append `version=<resolved>` to this file ($GITHUB_OUTPUT)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        version = normalize_version(args.version)
        if not args.check:
            stamp_init_file(version, Path(args.init_file))
        if args.github_output:
            with Path(args.github_output).open("a", encoding="utf-8") as handle:
                handle.write(f"version={version}\n")
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Resolved version: {version}")
    if not args.check:
        print(f"Stamped version: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
