#!/usr/bin/env python3
"""Extract the requirements of pyproject.toml into requirements files for the wheel.

Used by ``//teachers_teammate:wheel_requires`` so the Bazel-built wheel's ``Requires-Dist`` and
``Provides-Extra`` metadata are derived from pyproject.toml — the single source of truth for pip
dependencies — instead of a hand-maintained list in BUILD that can drift out of sync.

Usage:
    extract_wheel_requires.py <pyproject.toml> <output.txt> [--extra NAME=<output.txt> ...]

The first output holds ``[project.dependencies]``; each ``--extra`` writes one of
``[project.optional-dependencies]`` (``llm``, ``privacy``).  The tooling extras (``test``,
``dev``) are never asked for: a teacher's install does not need them.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tomllib


def requirements(pyproject: dict, extra: str | None) -> list[str]:
    """Return the requirements of *extra*, or the base ones when *extra* is ``None``.

    Raises:
        KeyError: pyproject.toml has no such extra.
    """
    project = pyproject["project"]
    if extra is None:
        return list(project["dependencies"])
    return list(project["optional-dependencies"][extra])


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Write the base requirements and each requested extra."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pyproject", type=Path)
    parser.add_argument("output", type=Path, help="where the base requirements go")
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="NAME=OUTPUT",
        help="write the optional-dependencies group NAME to OUTPUT (repeatable)",
    )
    args = parser.parse_args(argv)

    data = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))
    _write(args.output, requirements(data, None))
    for item in args.extra:
        name, separator, target = item.partition("=")
        if not separator or not name or not target:
            parser.error(f"--extra wants NAME=OUTPUT, got {item!r}")
        try:
            _write(Path(target), requirements(data, name))
        except KeyError:
            print(f"pyproject.toml has no optional-dependencies group {name!r}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
