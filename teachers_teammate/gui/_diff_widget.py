"""Inline word-level diff widget: deleted in red/strikethrough, added in green."""

from __future__ import annotations

import html

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QTextEdit, QWidget

from ..domain.proofreading.word_diff import diff_opcodes, tokenize


class DiffWidget(QTextEdit):
    """Inline word-level diff: deleted in red/strikethrough, added in green."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        font = QFont()
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSize(9)
        self.setFont(font)
        self._last_key: tuple[str, str, int] | None = None

    def set_texts(self, original: str, corrected: str) -> None:
        key = (original, corrected, self.font().pointSize())
        if key == self._last_key:
            return  # same comparison at the same size: keep the rendered diff (and scroll)
        self._last_key = key
        if not original:
            self.setPlainText("(no OCR text available for comparison)")
            return
        if not corrected:
            self.setPlainText("(no correction available - skipped or not yet generated)")
            return

        # Get current font size from widget
        current_font = self.font()
        font_size_pt = current_font.pointSize()

        orig_tokens = tokenize(original)
        corr_tokens = tokenize(corrected)

        parts = [
            "<html><body>"
            f"<pre style='white-space: pre-wrap; font-family: monospace; font-size: {font_size_pt}pt;'>"
        ]
        for tag, i1, i2, j1, j2 in diff_opcodes(orig_tokens, corr_tokens):
            if tag == "equal":
                parts.append(self._escape_tokens(orig_tokens[i1:i2]))
            elif tag == "replace":
                old = self._escape_tokens(orig_tokens[i1:i2])
                new = self._escape_tokens(corr_tokens[j1:j2])
                parts.append(
                    f'<span style="background:#ffd7d7;text-decoration:line-through;'
                    f'color:#c0392b;">{old}</span>'
                    f'<span style="background:#d7ffd7;color:#27ae60;">{new}</span>'
                )
            elif tag == "delete":
                old = self._escape_tokens(orig_tokens[i1:i2])
                parts.append(
                    f'<span style="background:#ffd7d7;text-decoration:line-through;'
                    f'color:#c0392b;">{old}</span>'
                )
            elif tag == "insert":
                new = self._escape_tokens(corr_tokens[j1:j2])
                parts.append(f'<span style="background:#d7ffd7;color:#27ae60;">{new}</span>')
        parts.append("</pre></body></html>")
        self.setHtml("".join(parts))

    @staticmethod
    def _escape_tokens(tokens: list[str]) -> str:
        return "".join(html.escape(token) for token in tokens)

    def clear_diff(self) -> None:
        self._last_key = None
        self.clear()
