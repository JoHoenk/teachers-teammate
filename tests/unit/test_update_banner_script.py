"""The update-banner script CI runs inside a bundle, without the bundle.

``tests/e2e/update_banner.py`` needs a built app and the network, so what is checked here is the
part that decides pass or fail: what the banner has to say, the settings the two windows are built
on, and the answers of the feed — a rate-limited runner passes and is written down, a refused one
fails, and the tag the CI step read has to be the tag the feed names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teachers_teammate.exceptions import ReleaseFeedError
from teachers_teammate.texts import app as app_text
from tests.e2e import update_banner


def _url(tag: str) -> str:
    """Return the release page of *tag*, as the feed names it."""
    return f"{update_banner.RELEASE_URL_PREFIX}releases/tag/{tag}"


def test_the_banner_must_name_the_release_the_feed_answered() -> None:
    """
    Given  the answer of the feed and the banner the window shows for it
    When   the banner is checked against that answer, another release's page and another website
    Then   only the banner of that release at that page passes
    """
    update_banner._check_offer(
        app_text.update_available("0.5.7", _url("v0.5.7")), "v0.5.7", _url("v0.5.7")
    )

    with pytest.raises(update_banner._Failed, match=r"v0\.5\.7"):
        update_banner._check_offer(
            app_text.update_available("0.5.6", _url("v0.5.7")), "v0.5.7", _url("v0.5.7")
        )
    with pytest.raises(update_banner._Failed, match="a page of this project"):
        update_banner._check_offer(
            app_text.update_available("0.5.7", "https://example.com/v0.5.7"),
            "v0.5.7",
            "https://example.com/v0.5.7",
        )


def test_the_settings_write_the_switch_each_window_is_built_on(tmp_path: Path) -> None:
    """
    Given  a temporary folder
    When   the settings are written with the check on and with it off
    Then   the file carries that switch, and the output folder inside the same folder
    """
    on = update_banner._write_settings(tmp_path, check_for_updates=True)
    assert "check_for_updates = true" in on.read_text(encoding="utf-8")

    off = update_banner._write_settings(tmp_path, check_for_updates=False)
    text = off.read_text(encoding="utf-8")
    assert "check_for_updates = false" in text
    assert (tmp_path / "Ausgabe").as_posix() in text


def test_a_rate_limited_runner_passes_and_says_so(tmp_path: Path, monkeypatch) -> None:
    """
    Given  GitHub answering with its hourly limit
    When   the script runs
    Then   it passes, and both the result and the job summary say the banner was not checked
    """

    def refused() -> tuple[str, str]:
        raise ReleaseFeedError(403, rate_limited=True)

    monkeypatch.setattr(update_banner, "_ask_feed", refused)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = tmp_path / "banner.json"

    assert update_banner.main([str(out)]) == 0

    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["state"] == update_banner.RATE_LIMITED
    assert result["detail"] == update_banner.RATE_LIMITED_DETAIL
    written = summary.read_text(encoding="utf-8")
    assert update_banner.RATE_LIMITED in written
    assert update_banner.RATE_LIMITED_DETAIL in written


def test_a_feed_that_gives_no_release_fails(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a refused answer that is not the hourly limit
    When   the script runs
    Then   it fails and the result names the status
    """

    def refused() -> tuple[str, str]:
        raise ReleaseFeedError(502)

    monkeypatch.setattr(update_banner, "_ask_feed", refused)
    out = tmp_path / "banner.json"

    assert update_banner.main([str(out)]) == 1

    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "502" in result["failed"][0]


def test_the_tag_the_ci_step_read_must_be_the_one_the_feed_names(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a feed that names a release, and a CI step that read another tag
    When   the script runs
    Then   it fails and names both, so a stale release list cannot pass
    """
    monkeypatch.setattr(update_banner, "_ask_feed", lambda: ("v0.5.7", _url("v0.5.7")))
    out = tmp_path / "banner.json"

    assert update_banner.main([str(out), "--tag", "v0.5.6"]) == 1

    result = json.loads(out.read_text(encoding="utf-8"))
    assert "v0.5.7" in result["failed"][0]
    assert "v0.5.6" in result["failed"][0]
