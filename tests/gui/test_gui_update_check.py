"""Tests for the update check against GitHub Releases."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.gui import _update_check
from teachers_teammate.gui._background import wait_for_background_tasks
from teachers_teammate.gui._update_check import (
    _version_tuple,
    check_for_update,
    fetch_newer_release,
)


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


def _fake_response(tag: str, url: str = "https://example.com/rel") -> MagicMock:
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
        assert fetch_newer_release() == ("1.0.0", "https://example.com/rel")


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
        assert fetch_newer_release() == ("0.5.7", "https://example.com/rel")


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
        assert fetch_newer_release() == ("0.5.7", "https://example.com/rel")


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


def test_a_network_failure_is_silent() -> None:
    """
    Given  urllib raises an OSError when contacting the GitHub API
    When   the release is fetched
    Then   nothing is offered and no exception escapes
    """
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        assert fetch_newer_release() is None


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

    newer = ("1.0.0", "https://example.com/rel")
    with patch.object(_update_check, "fetch_newer_release", lambda: newer):
        check_for_update(on_available)
        assert wait_for_background_tasks(5000)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

    version, url, thread = offered[0]
    assert (version, url) == ("1.0.0", "https://example.com/rel")
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
