"""Colour-coded log view widget."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import QTextEdit, QWidget

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
