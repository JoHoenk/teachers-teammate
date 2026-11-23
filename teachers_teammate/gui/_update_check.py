"""Update check against GitHub Releases, run on the background pool."""

from __future__ import annotations

from collections.abc import Callable
import re

from PySide6.QtCore import QObject

import teachers_teammate as _pkg

from ._background import run_in_background

_RELEASES_API = "https://api.github.com/repos/JoHoenk/teachers-teammate/releases/latest"
#: Every release page of this project is under this prefix.  The banner is a link, and the
# answer comes from the network: what points elsewhere is not opened.
_RELEASE_URL_PREFIX = "https://github.com/JoHoenk/teachers-teammate/"

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


def _version_tuple(tag: str) -> tuple[int, ...]:
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


def _is_release_url(url: str) -> bool:
    """Whether *url* names a page of this project's own releases."""
    return url.startswith(_RELEASE_URL_PREFIX) and len(url) > len(_RELEASE_URL_PREFIX)


def fetch_newer_release() -> tuple[str, str] | None:
    """Return the release ``(version, url)`` that is newer than this one, or ``None``.

    Best effort: no answer, no network and an unreadable answer all give ``None``.  An
    installed version that cannot be read gives no banner either: better silent than offering
    the same release on every launch.
    """
    try:
        import json  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        req = urllib.request.Request(
            _RELEASES_API,
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())

        tag = data.get("tag_name", "")
        url = data.get("html_url", "")
        latest = _version_tuple(tag)
        installed = _version_tuple(_pkg.__version__)
        if tag and _is_release_url(url) and latest and installed and latest > installed:
            return tag.lstrip("v"), url
    except Exception:  # noqa: BLE001  # update check is best-effort; network/parse errors are silently ignored
        pass
    return None


def check_for_update(
    on_available: Callable[[str, str], None], *, owner: QObject | None = None
) -> None:
    """Ask GitHub Releases off the GUI thread; *on_available* gets a newer ``(version, url)``."""

    def deliver(result: object, error: BaseException | None) -> None:
        if error is None and isinstance(result, tuple) and len(result) == 2:
            on_available(result[0], result[1])

    run_in_background(fetch_newer_release, deliver, owner=owner, network=True)
