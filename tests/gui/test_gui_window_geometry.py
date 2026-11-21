"""GUI tests for fitting a window to a small screen: the helper, the assistant, Settings."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from teachers_teammate.gui._window_geometry import SCREEN_SHARE, fit_to_screen
from teachers_teammate.gui.settings import SettingsPage, SettingsWindow
from teachers_teammate.gui.setup import SetupAssistant, SetupContext

from ._gui_harness import FakeGuiService, settings_context
from ._service_stubs import services_of
from ._settings_stubs import SetupAppService

#: A laptop screen with the taskbar taken off.
SMALL = QRect(0, 0, 1280, 680)


def _within(size: QSize, available: QRect) -> bool:
    """Return whether *size* is at most the share of *available* a window may take."""
    return size.width() <= int(available.width() * SCREEN_SHARE) and size.height() <= int(
        available.height() * SCREEN_SHARE
    )


def test_fit_to_screen_clamps_the_start_size(qtbot) -> None:
    """
    Given  a window that wants 1500 x 900 on a screen of 1280 x 680
    When   it is fitted to the screen
    Then   it is at most 90 % of the screen in each direction
    """
    window = QWidget()
    qtbot.addWidget(window)

    fit_to_screen(window, QSize(1500, 900), available=SMALL)

    assert window.size() == QSize(int(1280 * SCREEN_SHARE), int(680 * SCREEN_SHARE))


def test_fit_to_screen_lowers_a_minimum_that_is_too_big(qtbot) -> None:
    """
    Given  a window whose content asks for 560 px of height, on a screen that has 680 px
    When   it is fitted to the screen
    Then   the minimum is lowered to what the screen allows, and a smaller one is left alone
    """
    window = QWidget()
    qtbot.addWidget(window)
    window.setMinimumSize(300, 900)

    fit_to_screen(window, available=SMALL)

    assert window.minimumHeight() == int(680 * SCREEN_SHARE)
    assert window.minimumWidth() == 300


def test_fit_to_screen_counts_the_minimum_the_layout_asks_for(qtbot) -> None:
    """
    Given  a window whose layout holds a label that needs 1000 px of height
    When   it is fitted to the small screen
    Then   the window's minimum is no more than the screen allows
    """
    window = QWidget()
    layout = QVBoxLayout(window)
    tall = QLabel("x")
    tall.setMinimumHeight(1000)
    layout.addWidget(tall)
    qtbot.addWidget(window)

    fit_to_screen(window, available=SMALL)

    assert window.minimumHeight() <= int(680 * SCREEN_SHARE)


def test_the_assistant_fits_a_small_screen(qtbot) -> None:
    """
    Given  the assistant on a screen of 1280 x 680
    When   it is fitted
    Then   the window and its minimum are within the screen, whatever its largest page wants
    """
    assistant = SetupAssistant(
        SetupContext(settings={}, app_service=services_of(SetupAppService()), first_start=False)
    )
    qtbot.addWidget(assistant)

    fit_to_screen(assistant, available=SMALL)

    assert _within(assistant.size(), SMALL)
    assert _within(assistant.minimumSize(), SMALL)


def test_the_assistant_fits_the_screen_it_is_on_when_it_is_shown(qtbot) -> None:
    """
    Given  the assistant
    When   it is shown on the screen of this run
    Then   it is within the share of that screen the window may take
    """
    assistant = SetupAssistant(
        SetupContext(settings={}, app_service=services_of(SetupAppService()), first_start=False)
    )
    qtbot.addWidget(assistant)
    assistant.show()

    available = assistant.screen().availableGeometry()
    assert _within(assistant.size(), available)


@pytest.mark.parametrize("page", list(SettingsPage))
def test_the_settings_window_never_asks_for_more_than_the_screen(qtbot, page) -> None:
    """
    Given  the Settings window on any of its pages
    When   it is built
    Then   its size and its minimum are within the screen of this run: no page forces the window
           past it (the Privacy page once asked for 560 px by itself)
    """
    window = SettingsWindow(settings_context(FakeGuiService()), page=page)
    qtbot.addWidget(window)

    available = QApplication.primaryScreen().availableGeometry()
    assert _within(window.size(), available)
    assert _within(window.minimumSize(), available)
