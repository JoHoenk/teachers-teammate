"""Qt's own messages in the log file: what is written, how often, and when it is installed."""

from __future__ import annotations

import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.infrastructure import qt_log


def _kind(name: str) -> SimpleNamespace:
    """Return a stand-in for ``QtMsgType.<name>``."""
    return SimpleNamespace(name=name)


def _context(category: str | None) -> SimpleNamespace:
    """Return a stand-in for the ``QMessageLogContext`` of a message."""
    return SimpleNamespace(category=category)


@pytest.fixture(autouse=True)
def _no_warning_seen_yet():
    """Start every test with no Qt warning written, and leave none behind."""
    qt_log._seen = qt_log._Seen()
    yield
    qt_log._seen = qt_log._Seen()


def test_each_kind_of_message_becomes_its_level_with_its_category(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Given  messages of each kind, one with a category, one with Qt's default, one with none
    When   they reach the handler
    Then   a warning is a WARNING, a critical or fatal one an ERROR, the rest a DEBUG detail,
           and the category leads the line unless it says nothing
    """
    with caplog.at_level(logging.DEBUG, logger="qt"):
        qt_log.log_message(_kind("QtWarningMsg"), _context("qt.qpa.xcb"), "no display")
        qt_log.log_message(_kind("QtCriticalMsg"), _context("default"), "broken")
        qt_log.log_message(_kind("QtFatalMsg"), _context(None), "gone")
        qt_log.log_message(_kind("QtInfoMsg"), _context(None), "hello")
        qt_log.log_message(_kind("QtDebugMsg"), _context(None), "detail")

    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "qt"] == [
        (logging.WARNING, "qt.qpa.xcb: no display"),
        (logging.ERROR, "broken"),
        (logging.ERROR, "gone"),
        (logging.DEBUG, "hello"),
        (logging.DEBUG, "detail"),
    ]


def test_a_warning_is_written_once_and_an_error_every_time(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Given  Qt repeating one warning and one critical message on every paint
    When   each reaches the handler three times
    Then   the warning is written once and the critical message three times
    """
    with caplog.at_level(logging.DEBUG, logger="qt"):
        for _ in range(3):
            qt_log.log_message(_kind("QtWarningMsg"), _context(None), "again")
            qt_log.log_message(_kind("QtCriticalMsg"), _context(None), "worse")

    texts = [r.getMessage() for r in caplog.records if r.name == "qt"]
    assert texts.count("again") == 1
    assert texts.count("worse") == 3


def test_warnings_past_the_cap_are_dropped_with_one_note(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Given  more different warnings than the cap lets through
    When   they reach the handler
    Then   the cap's worth is written, then one note, and the rest not at all
    """
    with caplog.at_level(logging.DEBUG, logger="qt"):
        for number in range(qt_log._MAX_DISTINCT + 20):
            qt_log.log_message(_kind("QtWarningMsg"), _context(None), f"warning {number}")

    texts = [r.getMessage() for r in caplog.records if r.name == "qt"]
    assert len(texts) == qt_log._MAX_DISTINCT + 1
    assert texts[-1] == "Further different Qt warnings are not written"


def test_install_hands_the_handler_to_qt() -> None:
    """
    Given  Qt's message hook
    When   the log is installed
    Then   Qt's handler is the one of this module
    """
    qtcore = MagicMock()

    with patch.dict(sys.modules, {"PySide6.QtCore": qtcore}):
        qt_log.install()

    qtcore.qInstallMessageHandler.assert_called_once_with(qt_log.log_message)
