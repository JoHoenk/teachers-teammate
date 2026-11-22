"""Prompt editor: the instructions the teacher can change, and what the app adds to them.

The **instructions** are one editable box that always shows the text that is really used: the
built-in text of the document language until the teacher changes it.  Whether the text is a
change is decided on the way out -- text equal to the built-in one is stored as ``""`` ("use the
built-in prompt for the document language"), so an unchanged prompt keeps following the language.

Below, collapsed, sits the part the app adds: the read-only note for formatted texts.  The
content review's prompt is the one this editor still serves; the proofreading instructions are
fixed and shown read-only in their own dialog.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..domain.prompt_text import normalize_prompt_text
from ..texts import main_window as win
from ..texts import review as txt


class PromptEditor(QWidget):
    """The editable instructions plus, collapsed, what the app adds to them."""

    def __init__(
        self,
        prompt: str = "",
        parent: QWidget | None = None,
        *,
        builtin_text: str = "",
        fixed_text: str = "",
    ) -> None:
        """Build the editor.

        *builtin_text* is the default of the document language (``""`` = none to go back to).
        *fixed_text* is the read-only note the app adds.
        """
        super().__init__(parent)
        self._builtin_text = builtin_text
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)

        # ── Instructions (editable) ───────────────────────────────────────────────
        header = QHBoxLayout()
        header.addWidget(QLabel(txt.YOUR_INSTRUCTIONS_HEADING))
        header.addStretch()
        self._reset_btn = QPushButton(txt.RESET_BUTTON)
        self._reset_btn.setToolTip(txt.RESET_BUTTON_TOOLTIP)
        self._reset_btn.clicked.connect(self._on_reset)
        header.addWidget(self._reset_btn)
        vbox.addLayout(header)

        self._edit = QPlainTextEdit()
        self._edit.setPlainText(prompt or builtin_text)
        vbox.addWidget(self._edit, stretch=1)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #888; font-size: 8pt;")
        vbox.addWidget(self._status)

        self._conflict_hint = QLabel(txt.CONFLICT_HINT)
        self._conflict_hint.setWordWrap(True)
        self._conflict_hint.setStyleSheet("color: #e67e22; font-size: 8pt;")
        vbox.addWidget(self._conflict_hint)

        # ── What the app adds (collapsed) ─────────────────────────────────────────
        vbox.addWidget(self._build_advanced_section(fixed_text))

        self._edit.textChanged.connect(self._sync)
        self._sync()

    def _build_advanced_section(self, fixed_text: str) -> QWidget:
        """Return the collapsible section with the read-only note."""
        box = QWidget()
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(0, 0, 0, 0)
        self._advanced_toggle = QPushButton(win.collapsed(txt.FIXED_HEADING))
        self._advanced_toggle.setFlat(True)
        self._advanced_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advanced_toggle.setStyleSheet("text-align: left; font-weight: bold;")
        self._advanced_toggle.setToolTip(txt.PROMPT_DIALOG_FIXED_TEXT)
        self._advanced_toggle.clicked.connect(self._on_toggle_advanced)
        self._advanced_toggle.setVisible(bool(fixed_text))
        vbox.addWidget(self._advanced_toggle)

        self._advanced = QWidget()
        inner = QVBoxLayout(self._advanced)
        inner.setContentsMargins(0, 0, 0, 0)
        self._fixed_view = QPlainTextEdit()
        self._fixed_view.setPlainText(fixed_text)
        self._fixed_view.setReadOnly(True)
        self._fixed_view.setStyleSheet("color: #aaa;")
        self._fixed_view.setMaximumHeight(100)
        self._fixed_view.setVisible(bool(fixed_text))
        inner.addWidget(self._fixed_view)
        self._advanced.setVisible(False)
        vbox.addWidget(self._advanced)
        return box

    # ── public ────────────────────────────────────────────────────────────────

    def prompt(self) -> str:
        """Return the edited instructions; "" means the built-in prompt is used.

        Text equal to the built-in one is not a customisation: it is stored as a reference
        (``""``) and keeps following the document language, exactly as after a restart.
        """
        text = self._edit.toPlainText().strip()
        if not text or (
            self._builtin_text
            and normalize_prompt_text(text) == normalize_prompt_text(self._builtin_text)
        ):
            return ""
        return text

    # ── private ───────────────────────────────────────────────────────────────

    def _on_reset(self) -> None:
        """Put the built-in instructions of the document language back into the editor."""
        self._edit.setPlainText(self._builtin_text)

    def _on_toggle_advanced(self) -> None:
        expanded = not self._advanced.isVisible()
        self._advanced.setVisible(expanded)
        self._advanced_toggle.setText(
            win.expanded(txt.FIXED_HEADING) if expanded else win.collapsed(txt.FIXED_HEADING)
        )

    def _sync(self) -> None:
        """Keep the status line, the hint and the reset button consistent with the editor."""
        custom = self.prompt()
        self._reset_btn.setEnabled(bool(self._builtin_text) and bool(custom))
        self._reset_btn.setVisible(bool(self._builtin_text))
        if not self._builtin_text:
            self._status.setText("")
        else:
            self._status.setText(txt.CUSTOM_STATUS if custom else txt.BUILTIN_STATUS)
        self._conflict_hint.setVisible(bool(custom) and self._sets_output_format(custom))

    @staticmethod
    def _sets_output_format(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in txt.CONFLICT_MARKERS)
