"""Background update check against GitHub Releases."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

import teachers_teammate as _pkg

_RELEASES_API = "https://api.github.com/repos/JoHoenk/teachers-teammate/releases/latest"

# Threads that have been started but not yet finished. Holding a reference here keeps a
# running QThread from being destroyed (which makes Qt abort the process) when the window
# that launched it is closed before the network request returns.
_RUNNING: set[UpdateCheckThread] = set()


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
            if tag and url and _version_tuple(tag) > _version_tuple(_pkg.__version__):
                self.update_available.emit(tag.lstrip("v"), url)
        except Exception:  # noqa: BLE001  # update check is best-effort; network/parse errors are silently ignored
            pass
