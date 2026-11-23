"""NOTICE, read: its sections, the Qt modules it names, the versions it promises.

``NOTICE`` is hand-written and a test keeps it on the locked versions.  Two tools read it —
``tests/unit/test_license_inventory.py`` for the versions, and ``tools/release/mirror_sources.py``
for the list of files a sources release has to hold — so the reading lives here, once, and needs
nothing but the standard library.
"""

from __future__ import annotations

import re

SOURCE_CODE = "Source code"
"""The section that names the sources of the LGPL and MPL components."""

_UNDERLINE = re.compile(r"-+")
"""What sits under a heading: a line of dashes, nothing else."""

_QT_MODULES = re.compile(r"^\s*\((?P<modules>[a-z0-9][a-z0-9, ]*)\)\s*$", re.MULTILINE)
"""The ``(qtbase, qtsvg, qtimageformats, qtwayland)`` line of the Qt entry."""

_FFMPEG = re.compile(
    r"^FFmpeg (?P<version>\S+), inside opencv-python-headless (?P<opencv>\S+)", re.MULTILINE
)
"""The FFmpeg entry: the FFmpeg version and the wheel it rides in."""


def sections(text: str) -> dict[str, str]:
    """Return *text*'s sections: heading -> body, headings and underlines left out."""
    lines = text.splitlines()
    headings = [
        index
        for index, line in enumerate(lines[:-1])
        if line.strip() and _UNDERLINE.fullmatch(lines[index + 1])
    ]
    found: dict[str, str] = {}
    for position, index in enumerate(headings):
        end = headings[position + 1] if position + 1 < len(headings) else len(lines)
        found[lines[index].strip()] = "\n".join(lines[index + 2 : end])
    return found


def section(text: str, title: str) -> str:
    """Return the body of the section *title*.

    Raises:
        KeyError: *text* has no section of that name.
    """
    return sections(text)[title]


def qt_modules(text: str) -> tuple[str, ...]:
    """Return the Qt modules NOTICE names as the sources of the bundles' Qt libraries.

    Raises:
        ValueError: the source section carries no comma-separated list of modules.
    """
    match = _QT_MODULES.search(section(text, SOURCE_CODE))
    if match is None:
        raise ValueError("NOTICE's source section names no list of Qt modules")
    return tuple(name.strip() for name in match.group("modules").split(",") if name.strip())


def ffmpeg_versions(text: str) -> tuple[str, str]:
    """Return the FFmpeg version and the ``opencv-python-headless`` version NOTICE names.

    Raises:
        ValueError: the source section carries no ``FFmpeg <version>, inside ...`` line.
    """
    match = _FFMPEG.search(section(text, SOURCE_CODE))
    if match is None:
        raise ValueError("NOTICE's source section names no FFmpeg version")
    return match.group("version"), match.group("opencv")
