#!/usr/bin/env python3
"""Extract ``[project.dependencies]`` from pyproject.toml into a requirements file.

Used by ``//teachers_teammate:wheel_requires`` so the Bazel-built wheel's
``Requires-Dist`` metadata is derived from pyproject.toml — the single source of
truth for pip dependencies — instead of a hand-maintained list in BUILD that can
drift out of sync.

Usage:
    extract_wheel_requires.py <pyproject.toml> <output.txt>
"""

from __future__ import annotations

from pathlib import Path
import sys
import tomllib


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: extract_wheel_requires.py <pyproject.toml> <output.txt>", file=sys.stderr)
        raise SystemExit(2)
    pyproject, out = Path(sys.argv[1]), Path(sys.argv[2])
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    out.write_text("\n".join(deps) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
