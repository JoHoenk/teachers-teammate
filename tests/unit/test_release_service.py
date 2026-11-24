"""The update check as a service: a fake feed stands in for GitHub."""

from __future__ import annotations

from teachers_teammate.application.releases import ReleaseService
from teachers_teammate.domain.releases import ReleaseOffer
from teachers_teammate.exceptions import ReleaseFeedError

_RELEASE_PAGE = "https://github.com/JoHoenk/teachers-teammate/releases/tag/v1.0.0"


class _FakeFeed:
    """A feed that answers what it was given, or raises it."""

    def __init__(self, answer: tuple[str, str] | Exception) -> None:
        self._answer = answer
        self.asked = 0

    def latest(self) -> tuple[str, str]:
        """Return the scripted answer, or raise the scripted failure."""
        self.asked += 1
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def test_a_newer_release_is_offered() -> None:
    """
    Given  the feed names v1.0.0 and this build is 0.9.0
    When   the service is asked
    Then   the release is offered with its version and page, and the feed was asked once
    """
    feed = _FakeFeed(("v1.0.0", _RELEASE_PAGE))

    assert ReleaseService(feed=feed, installed_version="0.9.0").newer_release() == ReleaseOffer(
        version="1.0.0", url=_RELEASE_PAGE
    )
    assert feed.asked == 1


def test_the_newest_release_offers_nothing() -> None:
    """
    Given  the feed names the release this build already is
    When   the service is asked
    Then   nothing is offered
    """
    feed = _FakeFeed(("v0.9.0", _RELEASE_PAGE))

    assert ReleaseService(feed=feed, installed_version="0.9.0").newer_release() is None


def test_no_network_offers_nothing_and_writes_one_line(caplog) -> None:
    """
    Given  the feed cannot be read at all
    When   the service is asked
    Then   nothing is offered and one info line names the failure
    """
    feed = _FakeFeed(ReleaseFeedError())

    with caplog.at_level("INFO", logger="teachers_teammate.application.releases"):
        assert ReleaseService(feed=feed, installed_version="0.9.0").newer_release() is None

    assert len(caplog.records) == 1
    line = caplog.records[0].getMessage()
    assert "ReleaseFeedError" in line
    assert caplog.records[0].levelname == "INFO"


def test_a_refused_answer_writes_the_status(caplog) -> None:
    """
    Given  GitHub refused the request with 403, its hourly limit
    When   the service is asked
    Then   the log line carries the status, so a log a teacher sends shows the reason
    """
    feed = _FakeFeed(ReleaseFeedError(403, rate_limited=True))

    with caplog.at_level("INFO", logger="teachers_teammate.application.releases"):
        assert ReleaseService(feed=feed, installed_version="0.9.0").newer_release() is None

    line = caplog.records[0].getMessage()
    assert "403" in line
    assert "api.github.com" not in line
