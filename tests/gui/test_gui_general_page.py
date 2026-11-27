"""Tests for Settings → General and its place in the Settings window."""

from __future__ import annotations

import pytest

from teachers_teammate.gui.settings._general_page import GeneralPage
from tests.gui._gui_harness import settings_context


def _page(qtbot, **settings) -> GeneralPage:
    page = GeneralPage(settings_context(settings=settings))
    qtbot.addWidget(page)
    return page


@pytest.mark.gui
def test_defaults_are_on(qtbot) -> None:
    """
    Given  settings without a keep_awake or check_for_updates key
    When   the General page is built
    Then   both boxes are ticked and values() hands the keys back as True
    """
    page = _page(qtbot)

    assert page.values() == {
        "keep_awake": True,
        "check_for_updates": True,
        "log_detail": "normal",
    }
    assert page.owned_keys() == frozenset({"keep_awake", "check_for_updates", "log_detail"})


@pytest.mark.gui
def test_keep_awake_follows_load_and_restore_defaults(qtbot) -> None:
    """
    Given  a page loaded with keep_awake off
    When   the defaults are restored
    Then   values() shows False after load and True after restore
    """
    page = _page(qtbot)

    page.load({"keep_awake": False})
    assert page.values()["keep_awake"] is False

    page.restore_defaults()
    assert page.values()["keep_awake"] is True


@pytest.mark.gui
def test_check_for_updates_round_trips_through_load_and_values(qtbot) -> None:
    """
    Given  a page built from settings that turn the update check off
    When   the page is built, then loaded with the key on, then restored to defaults
    Then   values() reports off, then on, then on again
    """
    page = _page(qtbot, check_for_updates=False)
    assert page.values()["check_for_updates"] is False

    page.load({"check_for_updates": True})
    assert page.values()["check_for_updates"] is True

    page.load({"check_for_updates": False})
    page.restore_defaults()
    assert page.values()["check_for_updates"] is True


@pytest.mark.gui
def test_an_old_config_without_the_key_shows_the_check_on(qtbot) -> None:
    """
    Given  a settings dict written before the update check had a switch
    When   the General page is built from it
    Then   the box is ticked, so an existing installation keeps checking
    """
    page = _page(qtbot, keep_awake=False)

    assert page.values()["check_for_updates"] is True


@pytest.mark.gui
def test_log_detail_offers_normal_and_detailed(qtbot) -> None:
    """
    Given  a page built from settings that ask for the detailed log
    When   it is built, then loaded with the default, then shown a level no item stands for
    Then   it shows detailed, then normal, and an errors-only level shows as normal
    """
    page = _page(qtbot, log_detail="detailed")
    assert page.values()["log_detail"] == "detailed"

    page.load({"log_detail": "normal"})
    assert page.values()["log_detail"] == "normal"

    page.load({"log_detail": "errors"})
    assert page.values()["log_detail"] == "normal"
