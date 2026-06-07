#!/usr/bin/env python3
"""Stamp release version into pyproject and Bazel wheel metadata."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PEP440_PATTERN = r"[0-9][0-9]*(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+|\.post[0-9]+|\.dev[0-9]+)?"

# SemVer pre-release labels → PEP 440 equivalents
# e.g. 0.1.2-rc.1 → 0.1.2rc1,  0.1.2-pre → 0.1.2rc0,  0.1.2-alpha.2 → 0.1.2a2
_SEMVER_PRE_MAP = {"alpha": "a", "beta": "b", "rc": "rc", "pre": "rc"}


def normalize_version(raw: str) -> str:
    """Normalize and validate a release version string.

    Accepts both PEP 440 and SemVer pre-release notation:
      v0.1.2-rc     → 0.1.2rc0
      v0.1.2-rc.1   → 0.1.2rc1
      v0.1.2-alpha.2 → 0.1.2a2
    """
    version = raw.lstrip("v")
    m = re.match(r"^(\d+(?:\.\d+)*)-([a-z]+)(?:\.(\d+))?$", version)
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


def _find_py_wheel_block(lines: list[str], target_name: str) -> tuple[int, int]:
    i = 0
    while i < len(lines):
        line = lines[i]
        if "py_wheel(" not in line:
            i += 1
            continue

        start = i
        depth = line.count("(") - line.count(")")
        i += 1

        while i < len(lines) and depth > 0:
            depth += lines[i].count("(") - lines[i].count(")")
            i += 1

        end = i
        name = None
        for block_line in lines[start:end]:
            m = re.match(r'^\s*name\s*=\s*"([^"]+)"\s*,\s*$', block_line)
            if m:
                name = m.group(1)
                break

        if name == target_name:
            return start, end

    msg = f"could not find py_wheel target '{target_name}' in teachers_teammate/BUILD"
    raise ValueError(msg)


def stamp_bazel_wheel_version(content: str, version: str, target_name: str) -> str:
    """Stamp the version field inside one named py_wheel target."""
    lines = content.splitlines(keepends=True)
    block_start, block_end = _find_py_wheel_block(lines, target_name)

    version_line_count = 0
    for index in range(block_start, block_end):
        new_line, matched = re.subn(
            r'^(\s*version\s*=\s*")[^"]*("\s*,\s*)$',
            rf"\g<1>{version}\g<2>",
            lines[index],
        )
        if matched:
            lines[index] = new_line
            version_line_count += 1

    if version_line_count != 1:
        msg = f'expected exactly one version field inside py_wheel(name="{target_name}")'
        raise ValueError(msg)

    return "".join(lines)


def stamp_files(version: str, init_path: Path, build_path: Path, target_name: str) -> None:
    """Stamp both __init__.py and Bazel BUILD with a normalized version."""
    init_content = init_path.read_text(encoding="utf-8")
    init_updated = stamp_init_version(init_content, version)
    init_path.write_text(init_updated, encoding="utf-8")

    build_content = build_path.read_text(encoding="utf-8")
    build_updated = stamp_bazel_wheel_version(build_content, version, target_name)
    build_path.write_text(build_updated, encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version (v-prefixed accepted)")
    parser.add_argument(
        "--init-file",
        default="teachers_teammate/__init__.py",
        help="Path to teachers_teammate/__init__.py",
    )
    parser.add_argument(
        "--build-file",
        default="teachers_teammate/BUILD",
        help="Path to Bazel BUILD file containing py_wheel target",
    )
    parser.add_argument(
        "--wheel-target",
        default="teachers_teammate_wheel",
        help="Name of the py_wheel target whose version should be stamped",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        version = normalize_version(args.version)
        stamp_files(
            version=version,
            init_path=Path(args.init_file),
            build_path=Path(args.build_file),
            target_name=args.wheel_target,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Resolved version: {version}")
    print(f"Stamped version: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
