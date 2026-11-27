#!/usr/bin/env python3
"""Write the Windows version resource that PyInstaller puts into the app exe.

Usage (run from the repo root, before the spec):
    python tools/build/make_version_resource.py [--version 0.5.7] [--output PATH]

Without ``--version`` the release version comes from ``$VERSION`` — the same string the
workflow hands to ``makensis`` — and, failing that, from the stamped
``teachers_teammate.__version__``.  The file goes to
``teachers_teammate/_build/version_info.txt`` by default, which the spec reads on Windows.

Why the strings are what they are: the product name and the product version are the same in the
app exe, the setup and the uninstaller, so the values here are the ones ``tools/packaging/windows.nsi`` compiles in.  ``ProductVersion`` is therefore the
release string (``0.5.7-rc.1``), not the PEP 440 form of the stamped version (``0.5.7rc1``);
only the four-number fixed fields are derived from it.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT = ROOT / "teachers_teammate" / "_build" / "version_info.txt"

#: The product name, identical in the exe, the setup and the uninstaller (``APP_NAME`` in
#: ``windows.nsi``).  A test keeps the two equal.
PRODUCT_NAME = "Teacher's Teammate"
#: ``APP_PUBLISHER`` in ``windows.nsi``: what Programs and Features shows as the publisher.
COMPANY_NAME = "Teacher's Teammate Contributors"
FILE_DESCRIPTION = "Teacher's Teammate"
LEGAL_COPYRIGHT = "\u00a9 2026 Teacher's Teammate contributors \u00b7 GPL-3.0-or-later"
ORIGINAL_FILENAME = "teachers-teammate.exe"

#: The leading release numbers of a version, with or without a ``v`` and whatever follows them
#: (``0.5.7``, ``v0.5.7``, ``0.5.7-rc.1``, ``0.5.7rc1``).
_RELEASE = re.compile(r"^v?(\d+(?:\.\d+)*)")


def numeric_version(version: str) -> tuple[int, int, int, int]:
    """Return the four numbers Windows keeps in the fixed part of the resource.

    ``0.5.7`` and ``0.5.7-rc.1`` both give ``(0, 5, 7, 0)``: the pre-release label has no place
    in a fixed field, which holds numbers only.
    """
    match = _RELEASE.match(version.strip())
    if not match:
        msg = f"'{version}' does not start with a version number"
        raise ValueError(msg)
    numbers = [int(part) for part in match.group(1).split(".")]
    if len(numbers) > 4:
        msg = f"'{version}' has more than four numbers, which a Windows version cannot hold"
        raise ValueError(msg)
    padded = [*numbers, *([0] * (4 - len(numbers)))]
    return (padded[0], padded[1], padded[2], padded[3])


def fields(version: str) -> dict[str, str]:
    """Return the string fields of the resource, in the order Windows shows them."""
    return {
        "CompanyName": COMPANY_NAME,
        "FileDescription": FILE_DESCRIPTION,
        "FileVersion": version,
        "LegalCopyright": LEGAL_COPYRIGHT,
        "OriginalFilename": ORIGINAL_FILENAME,
        "ProductName": PRODUCT_NAME,
        "ProductVersion": version,
    }


def version_resource(version: str) -> str:
    """Return the ``VSVersionInfo`` text PyInstaller reads for ``EXE(version=…)``.

    The strings are written as JSON string literals: they are valid Python, and the copyright
    sign and the middle dot stay escaped, so the file is plain ASCII whatever reads it.
    """
    numbers = numeric_version(version)
    strings = ",\n".join(
        f"            StringStruct({json.dumps(name)}, {json.dumps(value)})"
        for name, value in fields(version).items()
    )
    return (
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers={numbers},\n"
        f"    prodvers={numbers},\n"
        "    mask=0x3f,\n"
        "    flags=0x0,\n"
        "    OS=0x40004,\n"
        "    fileType=0x1,\n"
        "    subtype=0x0,\n"
        "    date=(0, 0)\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo(\n"
        "      [\n"
        "        StringTable(\n"
        "          '040904B0',\n"
        "          [\n"
        f"{strings}\n"
        "          ])\n"
        "      ]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )


def release_version() -> str:
    """Return the version to write: ``$VERSION``, else the stamped ``__version__``."""
    from_env = os.environ.get("VERSION", "").strip()
    if from_env:
        return from_env
    sys.path.insert(0, str(ROOT))
    from teachers_teammate import __version__  # noqa: PLC0415

    return __version__


def write(version: str, output: Path) -> Path:
    """Write the resource for *version* to *output* and return the path."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(version_resource(version), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--version",
        default="",
        help="the release version, e.g. 0.5.7 or 0.5.7-rc.1 (default: $VERSION, then __version__)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"where to write the resource (default: {DEFAULT_OUTPUT.relative_to(ROOT)})",
    )
    args = parser.parse_args()
    version = args.version.strip() or release_version()
    path = write(version, args.output)
    print(f"    version resource for {version} -> {path}")


if __name__ == "__main__":
    main()
