#!/usr/bin/env python3
"""Check the launcher a .deb installs: the entry, its Exec, its Icon and the icon's size.

Run inside the container that installed the package (the ``install-linux`` job of
``build_artifacts.yml``), where the paths are the installed ones:

    python3 tools/packaging/check_desktop_entry.py

It reads ``/usr/share/applications/teachers-teammate.desktop``, runs ``desktop-file-validate``
on it, resolves ``Exec=`` and ``Icon=`` to installed files, and checks that the icon sits in
the hicolor folder that names its real size — a 776x776 PNG in ``256x256/apps`` is a lie the
theme's cache believes, and the menu then draws a stretched icon.

``--desktop`` and ``--icon-root`` exist so a unit test can drive the same checks on a folder
of its own.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import struct
import subprocess
import sys

#: Where the package puts the entry and the icon theme.
DESKTOP = Path("/usr/share/applications/teachers-teammate.desktop")
ICON_ROOT = Path("/usr/share/icons/hicolor")

#: The magic bytes of a PNG, and the offset of its IHDR width and height.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_IHDR_SIZE = slice(16, 24)

#: The keys a launcher needs before a desktop environment can show it.
_REQUIRED = ("Type", "Name", "Exec", "Icon")


def read_entry(path: Path) -> dict[str, str]:
    """Return the ``[Desktop Entry]`` keys of *path*.

    Args:
        path: the ``.desktop`` file.
    """
    keys: dict[str, str] = {}
    in_entry = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_entry = stripped == "[Desktop Entry]"
            continue
        if not in_entry or not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        keys[key.strip()] = value.strip()
    return keys


def png_size(path: Path) -> tuple[int, int]:
    """Return the width and height in *path*'s PNG header.

    Raises:
        ValueError: the file is not a PNG.
    """
    header = path.read_bytes()[:24]
    if header[:8] != _PNG_MAGIC:
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", header[_IHDR_SIZE])


def check(desktop: Path, icon_root: Path) -> list[str]:
    """Return a line per problem with the launcher; an empty list means it is usable.

    Args:
        desktop: the installed ``.desktop`` file.
        icon_root: the hicolor theme the icon was installed into.
    """
    if not desktop.is_file():
        return [f"{desktop} is missing"]

    problems: list[str] = []
    validator = shutil.which("desktop-file-validate")
    if validator is None:
        problems.append("desktop-file-validate is missing; install desktop-file-utils")
    else:
        result = subprocess.run(
            [validator, str(desktop)], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            detail = (result.stdout + result.stderr).strip()
            problems.append(f"desktop-file-validate: {detail or result.returncode}")

    entry = read_entry(desktop)
    missing = [key for key in _REQUIRED if not entry.get(key)]
    if missing:
        return [*problems, f"the entry has no {'=, '.join(missing)}="]

    executable = entry["Exec"].split()[0]
    if shutil.which(executable) is None:
        problems.append(f"Exec={entry['Exec']} does not resolve to an installed program")

    icons = sorted(icon_root.glob(f"*/apps/{entry['Icon']}.png"))
    if not icons:
        return [*problems, f"Icon={entry['Icon']} names no file under {icon_root}"]
    for icon in icons:
        try:
            width, height = png_size(icon)
        except ValueError as error:
            problems.append(str(error))
            continue
        named = icon.parent.parent.name
        if named != f"{width}x{height}":
            problems.append(f"{icon} is {width}x{height}, the folder says {named}")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Check the installed launcher; return 0 when it is usable."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--desktop", type=Path, default=DESKTOP, help=f"the installed entry (default: {DESKTOP})"
    )
    parser.add_argument(
        "--icon-root",
        type=Path,
        default=ICON_ROOT,
        help=f"the hicolor theme (default: {ICON_ROOT})",
    )
    args = parser.parse_args(argv)

    problems = check(args.desktop, args.icon_root)
    if problems:
        print("The launcher the package installs is not usable:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"{args.desktop} is valid, its Exec and Icon resolve, and the icon matches its folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
