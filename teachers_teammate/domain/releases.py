"""Which published release is newer than this build, and whether it may be offered.

Pure: the answer of the release feed goes in, the offer or nothing comes out.  The rules are the
ones the app has always used — a development build is never offered a release, an installed
version that cannot be read neither, a pre-release sorts below the release it belongs to, and
only a page of this project's own releases is opened.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

#: Where the newest release of this project is asked for.
RELEASES_API = "https://api.github.com/repos/JoHoenk/teachers-teammate/releases/latest"
#: Every release page of this project is under this prefix.  The banner is a link, and the
#: answer comes from the network: what points elsewhere is not opened.
RELEASE_URL_PREFIX = "https://github.com/JoHoenk/teachers-teammate/"
#: The version of a build nobody stamped, which sorts below every release.
DEVELOPMENT_VERSION = "0.0.0.dev0"

# A release version, either as the app writes it (0.5.7rc1, PEP 440) or as a tag reads
# (v0.5.7-rc.1): the pre-release label sits right after the release part or behind a dash.
# A development, post or local suffix (0.5.7.dev0, 0.5.7.post1, 0.5.7+abc) is read as the
# plain release, so such a build is never offered the release it already is. The number
# behind a dashed label is either absent (-pre) or whole (-rc1, -rc.1), never a bare dot.
_VERSION_PATTERN = re.compile(
    r"^v?(?P<release>\d+(?:\.\d+)*)"
    r"(?:-(?P<label>alpha|beta|rc|pre)(?:\.?\d+)?|(?P<pep_label>a|b|rc)\d+)?"
    r"(?:\.(?:dev|post)\d+)*(?:\+[0-9A-Za-z.]+)?$"
)


@dataclass(frozen=True)
class ReleaseOffer:
    """A published release that is newer than the one this app is."""

    version: str
    """The release version, as the banner names it: the tag without its ``v``."""
    url: str
    """Its release page, under :data:`RELEASE_URL_PREFIX`."""


def version_tuple(tag: str) -> tuple[int, ...]:
    """Convert a version string like '1.2.3', 'v1.2.3-rc.1' or '1.2.3rc1' to a comparable tuple.

    Trailing zeros of the release part are dropped, so '1.2' and '1.2.0' are the same version.
    The release part is followed by a pre-release flag: 0 for a final release, -1 for a
    pre-release, so a pre-release sorts below the release it belongs to. A string that is not
    a version at all gives (), which compares below every version.
    """
    match = _VERSION_PATTERN.match(tag.strip())
    if not match:
        return ()
    release = [int(part) for part in match.group("release").split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    is_prerelease = match.group("label") is not None or match.group("pep_label") is not None
    return (*release, -1 if is_prerelease else 0)


def is_release_url(url: str) -> bool:
    """Whether *url* names a page of this project's own releases."""
    return url.startswith(RELEASE_URL_PREFIX) and len(url) > len(RELEASE_URL_PREFIX)


def newer_release(tag: str, url: str, installed: str) -> ReleaseOffer | None:
    """Return the offer for the release *tag* at *url*, or ``None`` when there is nothing to offer.

    A development build is offered nothing: its placeholder sorts below every release, so every
    answer would be an offer.  An installed version that cannot be read gives no banner either:
    better silent than offering the same release on every launch.
    """
    if installed == DEVELOPMENT_VERSION:
        return None
    latest = version_tuple(tag)
    current = version_tuple(installed)
    if not (tag and is_release_url(url) and latest and current and latest > current):
        return None
    return ReleaseOffer(version=tag.lstrip("v"), url=url)
