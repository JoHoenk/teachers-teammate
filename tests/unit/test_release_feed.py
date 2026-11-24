"""The one place the update check speaks HTTPS: GitHub's release feed."""

from __future__ import annotations

from email.message import Message
from http.client import BadStatusLine, IncompleteRead
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from teachers_teammate.exceptions import ReleaseFeedError
from teachers_teammate.infrastructure.release_feed import GitHubReleaseFeed

_RELEASE_PAGE = "https://github.com/JoHoenk/teachers-teammate/releases/tag/v1.0.0"

_URLOPEN = "teachers_teammate.infrastructure.release_feed.urlopen"


def _response(body: object) -> MagicMock:
    """Return the answer ``urlopen`` gives for *body*, as JSON."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _refused(status: int, remaining: str = "") -> HTTPError:
    """Return the error ``urlopen`` raises for *status*, with GitHub's rate-limit header."""
    headers = Message()
    headers["x-ratelimit-remaining"] = remaining
    return HTTPError("https://api.github.com/", status, "denied", headers, None)


def test_the_tag_and_the_page_come_back() -> None:
    """
    Given  GitHub answers with the newest release
    When   the feed is read
    Then   its tag and its page come back
    """
    body = {"tag_name": "v1.0.0", "html_url": _RELEASE_PAGE}

    with patch(_URLOPEN, return_value=_response(body)):
        assert GitHubReleaseFeed().latest() == ("v1.0.0", _RELEASE_PAGE)


def test_a_release_without_a_page_still_comes_back() -> None:
    """
    Given  GitHub answers the tag but no page
    When   the feed is read
    Then   the empty page is handed on, and the domain rule refuses to offer it
    """
    with patch(_URLOPEN, return_value=_response({"tag_name": "v1.0.0"})):
        assert GitHubReleaseFeed().latest() == ("v1.0.0", "")


def test_a_rate_limited_answer_names_the_limit() -> None:
    """
    Given  GitHub refuses with 403 and no request left in the hour
    When   the feed is read
    Then   the error carries the status and says it is the limit
    """
    with patch(_URLOPEN, side_effect=_refused(403, remaining="0")):
        with pytest.raises(ReleaseFeedError) as raised:
            GitHubReleaseFeed().latest()

    assert raised.value.status == 403
    assert raised.value.rate_limited is True


def test_a_refusal_that_is_no_rate_limit_says_so() -> None:
    """
    Given  GitHub refuses with 404 and requests left in the hour
    When   the feed is read
    Then   the error carries the status and is not called a rate limit
    """
    with patch(_URLOPEN, side_effect=_refused(404, remaining="57")):
        with pytest.raises(ReleaseFeedError) as raised:
            GitHubReleaseFeed().latest()

    assert raised.value.status == 404
    assert raised.value.rate_limited is False


@pytest.mark.parametrize(
    "failure",
    [
        URLError("no route to host"),
        TimeoutError("timed out"),
        OSError("connection refused"),
        IncompleteRead(b"{"),
        BadStatusLine("garbage"),
    ],
    ids=["unreachable", "timeout", "refused", "cut-off", "bad-status"],
)
def test_no_answer_carries_no_status(failure: Exception) -> None:
    """
    Given  the request never gets an answer: unreachable, a timeout, a refused or cut connection,
           a reply that is no HTTP
    When   the feed is read
    Then   the error carries no status
    """
    with patch(_URLOPEN, side_effect=failure):
        with pytest.raises(ReleaseFeedError) as raised:
            GitHubReleaseFeed().latest()

    assert raised.value.status is None


def test_an_answer_that_is_no_release_object_fails() -> None:
    """
    Given  GitHub answers a JSON list where the object of one release belongs
    When   the feed is read
    Then   the read fails, so no release is made up out of it
    """
    with patch(_URLOPEN, return_value=_response([{"tag_name": "v1.0.0"}])):
        with pytest.raises(ReleaseFeedError):
            GitHubReleaseFeed().latest()


def test_an_answer_that_is_not_json_fails() -> None:
    """
    Given  the answer is not JSON at all, as a proxy's error page is not
    When   the feed is read
    Then   the read fails
    """
    resp = MagicMock()
    resp.read.return_value = b"<html>blocked</html>"
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)

    with patch(_URLOPEN, return_value=resp):
        with pytest.raises(ReleaseFeedError):
            GitHubReleaseFeed().latest()
