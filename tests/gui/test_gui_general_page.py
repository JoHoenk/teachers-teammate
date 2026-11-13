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
def test_keep_awake_is_on_by_default(qtbot) -> None:
    """
    Given  settings without a keep_awake key
    When   the General page is built
    Then   the box is ticked and values() hands the key back as True
    """
    page = _page(qtbot)

    assert page.values() == {"keep_awake": True}
    assert page.owned_keys() == frozenset({"keep_awake"})


@pytest.mark.gui
def test_keep_awake_follows_load_and_restore_defaults(qtbot) -> None:
    """
    Given  a page loaded with keep_awake off
    When   the defaults are restored
    Then   values() shows False after load and True after restore
    """
    page = _page(qtbot)

    page.load({"keep_awake": False})
    assert page.values() == {"keep_awake": False}

    page.restore_defaults()
    assert page.values() == {"keep_awake": True}
