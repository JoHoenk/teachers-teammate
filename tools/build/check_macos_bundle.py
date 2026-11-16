#!/usr/bin/env python3
"""Check that the macOS bundle runs on the oldest macOS we support.

PyInstaller bundles the libraries of the machine it builds on, so a build on a newer macOS
silently raises the minimum macOS — and the app then refuses to start on an older one with
"requires macOS 15.0 or later". This finds every Mach-O file in the ``.app`` (by magic bytes,
not by extension), reads the minimum macOS each one asks for with ``vtool -show-build`` and
fails when one needs a newer macOS than ``--max-minos``.

A universal2 file holds one slice per architecture, and ``vtool`` prints one build command per
slice; every slice counts, because the app has to start on the architecture the teacher has.

Usage (from the repo root, after build_standalone.py, on macOS):

    python tools/build/check_macos_bundle.py
"""

from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path
import re
import shutil
import subprocess
import sys

#: The magic bytes of a Mach-O file, thin (one architecture) and fat (several).
_THIN_MAGIC = (
    b"\xcf\xfa\xed\xfe",  # 0xFEEDFACF, 64-bit, little-endian
    b"\xfe\xed\xfa\xcf",  # 0xFEEDFACF, 64-bit, big-endian
    b"\xce\xfa\xed\xfe",  # 0xFEEDFACE, 32-bit, little-endian
    b"\xfe\xed\xfa\xce",  # 0xFEEDFACE, 32-bit, big-endian
)
_FAT_MAGIC = (
    b"\xca\xfe\xba\xbe",  # 0xCAFEBABE, big-endian
    b"\xbe\xba\xfe\xca",  # 0xCAFEBABE, little-endian
    b"\xca\xfe\xba\xbf",  # 0xCAFEBABF, 64-bit, big-endian
    b"\xbf\xba\xfe\xca",  # 0xCAFEBABF, 64-bit, little-endian
)

#: A fat header's architecture count is a big-endian ``uint32`` at offset 4.  A Java ``.class``
#: file starts with the same 0xCAFEBABE, but its bytes 4-7 are the class file version (0x0000
#: 0x0034 for Java 8), so the count is 0 or absurdly large and the file is not a Mach-O one.
_MAX_ARCHITECTURES = 32

#: The oldest macOS the bundle may need.  The PySide6 6.12.0 wheel's Qt libraries say 14.4, so
#: the app's own floor follows them; the spec's ``LSMinimumSystemVersion`` reads this number, so
#: the .app and this check cannot disagree.
MACOS_MINIMUM = "14.4"

#: The two load commands that carry a minimum macOS, and the line that holds it.
_MINOS = re.compile(r"^\s*minos\s+(\d+(?:\.\d+)*)\s*$", re.MULTILINE)
_VERSION = re.compile(r"^\s*version\s+(\d+(?:\.\d+)*)\s*$", re.MULTILINE)


def macho_arch_count(header: bytes) -> int:
    """Return how many architecture slices *header* announces, or 0 when it is not a Mach-O file.

    Args:
        header: the first eight bytes of the file.
    """
    magic = header[:4]
    if magic in _THIN_MAGIC:
        return 1
    if magic in _FAT_MAGIC:
        order = "big" if magic in (_FAT_MAGIC[0], _FAT_MAGIC[2]) else "little"
        count = int.from_bytes(header[4:8], order)
        return count if 1 <= count <= _MAX_ARCHITECTURES else 0
    return 0


def parse_minos(text: str) -> list[tuple[int, ...]]:
    """Return the minimum macOS of every build command in ``vtool -show-build`` output.

    ``LC_BUILD_VERSION`` names it ``minos``; the older ``LC_VERSION_MIN_MACOSX`` names it
    ``version``.  The ``version`` line of a ``tool`` entry is the tool's own version (711.0),
    not a macOS, so only the line inside one of those two commands counts.
    """
    versions: list[tuple[int, ...]] = []
    for block in re.split(r"^\s*cmd ", text, flags=re.MULTILINE)[1:]:
        command = block.split(None, 1)[0]
        pattern = {
            "LC_BUILD_VERSION": _MINOS,
            "LC_VERSION_MIN_MACOSX": _VERSION,
        }.get(command)
        if pattern is None:
            continue
        match = pattern.search(block)
        if match:
            versions.append(tuple(int(part) for part in match.group(1).split(".")))
    return versions


def normalise(version: tuple[int, ...]) -> tuple[int, int, int]:
    """Pad *version* to major.minor.patch, so ``14`` and ``14.0`` compare equal."""
    padded = (*version, 0, 0, 0)
    return (padded[0], padded[1], padded[2])


def show(version: tuple[int, ...]) -> str:
    """Write *version* as macOS does: ``14.0``, and ``15.2.1`` only when there is a patch."""
    return ".".join(str(part) for part in (version if version[2:] != (0,) else version[:2]))


def parse_limit(text: str) -> tuple[int, int, int]:
    """Turn ``--max-minos`` (``14`` or ``14.0``) into a comparable version."""
    try:
        return normalise(tuple(int(part) for part in text.split(".")))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a version like 14.0: {text!r}") from None


def highest_minos(path: Path) -> tuple[int, int, int] | None:
    """Return the newest minimum macOS *path* asks for, or None when it names none.

    Raises:
        RuntimeError: ``vtool`` could not read the file.
    """
    result = subprocess.run(
        ["vtool", "-show-build", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"vtool exited with {result.returncode}")
    newest = max(parse_minos(result.stdout), default=None)
    return None if newest is None else normalise(newest)


def macho_files(app: Path) -> list[Path]:
    """Return every Mach-O file under *app*, found by magic bytes (an extension says nothing)."""
    found: list[Path] = []
    for path in sorted(app.rglob("*")):
        # A symlink is the same file as its target; counting it twice would only repeat a line.
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                header = handle.read(8)
        except OSError:
            continue
        if macho_arch_count(header):
            found.append(path)
    return found


def read_versions(files: list[Path]) -> tuple[dict[Path, tuple[int, int, int]], list[str]]:
    """Read the minimum macOS of every file once.

    Returns:
        The versions of the files that name one, and a line per file ``vtool`` could not read.
    """
    versions: dict[Path, tuple[int, int, int]] = {}
    errors: list[str] = []
    for path in files:
        try:
            newest = highest_minos(path)
        except RuntimeError as error:
            errors.append(f"{path}: vtool failed: {error}")
            continue
        if newest is not None:
            versions[path] = newest
    return versions, errors


def too_new(
    versions: dict[Path, tuple[int, int, int]],
    limit: tuple[int, ...],
    ignore: tuple[str, ...] = (),
) -> list[str]:
    """Return a line per file that needs a newer macOS than *limit*.

    Args:
        versions: the minimum macOS of each file.
        limit: the oldest macOS the bundle may need.
        ignore: ``fnmatch`` patterns of files that are named in the docs as known exceptions.
    """
    ceiling = normalise(limit)
    return [
        f"{path} needs macOS {show(newest)}"
        for path, newest in versions.items()
        if newest > ceiling and not any(fnmatch.fnmatch(path.as_posix(), g) for g in ignore)
    ]


def main(argv: list[str] | None = None) -> int:
    """Check the bundle against ``--max-minos``; return 0 when every file fits."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "app",
        type=Path,
        nargs="?",
        default=Path("dist/teachers-teammate.app"),
        help="the built .app (default: dist/teachers-teammate.app)",
    )
    parser.add_argument(
        "--max-minos",
        type=parse_limit,
        default=parse_limit(MACOS_MINIMUM),
        help=f"the oldest macOS the bundle may need (default: {MACOS_MINIMUM})",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="GLOB",
        help="a file pattern allowed to need more (repeatable); the files are still counted",
    )
    args = parser.parse_args(argv)

    if shutil.which("vtool") is None:
        print("vtool is missing; it comes with the Xcode command line tools")
        return 1
    if not args.app.is_dir():
        print(f"{args.app} is missing; run tools/build/build_standalone.py first")
        return 1

    files = macho_files(args.app)
    if not files:
        print(f"no Mach-O file in {args.app}")
        return 1
    versions, errors = read_versions(files)
    if errors:
        print("vtool could not read these files:")
        for error in errors:
            print(f"  {error}")
        return 1
    if not versions:
        # A check that read nothing is broken, not clean.
        print(f"vtool named no minimum macOS for any of the {len(files)} Mach-O files")
        return 1
    limit_text = show(args.max_minos)
    problems = too_new(versions, args.max_minos, tuple(args.ignore))
    if problems:
        print(f"The bundle needs a newer macOS than {limit_text}:")
        for problem in problems:
            print(f"  {problem}")
        print("\nBuild on an older macOS, or raise --max-minos and say so in the docs.")
        return 1
    newest = show(max(versions.values()))
    exempt = len(too_new(versions, args.max_minos)) if args.ignore else 0
    if exempt:
        print(f"{exempt} files are exempt by --ignore and need up to {newest}.")
    print(f"{len(files)} bundled Mach-O files fit macOS {limit_text} (newest: {newest}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
