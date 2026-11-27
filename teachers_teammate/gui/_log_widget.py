"""Colour-coded log view widget, and the handler that feeds it from the run's logger."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import QTextEdit, QWidget

from ..interfaces import RUN_LOGGER
from ._constants import _COLOUR_ERROR, _COLOUR_INFO, _COLOUR_OK, _COLOUR_WARNING


class LogWidget(QTextEdit):
    """Read-only log view with colour-coded WARNING / ERROR / OK lines."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        font = QFont()
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSize(9)
        self.setFont(font)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

    def append_text(self, text: str) -> None:
        """Append *text* with colour coding."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith("ERROR") or "\u2192 ERROR:" in stripped:
                colour = _COLOUR_ERROR
            elif stripped.startswith("WARNING") or "\u2192 WARNING:" in stripped:
                colour = _COLOUR_WARNING
            elif "\u2192 Saved:" in stripped or stripped.startswith("Done:"):
                colour = _COLOUR_OK
            else:
                colour = _COLOUR_INFO
            fmt = cursor.charFormat()
            fmt.setForeground(QColor(colour))
            cursor.setCharFormat(fmt)
            cursor.insertText(line)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def clear_log(self) -> None:
        self.clear()


class _PaneLines(QObject):
    """Crosses the thread boundary: a signal reaches the GUI thread from wherever it was sent."""

    line = Signal(str)


class _PaneFormat(logging.Formatter):
    """A line as the pane shows it: a warning and an error say so, the rest is just the line."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        return f"{record.levelname}: {message}" if record.levelno >= logging.WARNING else message


class RunLogHandler(logging.Handler):
    """Writes a run's own lines into the pane, whichever thread logged them.

    The pane is the window's view of a run while it goes; the log file keeps the same lines
    afterwards.  The window owns the handler: it takes it off the logger when it closes.
    """

    def __init__(self, widget: LogWidget) -> None:
        super().__init__()
        self._lines = _PaneLines(widget)
        self._lines.line.connect(widget.append_text)
        self.setFormatter(_PaneFormat())

    def emit(self, record: logging.LogRecord) -> None:
        self._lines.line.emit(self.format(record) + "\n")

    def close(self) -> None:
        """Take the handler off the run's logger: the window that read it is going."""
        logging.getLogger(RUN_LOGGER).removeHandler(self)
        super().close()


def install_run_log(widget: LogWidget) -> RunLogHandler:
    """Show the run's own lines in *widget*, which closes the handler with its window."""
    handler = RunLogHandler(widget)
    logging.getLogger(RUN_LOGGER).addHandler(handler)
    return handler
