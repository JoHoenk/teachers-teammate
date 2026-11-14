#!/usr/bin/env python3
"""Check that the Linux bundle runs on the oldest distribution we support.

PyInstaller bundles the libraries of the machine it builds on, so a build on a newer
distribution silently raises the minimum glibc — and the app then refuses to start on an
older one with a message about ``GLIBC_2.xx not found``. This reads the bundle's table of
contents, runs ``objdump -T`` on every ELF file in it, and fails when one needs a newer
``GLIBC_x.y`` than ``--max-glibc``.

``--unused`` answers the other question: which libraries did PyInstaller collect that
nothing in the bundle loads? Those are dead weight, and they are how a dropped Qt plugin
still drags its dependencies in.

Usage (from the repo root, after build_standalone.py):

    python tools/build/check_linux_bundle.py --max-glibc 2.35
    python tools/build/check_linux_bundle.py --unused
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
TOC = ROOT / "pyinstaller-work" / "teachers-teammate" / "PKG-00.toc"

#: The kinds in the table of contents that are ELF files.
_ELF_KINDS = frozenset({"BINARY", "EXTENSION"})

_GLIBC = re.compile(r"GLIBC_(\d+)\.(\d+)")

#: A shared library at the top level of the bundle, as opposed to a Python extension module.
_SHARED_LIBRARY = re.compile(r"^lib.+\.so")

#: Libraries the bootloader opens by name rather than through ``DT_NEEDED``.
_DLOPENED = ("libpython",)


def bundled_elf_files(toc_path: Path) -> list[tuple[str, Path]]:
    """Return ``(destination, source)`` for every ELF file the bundle carries."""
    contents = ast.literal_eval(toc_path.read_text(encoding="utf-8"))
    entries = contents[2]
    return [(dest, Path(source)) for dest, source, kind in entries if kind in _ELF_KINDS]


def needed_glibc(path: Path) -> set[tuple[int, int]]:
    """Return the ``GLIBC_x.y`` versions *path* needs, read from ``objdump -T``."""
    result = subprocess.run(
        ["objdump", "-T", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {(int(major), int(minor)) for major, minor in _GLIBC.findall(result.stdout)}


def too_new(files: list[tuple[str, Path]], limit: tuple[int, int]) -> list[str]:
    """Return a line per file that needs a newer glibc than *limit*."""
    problems: list[str] = []
    seen: set[Path] = set()
    for destination, source in files:
        if source in seen or not source.is_file():
            continue
        seen.add(source)
        newest = max(needed_glibc(source), default=None)
        if newest is not None and newest > limit:
            problems.append(f"{destination} needs GLIBC_{newest[0]}.{newest[1]}")
    return sorted(problems)


def needed_sonames(path: Path) -> set[str]:
    """Return the ``DT_NEEDED`` entries of *path*, read from ``objdump -p``."""
    result = subprocess.run(
        ["objdump", "-p", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {
        line.split()[-1] for line in result.stdout.splitlines() if line.strip().startswith("NEEDED")
    }


def unused_libraries(files: list[tuple[str, Path]]) -> list[str]:
    """Return the top-level shared libraries that no other bundled file needs."""
    needed: set[str] = set()
    for _, source in files:
        if source.is_file():
            needed |= needed_sonames(source)
    unused = []
    for destination, _ in files:
        name = destination.rsplit("/", 1)[-1]
        if "/" in destination or not _SHARED_LIBRARY.match(name):
            continue
        if name in needed or name.startswith(_DLOPENED):
            continue
        unused.append(destination)
    return sorted(set(unused))


def main() -> int:
    """Check the bundle against ``--max-glibc``; return 0 when every file fits."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--max-glibc",
        default="2.35",
        help="the oldest glibc the bundle may need (default: 2.35, Ubuntu 22.04)",
    )
    parser.add_argument(
        "--unused",
        action="store_true",
        help="list the bundled libraries nothing loads instead of checking glibc",
    )
    parser.add_argument(
        "--toc",
        type=Path,
        default=TOC,
        help="the bundle's PKG-00.toc (default: pyinstaller-work/teachers-teammate/PKG-00.toc)",
    )
    args = parser.parse_args()

    if shutil.which("objdump") is None:
        print("objdump is missing; install binutils")
        return 1
    if not args.toc.is_file():
        print(f"{args.toc} is missing; run tools/build/build_standalone.py first")
        return 1

    files = bundled_elf_files(args.toc)
    if args.unused:
        unused = unused_libraries(files)
        if not unused:
            print("Every bundled library is needed by another bundled file.")
            return 0
        print("Bundled libraries that nothing loads:")
        for name in unused:
            print(f"  {name}")
        print("\nDrop them in tools/build/teachers-teammate.spec, or say why they stay.")
        return 1

    limit = tuple(int(part) for part in args.max_glibc.split("."))
    problems = too_new(files, limit)
    if problems:
        print(f"The bundle needs a newer glibc than {args.max_glibc}:")
        for problem in problems:
            print(f"  {problem}")
        print("\nBuild on an older distribution, or raise --max-glibc and say so in the docs.")
        return 1
    print(f"{len(files)} bundled ELF files fit glibc {args.max_glibc}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
