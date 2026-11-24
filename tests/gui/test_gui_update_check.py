"""The update check's delivery: the ask runs off the GUI thread, the answer lands on it."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.releases import ReleaseOffer
from teachers_teammate.gui._background import wait_for_background_tasks
from teachers_teammate.gui._update_check import check_for_update
from teachers_teammate.texts.app import update_available

_RELEASE_PAGE = "https://github.com/JoHoenk/teachers-teammate/releases/tag/v1.0.0"
_OFFER = ReleaseOffer(version="1.0.0", url=_RELEASE_PAGE)


def test_the_banner_escape_offer_stays_a_plain_text() -> None:
    """
    Given  a version and a URL that carry markup
    When   the banner text is built
    Then   both are escaped, so the answer from the network cannot shape the banner
    """
    banner = update_available("<b>1.0</b>", "https://github.com/x?a=1&b=<script>")

    assert "<b>1.0</b>" not in banner
    assert "&lt;b&gt;1.0&lt;/b&gt;" in banner
    assert "&amp;b=&lt;script&gt;" in banner


@pytest.mark.gui
def test_the_answer_arrives_on_the_gui_thread(qtbot) -> None:
    """
    Given  the check run on the background pool
    When   the service offers a newer release
    Then   the callback is called on the GUI thread with the version and its URL
    """
    import threading  # noqa: PLC0415

    offered: list[tuple[str, str, int]] = []

    def on_available(version: str, url: str) -> None:
        offered.append((version, url, threading.get_ident()))

    check_for_update(lambda: _OFFER, on_available)
    assert wait_for_background_tasks(5000)
    qtbot.waitUntil(lambda: bool(offered), timeout=5000)

    version, url, thread = offered[0]
    assert (version, url) == ("1.0.0", _RELEASE_PAGE)
    assert thread == threading.get_ident()


@pytest.mark.gui
def test_nothing_is_reported_when_there_is_no_newer_release(qtbot) -> None:
    """
    Given  the installed version is the latest release
    When   the check runs
    Then   the callback is never called
    """
    offered: list[tuple[str, str]] = []

    check_for_update(lambda: None, lambda version, url: offered.append((version, url)))
    assert wait_for_background_tasks(5000)
    qtbot.wait(50)

    assert offered == []


@pytest.mark.gui
def test_a_failing_ask_reports_nothing(qtbot) -> None:
    """
    Given  the ask itself fails, because the service was not ready for it
    When   the check runs
    Then   the callback is never called and the failure stays off the GUI thread
    """

    def boom() -> ReleaseOffer | None:
        raise RuntimeError("no feed")

    offered: list[tuple[str, str]] = []

    check_for_update(boom, lambda version, url: offered.append((version, url)))
    assert wait_for_background_tasks(5000)
    qtbot.wait(50)

    assert offered == []
