"""NOTICE, read: its sections, the Qt modules it names, the parts inside the packages it names.

``NOTICE`` is hand-written and a test keeps it on the locked versions.  Two tools read it —
``tests/unit/test_license_inventory.py`` for the versions, and ``tools/release/mirror_sources.py``
for the list of files a sources release has to hold — so the reading lives here, once, and needs
nothing but the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

SOURCE_CODE = "Source code"
"""The section that names the sources of the LGPL and MPL components."""

_UNDERLINE = re.compile(r"-+")
"""What sits under a heading: a line of dashes, nothing else."""

_QT_MODULES = re.compile(r"^\s*\((?P<modules>[a-z0-9][a-z0-9, ]*)\)\s*$", re.MULTILINE)
"""The ``(qtbase, qtsvg, qtimageformats, qtwayland)`` line of the Qt entry."""

_VENDORED = re.compile(
    r"^(?P<part>\S+) (?P<version>\S+), inside (?P<package>\S+) (?P<package_version>\S+?)"
    r"(?: \((?P<platforms>[^)]*)\))?:$",
    re.MULTILINE,
)
"""The line that names a part inside a package: ``FFmpeg 9.0.2, inside opencv-python-headless``."""


@dataclass(frozen=True)
class Vendored:
    """A part NOTICE says rides inside a bundled package, with the versions it was written for."""

    part: str
    version: str
    package: str
    package_version: str
    platforms: str = ""


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


def vendored_entries(text: str) -> tuple[Vendored, ...]:
    """Return the parts NOTICE says ride inside a bundled package, in the order it writes them."""
    return tuple(
        Vendored(
            match.group("part"),
            match.group("version"),
            match.group("package"),
            match.group("package_version"),
            match.group("platforms") or "",
        )
        for match in _VENDORED.finditer(section(text, SOURCE_CODE))
    )
