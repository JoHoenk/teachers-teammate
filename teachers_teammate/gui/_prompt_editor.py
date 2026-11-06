"""Prompt editor: the instructions the teacher can change, and the advanced answer format.

The **instructions** are one editable box that always shows the text that is really used: the
built-in text of the document language until the teacher changes it.  Whether the text is a
change is decided on the way out -- text equal to the built-in one is stored as ``""`` ("use the
built-in prompt for the document language"), so an unchanged prompt keeps following the language.

Below, collapsed, sits the part the app adds: for proofreading the two **answer formats** (with
explanations / corrections only) that tell the model how to fill in its reply -- editable, for
advanced users -- plus the read-only note for formatted texts.  Evaluation shows its read-only
note only.
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

from ..application.correction_options import prompt_ignores_level
from ..domain.answer_format import ANSWER_FORMAT_VERSION, missing_fields, normalize_format_text
from ..domain.correction_prompts import normalize_prompt_text
from . import _user_text as txt


class _FormatBox(QWidget):
    """One editable answer format with its own *Reset to default* button."""

    def __init__(
        self, title: str, builtin: str, saved: str, parent: QWidget, *, check_fields: bool
    ) -> None:
        super().__init__(parent)
        self._builtin = builtin
        self._check_fields = check_fields
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        label = QLabel(title)
        label.setStyleSheet("font-weight: bold;")
        row.addWidget(label)
        row.addStretch()
        self._reset = QPushButton(txt.RESET_BUTTON)
        self._reset.setToolTip(txt.ANSWER_FORMAT_RESET_TOOLTIP)
        self._reset.clicked.connect(lambda: self._edit.setPlainText(self._builtin))
        row.addWidget(self._reset)
        vbox.addLayout(row)
        self._edit = QPlainTextEdit()
        self._edit.setPlainText(saved or builtin)
        self._edit.setMinimumHeight(120)
        vbox.addWidget(self._edit)
        self._missing = QLabel()
        self._missing.setWordWrap(True)
        self._missing.setStyleSheet("color: #e67e22; font-size: 8pt;")
        vbox.addWidget(self._missing)
        self._edit.textChanged.connect(self._sync)
        self._sync()

    def edit(self) -> QPlainTextEdit:
        """Return the text box."""
        return self._edit

    def value(self) -> str:
        """Return the edited format; "" means the built-in one (also for an unchanged text)."""
        text = self._edit.toPlainText().strip()
        if not text or normalize_format_text(text) == normalize_format_text(self._builtin):
            return ""
        return text

    def _sync(self) -> None:
        self._reset.setEnabled(bool(self.value()))
        missing = missing_fields(self.value()) if self._check_fields else []
        self._missing.setText(txt.answer_format_missing(missing) if missing else "")
        self._missing.setVisible(bool(missing))


class PromptEditor(QWidget):
    """The editable instructions plus, collapsed, what the app adds to them."""

    def __init__(
        self,
        prompt: str = "",
        parent: QWidget | None = None,
        *,
        builtin_text: str = "",
        fixed_text: str = "",
        answer_formats: tuple[str, str] | None = None,
        saved_formats: tuple[str, str] = ("", ""),
        level_aware: bool = False,
    ) -> None:
        """Build the editor.

        *builtin_text* is the default of the document language (``""`` = none to go back to).
        *answer_formats* are the built-in ``(with explanations, corrections only)`` formats and
        *saved_formats* the teacher's own versions; without them no format editor is shown.
        *fixed_text* is the read-only note the app adds.  *level_aware* shows the hint for
        instructions that do not use the ``{level}`` setting.
        """
        super().__init__(parent)
        self._builtin_text = builtin_text
        self._level_aware = level_aware
        self._format_boxes: tuple[_FormatBox, _FormatBox] | None = None
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
        if level_aware:
            self._edit.setToolTip(txt.LEVEL_PLACEHOLDER_TOOLTIP)
        vbox.addWidget(self._edit, stretch=1)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #888; font-size: 8pt;")
        vbox.addWidget(self._status)

        self._level_hint = QLabel(txt.LEVEL_HINT)
        self._level_hint.setWordWrap(True)
        self._level_hint.setStyleSheet("color: #e67e22; font-size: 8pt;")
        vbox.addWidget(self._level_hint)
        self._conflict_hint = QLabel(txt.CONFLICT_HINT)
        self._conflict_hint.setWordWrap(True)
        self._conflict_hint.setStyleSheet("color: #e67e22; font-size: 8pt;")
        vbox.addWidget(self._conflict_hint)

        # ── What the app adds (collapsed) ─────────────────────────────────────────
        vbox.addWidget(self._build_advanced_section(fixed_text, answer_formats, saved_formats))

        self._edit.textChanged.connect(self._sync)
        self._sync()

    def _build_advanced_section(
        self,
        fixed_text: str,
        answer_formats: tuple[str, str] | None,
        saved_formats: tuple[str, str],
    ) -> QWidget:
        """Return the collapsible section: answer formats (editable) and the read-only note."""
        box = QWidget()
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(0, 0, 0, 0)
        heading = txt.ANSWER_FORMAT_HEADING if answer_formats else txt.FIXED_HEADING
        self._advanced_toggle = QPushButton(f"▸ {heading}")
        self._advanced_heading = heading
        self._advanced_toggle.setFlat(True)
        self._advanced_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advanced_toggle.setStyleSheet("text-align: left; font-weight: bold;")
        self._advanced_toggle.setToolTip(
            txt.ANSWER_FORMAT_TOOLTIP if answer_formats else txt.PROMPT_DIALOG_FIXED_TEXT
        )
        self._advanced_toggle.clicked.connect(self._on_toggle_advanced)
        self._advanced_toggle.setVisible(bool(fixed_text or answer_formats))
        vbox.addWidget(self._advanced_toggle)

        self._advanced = QWidget()
        inner = QVBoxLayout(self._advanced)
        inner.setContentsMargins(0, 0, 0, 0)
        if answer_formats is not None:
            warning = QLabel(txt.ANSWER_FORMAT_WARNING)
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #e67e22; font-size: 8pt;")
            inner.addWidget(warning)
            self._format_boxes = (
                _FormatBox(
                    txt.ANSWER_FORMAT_REVIEW_TITLE,
                    answer_formats[0],
                    saved_formats[0],
                    self,
                    check_fields=True,
                ),
                _FormatBox(
                    txt.ANSWER_FORMAT_TEXT_TITLE,
                    answer_formats[1],
                    saved_formats[1],
                    self,
                    check_fields=False,
                ),
            )
            for format_box in self._format_boxes:
                inner.addWidget(format_box)
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

    def answer_format_values(self) -> dict[str, object]:
        """Return the answer-format settings to store (empty without a format editor)."""
        if self._format_boxes is None:
            return {}
        return {
            "answer_format_review": self._format_boxes[0].value(),
            "answer_format_text": self._format_boxes[1].value(),
            "answer_format_version": ANSWER_FORMAT_VERSION,
        }

    # ── private ───────────────────────────────────────────────────────────────

    def _on_reset(self) -> None:
        """Put the built-in instructions of the document language back into the editor."""
        self._edit.setPlainText(self._builtin_text)

    def _on_toggle_advanced(self) -> None:
        expanded = not self._advanced.isVisible()
        self._advanced.setVisible(expanded)
        self._advanced_toggle.setText(f"{'▾' if expanded else '▸'} {self._advanced_heading}")

    def _sync(self) -> None:
        """Keep the status line, hints and the reset button consistent with the editor."""
        custom = self.prompt()
        self._reset_btn.setEnabled(bool(self._builtin_text) and bool(custom))
        self._reset_btn.setVisible(bool(self._builtin_text))
        if not self._builtin_text:
            self._status.setText("")
        else:
            self._status.setText(txt.CUSTOM_STATUS if custom else txt.BUILTIN_STATUS)
        self._level_hint.setVisible(self._level_aware and prompt_ignores_level(custom))
        self._conflict_hint.setVisible(bool(custom) and self._sets_output_format(custom))

    @staticmethod
    def _sets_output_format(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in txt.CONFLICT_MARKERS)
