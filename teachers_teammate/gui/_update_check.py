"""The update check on the background pool: the ask off the GUI thread, the answer on it."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject

from ..domain.releases import ReleaseOffer
from ._background import run_in_background


def check_for_update(
    newer_release: Callable[[], ReleaseOffer | None],
    on_available: Callable[[str, str], None],
    *,
    owner: QObject | None = None,
) -> None:
    """Ask *newer_release* off the GUI thread; *on_available* gets a newer ``(version, url)``.

    Which release is newer, and whether one is offered at all, is the service's answer; this
    function only decides where the ask runs and where the answer lands.
    """

    def deliver(result: object, error: BaseException | None) -> None:
        if error is None and isinstance(result, ReleaseOffer):
            on_available(result.version, result.url)

    run_in_background(newer_release, deliver, owner=owner, network=True)
