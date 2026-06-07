"""Tests for the background update-check thread."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.gui._update_check import UpdateCheckThread, _version_tuple


# ── _version_tuple ────────────────────────────────────────────────────────────


def test_version_tuple_strips_v_prefix() -> None:
    """
    Given  a version tag with a 'v' prefix
    When   _version_tuple() is called
    Then   the prefix is stripped and each part is returned as an int
    """
    assert _version_tuple("v1.2.3") == (1, 2, 3)


def test_version_tuple_strips_prerelease_suffix() -> None:
    """
    Given  a version tag with a pre-release suffix separated by '-'
    When   _version_tuple() is called
    Then   only the numeric part before the dash is returned
    """
    assert _version_tuple("1.2.3-rc1") == (1, 2, 3)


def test_version_tuple_bare_version() -> None:
    """
    Given  a plain version string with no prefix or suffix
    When   _version_tuple() is called
    Then   the parts are returned as ints
    """
    assert _version_tuple("2.0.0") == (2, 0, 0)


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


# ── UpdateCheckThread ─────────────────────────────────────────────────────────


def _fake_response(tag: str, url: str = "https://example.com/rel") -> MagicMock:
    body = json.dumps({"tag_name": tag, "html_url": url}).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@pytest.mark.gui
def test_update_available_emitted_when_newer(qtbot) -> None:
    """
    Given  the installed version is 0.9.0 and the latest GitHub release is v1.0.0
    When   UpdateCheckThread.run() is called
    Then   update_available is emitted with the new version string and release URL
    """
    emitted: list[tuple[str, str]] = []
    thread = UpdateCheckThread()
    thread.update_available.connect(lambda v, u: emitted.append((v, u)))

    with (
        patch("teachers_teammate.__version__", "0.9.0"),
        patch("urllib.request.urlopen", return_value=_fake_response("v1.0.0")),
    ):
        thread.run()

    qtbot.wait(10)
    assert len(emitted) == 1
    assert emitted[0][0] == "1.0.0"
    assert "example.com" in emitted[0][1]


@pytest.mark.gui
def test_update_not_emitted_when_same_version(qtbot) -> None:
    """
    Given  the installed version equals the latest GitHub release (both 1.0.0)
    When   UpdateCheckThread.run() is called
    Then   update_available is not emitted
    """
    emitted: list[tuple[str, str]] = []
    thread = UpdateCheckThread()
    thread.update_available.connect(lambda v, u: emitted.append((v, u)))

    with (
        patch("teachers_teammate.__version__", "1.0.0"),
        patch("urllib.request.urlopen", return_value=_fake_response("v1.0.0")),
    ):
        thread.run()

    qtbot.wait(10)
    assert emitted == []


@pytest.mark.gui
def test_update_not_emitted_when_remote_is_older(qtbot) -> None:
    """
    Given  the installed version (1.1.0) is newer than the latest GitHub release (v1.0.0)
    When   UpdateCheckThread.run() is called
    Then   update_available is not emitted
    """
    emitted: list[tuple[str, str]] = []
    thread = UpdateCheckThread()
    thread.update_available.connect(lambda v, u: emitted.append((v, u)))

    with (
        patch("teachers_teammate.__version__", "1.1.0"),
        patch("urllib.request.urlopen", return_value=_fake_response("v1.0.0")),
    ):
        thread.run()

    qtbot.wait(10)
    assert emitted == []


@pytest.mark.gui
def test_network_failure_is_silent(qtbot) -> None:
    """
    Given  urllib raises an OSError when contacting the GitHub API
    When   UpdateCheckThread.run() is called
    Then   update_available is not emitted and no exception escapes run()
    """
    emitted: list[tuple[str, str]] = []
    thread = UpdateCheckThread()
    thread.update_available.connect(lambda v, u: emitted.append((v, u)))

    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        thread.run()  # must not raise

    qtbot.wait(10)
    assert emitted == []


@pytest.mark.gui
def test_malformed_response_is_silent(qtbot) -> None:
    """
    Given  the GitHub API returns JSON with no tag_name field
    When   UpdateCheckThread.run() is called
    Then   update_available is not emitted
    """
    emitted: list[tuple[str, str]] = []
    thread = UpdateCheckThread()
    thread.update_available.connect(lambda v, u: emitted.append((v, u)))

    resp = MagicMock()
    resp.read.return_value = json.dumps({"error": "not found"}).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)

    with (
        patch("teachers_teammate.__version__", "0.9.0"),
        patch("urllib.request.urlopen", return_value=resp),
    ):
        thread.run()

    qtbot.wait(10)
    assert emitted == []
