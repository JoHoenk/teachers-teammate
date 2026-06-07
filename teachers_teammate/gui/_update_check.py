"""Background update check against GitHub Releases."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

import teachers_teammate as _pkg

_RELEASES_API = "https://api.github.com/repos/JoHoenk/teachers-teammate/releases/latest"


def _version_tuple(tag: str) -> tuple[int, ...]:
    """Convert a version string like '1.2.3' or 'v1.2.3' to a comparable tuple."""
    tag = tag.lstrip("v").split("-", 1)[0]
    try:
        return tuple(int(x) for x in tag.split(".") if x.isdigit())
    except ValueError:
        return ()


class UpdateCheckThread(QThread):
    """Check GitHub Releases in the background and emit *update_available* if newer."""

    update_available = Signal(str, str)  # (version_tag, html_url)

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
            if tag and url and _version_tuple(tag) > _version_tuple(_pkg.__version__):
                self.update_available.emit(tag.lstrip("v"), url)
        except Exception:  # noqa: BLE001  # update check is best-effort; network/parse errors are silently ignored
            pass
