"""Background update check against GitHub Releases."""

from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import QThread, Signal

import teachers_teammate as _pkg

_RELEASES_API = "https://api.github.com/repos/JoHoenk/teachers-teammate/releases/latest"

# Threads that have been started but not yet finished. Holding a reference here keeps a
# running QThread from being destroyed (which makes Qt abort the process) when the window
# that launched it is closed before the network request returns.
_RUNNING: set[UpdateCheckThread] = set()

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


class UpdateCheckThread(QThread):
    """Check GitHub Releases in the background and emit *update_available* if newer."""

    update_available = Signal(str, str)  # (version_tag, html_url)

    def start(self, *args: Any) -> None:
        """Start the thread and keep it alive until it has finished."""
        _RUNNING.add(self)
        self.finished.connect(self._release)
        super().start(*args)

    def _release(self) -> None:
        self.wait()
        _RUNNING.discard(self)
        self.deleteLater()

    def run(self) -> None:
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
            # An installed version that cannot be read gives no banner: better silent than
            # offering the same release on every launch.
            if tag and url and latest and installed and latest > installed:
                self.update_available.emit(tag.lstrip("v"), url)
        except Exception:  # noqa: BLE001  # update check is best-effort; network/parse errors are silently ignored
            pass
