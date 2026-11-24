"""Tests for the update check against GitHub Releases."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from teachers_teammate.gui import _update_check
from teachers_teammate.gui._background import wait_for_background_tasks
from teachers_teammate.gui._update_check import (
    _version_tuple,
    check_for_update,
    fetch_newer_release,
)
from teachers_teammate.texts.app import update_available

_RELEASE_PAGE = "https://github.com/JoHoenk/teachers-teammate/releases/tag/v1.0.0"
_RELEASES_API = "https://api.github.com/repos/JoHoenk/teachers-teammate/releases/latest"


# ── _version_tuple ────────────────────────────────────────────────────────────


def test_version_tuple_strips_v_prefix() -> None:
    """
    Given  a version tag with a 'v' prefix
    When   _version_tuple() is called
    Then   the prefix is stripped, each part is an int, and the final-release flag is 0
    """
    assert _version_tuple("v1.2.3") == (1, 2, 3, 0)


def test_version_tuple_strips_prerelease_suffix() -> None:
    """
    Given  a version tag with a pre-release suffix separated by '-'
    When   _version_tuple() is called
    Then   the release part is returned with the pre-release flag -1
    """
    assert _version_tuple("1.2.3-rc1") == (1, 2, 3, -1)


def test_version_tuple_reads_a_pep440_prerelease() -> None:
    """
    Given  a version as the app writes it, with the label right after the release part
    When   _version_tuple() is called
    Then   it is read as a pre-release of that release
    """
    assert _version_tuple("0.5.7rc1") == (0, 5, 7, -1)


def test_version_tuple_reads_a_tag_with_a_dotted_prerelease() -> None:
    """
    Given  a release tag whose pre-release number follows a dot
    When   _version_tuple() is called
    Then   it is read as a pre-release of that release
    """
    assert _version_tuple("v0.5.7-rc.1") == (0, 5, 7, -1)


def test_a_prerelease_sorts_below_its_release() -> None:
    """
    Given  the pre-release and the final release of the same version
    When   both are converted
    Then   the pre-release compares lower, so a final release is offered to an rc tester
    """
    assert _version_tuple("0.5.7rc1") < _version_tuple("v0.5.7")


def test_version_tuple_bare_version() -> None:
    """
    Given  a plain version string with no prefix or suffix
    When   _version_tuple() is called
    Then   the parts are returned as ints, trailing zeros of the release dropped
    """
    assert _version_tuple("2.0.0") == (2, 0)


def test_version_tuple_treats_trailing_zeros_as_the_same_version() -> None:
    """
    Given  '0.6' and the tag 'v0.6.0'
    When   both are converted
    Then   they are equal, so the tag is not offered as an update
    """
    assert _version_tuple("0.6") == _version_tuple("v0.6.0")


@pytest.mark.parametrize("raw", ["0.5.7.dev0", "0.5.7.post1", "0.5.7+abc", "0.5.7rc1.dev2"])
def test_version_tuple_reads_dev_post_and_local_versions(raw: str) -> None:
    """
    Given  a development, post or local version of a release
    When   _version_tuple() is called
    Then   it compares like that release (rc ones like their pre-release), never as unreadable
    """
    expected = _version_tuple("0.5.7rc1") if "rc1" in raw else _version_tuple("0.5.7")
    assert _version_tuple(raw) == expected


@pytest.mark.parametrize("raw", ["v0.5.7-rc.", "v0.5.7-", "v0.5.7-rcx"])
def test_version_tuple_rejects_a_label_without_a_whole_number(raw: str) -> None:
    """
    Given  a tag with a dangling dot or an unknown label
    When   _version_tuple() is called
    Then   an empty tuple is returned
    """
    assert _version_tuple(raw) == ()


def test_version_tuple_invalid_returns_empty() -> None:
    """
    Given  a string that contains no numeric version segments
    When   _version_tuple() is called
    Then   an empty tuple is returned
    """
    assert _version_tuple("not-a-version") == ()


def test_version_tuple_empty_string_returns_empty() -> None:
    """
    Given  an empty string
    When   _version_tuple() is called
    Then   an empty tuple is returned
    """
    assert _version_tuple("") == ()


def _fake_response(
    tag: str, url: str = "https://github.com/JoHoenk/teachers-teammate/releases/tag/v1.0.0"
) -> MagicMock:
    body = json.dumps({"tag_name": tag, "html_url": url}).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_a_newer_release_is_offered() -> None:
    """
    Given  the installed version is 0.9.0 and the latest GitHub release is v1.0.0
    When   the release is fetched
    Then   its version without the tag prefix and its URL come back
    """
    with (
        patch("teachers_teammate.__version__", "0.9.0"),
        patch("urllib.request.urlopen", return_value=_fake_response("v1.0.0")),
    ):
        assert fetch_newer_release() == ("1.0.0", _RELEASE_PAGE)


def test_the_same_version_is_not_offered() -> None:
    """
    Given  the installed version equals the latest GitHub release (both 1.0.0)
    When   the release is fetched
    Then   nothing is offered
    """
    with (
        patch("teachers_teammate.__version__", "1.0.0"),
        patch("urllib.request.urlopen", return_value=_fake_response("v1.0.0")),
    ):
        assert fetch_newer_release() is None


def test_an_older_release_is_not_offered() -> None:
    """
    Given  the installed version (1.1.0) is newer than the latest GitHub release (v1.0.0)
    When   the release is fetched
    Then   nothing is offered
    """
    with (
        patch("teachers_teammate.__version__", "1.1.0"),
        patch("urllib.request.urlopen", return_value=_fake_response("v1.0.0")),
    ):
        assert fetch_newer_release() is None


def test_an_rc_build_is_not_offered_the_previous_release() -> None:
    """
    Given  the installed version is the pre-release 0.5.7rc1 and the latest release is v0.5.6
    When   the release is fetched
    Then   nothing is offered: the rc is newer than that release
    """
    with (
        patch("teachers_teammate.__version__", "0.5.7rc1"),
        patch("urllib.request.urlopen", return_value=_fake_response("v0.5.6")),
    ):
        assert fetch_newer_release() is None


def test_an_rc_build_is_offered_the_final_release() -> None:
    """
    Given  the installed version is the pre-release 0.5.7rc1 and the latest release is v0.5.7
    When   the release is fetched
    Then   it is offered with its version 0.5.7
    """
    with (
        patch("teachers_teammate.__version__", "0.5.7rc1"),
        patch("urllib.request.urlopen", return_value=_fake_response("v0.5.7")),
    ):
        assert fetch_newer_release() == ("0.5.7", _RELEASE_PAGE)


def test_a_final_build_is_offered_the_next_release() -> None:
    """
    Given  the installed version is 0.5.6 and the latest release is v0.5.7
    When   the release is fetched
    Then   it is offered with its version 0.5.7
    """
    with (
        patch("teachers_teammate.__version__", "0.5.6"),
        patch("urllib.request.urlopen", return_value=_fake_response("v0.5.7")),
    ):
        assert fetch_newer_release() == ("0.5.7", _RELEASE_PAGE)


def test_a_tag_that_is_not_a_version_is_not_offered() -> None:
    """
    Given  the latest release carries a tag that is not a version
    When   the release is fetched
    Then   nothing is offered
    """
    with (
        patch("teachers_teammate.__version__", "0.5.6"),
        patch("urllib.request.urlopen", return_value=_fake_response("nightly")),
    ):
        assert fetch_newer_release() is None


@pytest.mark.parametrize("installed", ["0.5.7.dev0", "0.5.7+abc", "0.5.7.post1", "garbage"])
def test_a_build_of_the_latest_release_is_not_offered_it(installed: str) -> None:
    """
    Given  an installed version that is a dev, post or local build of v0.5.7, or unreadable
    When   the release is fetched and v0.5.7 is the latest release
    Then   nothing is offered
    """
    with (
        patch("teachers_teammate.__version__", installed),
        patch("urllib.request.urlopen", return_value=_fake_response("v0.5.7")),
    ):
        assert fetch_newer_release() is None


def test_a_development_build_is_not_offered_a_release() -> None:
    """
    Given  a development build (0.0.0.dev0) and a newer release v1.0.0
    When   the release is fetched
    Then   nothing is offered: the placeholder sorts below every release
    """
    with (
        patch("teachers_teammate.__version__", "0.0.0.dev0"),
        patch("urllib.request.urlopen", return_value=_fake_response("v1.0.0")),
    ):
        assert fetch_newer_release() is None


def test_a_released_dev_version_is_offered_a_newer_release() -> None:
    """
    Given  a stamped release whose version carries a .dev suffix (0.6.0.dev1) and a newer v0.7.0
    When   the release is fetched
    Then   it is offered: only the unstamped placeholder is a development build
    """
    with (
        patch("teachers_teammate.__version__", "0.6.0.dev1"),
        patch("urllib.request.urlopen", return_value=_fake_response("v0.7.0")),
    ):
        offered = fetch_newer_release()

    assert offered is not None
    assert offered[0] == "0.7.0"


def test_a_development_build_asks_no_network() -> None:
    """
    Given  a development build
    When   the release is fetched
    Then   GitHub is not contacted at all
    """
    with (
        patch("teachers_teammate.__version__", "0.0.0.dev0"),
        patch("urllib.request.urlopen") as urlopen,
    ):
        assert fetch_newer_release() is None

    urlopen.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/rel",
        "https://github.com/someone-else/teachers-teammate/releases/tag/v1.0.0",
        "http://github.com/JoHoenk/teachers-teammate/releases/tag/v1.0.0",
        "https://github.com/JoHoenk/teachers-teammate/",
    ],
)
def test_a_release_page_that_is_not_this_projects_is_not_offered(url: str) -> None:
    """
    Given  the release feed names a newer version but points outside this project's releases
    When   the release is fetched
    Then   nothing is offered: the banner opens only a page of this repository
    """
    with (
        patch("teachers_teammate.__version__", "0.9.0"),
        patch("urllib.request.urlopen", return_value=_fake_response("v1.0.0", url)),
    ):
        assert fetch_newer_release() is None


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


def test_a_network_failure_is_silent() -> None:
    """
    Given  urllib raises an OSError when contacting the GitHub API
    When   the release is fetched
    Then   nothing is offered, no exception escapes, and GitHub was asked
    """
    with (
        patch("teachers_teammate.__version__", "0.9.0"),
        patch("urllib.request.urlopen", side_effect=OSError("connection refused")) as urlopen,
    ):
        assert fetch_newer_release() is None

    urlopen.assert_called_once()


def _http_error(status: int) -> HTTPError:
    """Return the error ``urlopen`` raises for *status*, with the headers GitHub sends."""
    return HTTPError(_RELEASES_API, status, "denied", {"x-ratelimit-remaining": "0"}, None)


@pytest.mark.parametrize(
    ("name", "failure"),
    [
        ("a rate limit", _http_error(403)),
        ("a missing release", _http_error(404)),
        ("a timeout", TimeoutError("timed out")),
        ("an unreachable host", URLError("no route to host")),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_every_failure_offers_nothing(name: str, failure: Exception) -> None:
    """
    Given  a run of the check whose answer fails: a rate limit, a missing release, a timeout,
           an unreachable host
    When   the release is fetched
    Then   nothing is offered and no exception escapes
    """
    with (
        patch("teachers_teammate.__version__", "0.9.0"),
        patch("urllib.request.urlopen", side_effect=failure),
    ):
        assert fetch_newer_release() is None


def test_a_list_answer_is_silent() -> None:
    """
    Given  the GitHub API answers a JSON list where an object is expected
    When   the release is fetched
    Then   nothing is offered
    """
    resp = MagicMock()
    resp.read.return_value = json.dumps([{"tag_name": "v1.0.0"}]).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)

    with (
        patch("teachers_teammate.__version__", "0.9.0"),
        patch("urllib.request.urlopen", return_value=resp),
    ):
        assert fetch_newer_release() is None


def test_a_failed_check_writes_one_log_line_with_its_status(caplog) -> None:
    """
    Given  the API refuses the request with a rate limit (403)
    When   the release is fetched
    Then   one info line names the failure and its status, and holds no URL and no answer
    """
    with (
        caplog.at_level("INFO", logger="teachers_teammate.gui._update_check"),
        patch("teachers_teammate.__version__", "0.9.0"),
        patch("urllib.request.urlopen", side_effect=_http_error(403)),
    ):
        assert fetch_newer_release() is None

    assert len(caplog.records) == 1
    line = caplog.records[0].getMessage()
    assert "403" in line
    assert "HTTPError" in line
    assert _RELEASES_API not in line


def test_a_malformed_answer_is_silent() -> None:
    """
    Given  the GitHub API returns JSON with no tag_name field
    When   the release is fetched
    Then   nothing is offered
    """
    resp = MagicMock()
    resp.read.return_value = json.dumps({"error": "not found"}).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)

    with (
        patch("teachers_teammate.__version__", "0.9.0"),
        patch("urllib.request.urlopen", return_value=resp),
    ):
        assert fetch_newer_release() is None


@pytest.mark.gui
def test_the_answer_arrives_on_the_gui_thread(qtbot) -> None:
    """
    Given  the check run on the background pool
    When   a newer release is found
    Then   the callback is called on the GUI thread with the version and its URL
    """
    import threading  # noqa: PLC0415

    offered: list[tuple[str, str, int]] = []

    def on_available(version: str, url: str) -> None:
        offered.append((version, url, threading.get_ident()))

    newer = ("1.0.0", _RELEASE_PAGE)
    with patch.object(_update_check, "fetch_newer_release", lambda: newer):
        check_for_update(on_available)
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

    with patch.object(_update_check, "fetch_newer_release", lambda: None):
        check_for_update(lambda version, url: offered.append((version, url)))
        assert wait_for_background_tasks(5000)
        qtbot.wait(50)

    assert offered == []
