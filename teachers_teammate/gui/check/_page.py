"""The Check tab: a header that says which document is open, and the preview panel.

The page owns no document state. The window pushes the name, the position in the queue and
what the Copy menu may offer, and listens for what the teacher asks for — so a document that
is open and a row that is selected can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFontMetrics, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import _user_text as txt
from .._export_menu import ExportKind
from .._preview_panel import PreviewPanel


class CopyKind(Enum):
    """What one entry of the Copy menu puts on the clipboard."""

    RAW = "raw"
    RAW_ANONYMIZED = "raw_anonymized"
    CORRECTED = "corrected"
    CORRECTED_ANONYMIZED = "corrected_anonymized"

    @property
    def label(self) -> str:
        """Menu entry text."""
        return _COPY_LABELS[self]

    @property
    def is_corrected(self) -> bool:
        """Whether this copies the corrected text instead of what was read."""
        return self in (CopyKind.CORRECTED, CopyKind.CORRECTED_ANONYMIZED)

    @property
    def is_anonymized(self) -> bool:
        """Whether the text is anonymized before it is copied."""
        return self in (CopyKind.RAW_ANONYMIZED, CopyKind.CORRECTED_ANONYMIZED)


_COPY_LABELS = {
    CopyKind.RAW: "Copy what was read",
    CopyKind.RAW_ANONYMIZED: "Copy what was read, names hidden",
    CopyKind.CORRECTED: "Copy corrected text",
    CopyKind.CORRECTED_ANONYMIZED: "Copy corrected text, names hidden",
}

#: The two Save entries of the Copy menu, in menu order.
_SAVE_KINDS = (ExportKind.TEXT, ExportKind.TEXT_ANONYMIZED)
_SAVE_LABELS = {
    ExportKind.TEXT: "Save what was read as text file\u2026",
    ExportKind.TEXT_ANONYMIZED: "Save it with names hidden\u2026",
}

#: How long the "Copied" note stays next to the button.
_COPIED_MS = 3000
#: Width the document name is elided to; the header never grows with a long file name.
_NAME_WIDTH = 320


@dataclass(frozen=True)
class CopyState:
    """What the Copy menu can offer for the open document."""

    has_raw: bool = False
    has_corrected: bool = False
    can_hide_names: bool = False

    def allows(self, kind: CopyKind) -> bool:
        """Whether *kind* can be copied: the text exists and, if asked, names can be hidden."""
        has_text = self.has_corrected if kind.is_corrected else self.has_raw
        return has_text and (self.can_hide_names or not kind.is_anonymized)

    def allows_save(self, kind: ExportKind) -> bool:
        """Whether *kind* can be written: the text exists and, if asked, names can be hidden."""
        return self.has_raw and (self.can_hide_names or kind is not ExportKind.TEXT_ANONYMIZED)


def _escape_is_for_the_focus(widget: QWidget | None) -> bool:
    """Whether Esc belongs to *widget* instead of meaning "back to Documents".

    Esc closes an open menu, and in a field the teacher can type in it is their own key
    (leaving a completion, dropping a selection). Only outside those does it leave the tab.
    """
    if QApplication.activePopupWidget() is not None:
        return True
    if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
        return not widget.isReadOnly()
    if isinstance(widget, QComboBox):
        return widget.isEditable()
    return False


class CheckPage(QWidget):
    """One document, full width: the header above, the preview panel below."""

    previous_requested = Signal()
    next_requested = Signal()
    copy_requested = Signal(object)  # CopyKind
    save_requested = Signal(object)  # ExportKind
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._copy_actions: dict[CopyKind, QAction] = {}
        self._save_actions: dict[ExportKind, QAction] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._build_header())
        self.preview = PreviewPanel()
        layout.addWidget(self.preview, 1)

    # ── Header ────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(8)

        self._name_label = QLabel()
        self._name_label.setStyleSheet("font-weight: bold;")
        self._name_label.setMaximumWidth(_NAME_WIDTH)
        layout.addWidget(self._name_label)

        self._position_label = QLabel()
        self._position_label.setStyleSheet("color: #888;")
        self._position_label.setVisible(False)
        layout.addWidget(self._position_label)

        self._previous_btn = QToolButton()
        self._previous_btn.setText("\u25c0")
        self._previous_btn.setToolTip("Previous document in the queue")
        self._previous_btn.setEnabled(False)
        self._previous_btn.clicked.connect(self.previous_requested)
        layout.addWidget(self._previous_btn)

        self._next_btn = QToolButton()
        self._next_btn.setText("\u25b6")
        self._next_btn.setToolTip("Next document in the queue")
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self.next_requested)
        layout.addWidget(self._next_btn)

        layout.addStretch()

        self._copy_btn = QToolButton()
        self._copy_btn.setText("Copy")
        self._copy_btn.setToolTip("Copy the text of this document, or save it as a text file")
        self._copy_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._copy_btn.setMenu(self._build_copy_menu())
        layout.addWidget(self._copy_btn)

        self._copied_label = QLabel()
        self._copied_label.setStyleSheet("color: #888;")
        self._copied_label.setVisible(False)
        layout.addWidget(self._copied_label)

        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.setInterval(_COPIED_MS)
        self._copied_timer.timeout.connect(lambda: self._copied_label.setVisible(False))
        return header

    def _build_copy_menu(self) -> QMenu:
        menu = QMenu(self)
        # A disabled entry explains itself: the tooltip is the eye button's, which points at
        # Settings → Add-ons, so the teacher knows where names could be hidden.
        menu.setToolTipsVisible(True)
        for kind in CopyKind:
            action = menu.addAction(kind.label)
            assert action is not None
            action.triggered.connect(lambda _checked=False, k=kind: self.copy_requested.emit(k))
            self._copy_actions[kind] = action
        menu.addSeparator()
        for kind in _SAVE_KINDS:
            action = menu.addAction(_SAVE_LABELS[kind])
            assert action is not None
            action.triggered.connect(lambda _checked=False, k=kind: self.save_requested.emit(k))
            self._save_actions[kind] = action
        return menu

    # ── What the window pushes ────────────────────────────────────────────

    def show_document(self, name: str, path: str, position: int, total: int) -> None:
        """Say which document is open and where it sits among the open-able ones."""
        self._name_label.setToolTip(path)
        metrics = QFontMetrics(self._name_label.font())
        self._name_label.setText(
            metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, _NAME_WIDTH)
        )
        self._position_label.setText(f"{position} of {total}" if total else "")
        self._position_label.setVisible(bool(total))
        self._previous_btn.setEnabled(position > 1)
        self._next_btn.setEnabled(0 < position < total)

    def clear(self) -> None:
        """Empty the header: no document is open."""
        self._name_label.setText("")
        self._name_label.setToolTip("")
        self._position_label.setText("")
        self._position_label.setVisible(False)
        self._previous_btn.setEnabled(False)
        self._next_btn.setEnabled(False)
        self.set_copy_state(CopyState())

    def set_copy_state(self, state: CopyState) -> None:
        """Say what the Copy menu may offer for the open document."""
        for kind, action in self._copy_actions.items():
            action.setEnabled(state.allows(kind))
            action.setToolTip(
                ""
                if state.can_hide_names or not kind.is_anonymized
                else txt.HIDE_PERSONAL_INFO_UNAVAILABLE_TIP
            )
        for kind, action in self._save_actions.items():
            action.setEnabled(state.allows_save(kind))
            action.setToolTip(
                ""
                if state.can_hide_names or kind is not ExportKind.TEXT_ANONYMIZED
                else txt.HIDE_PERSONAL_INFO_UNAVAILABLE_TIP
            )

    def show_copied(self) -> None:
        """Confirm a copy next to the button, for a few seconds."""
        self._copied_label.setText("Copied")
        self._copied_label.setVisible(True)
        self._copied_timer.start()

    # ── Keys ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Esc leaves the tab, unless the focused widget has its own use for it."""
        if event.key() == Qt.Key.Key_Escape and not _escape_is_for_the_focus(
            QApplication.focusWidget()
        ):
            self.back_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
