"""Privacy Settings: what leaves this computer, and how personal info is found and replaced.

One window for everything a teacher decides about privacy.  The first tab lists the answers
the user chose not to be asked about again ("Don't ask again" before a run that sends content
away) and lets them be taken back; the other two tabs are the existing personal-info
detection and pattern rules of :class:`~._anonymizer_config_dialog.AnonymizerConfigDialog`.

Changes in the first tab are only applied with OK, like the rest of the dialog.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ._anonymizer_config_dialog import AnonymizerConfigDialog
from ._privacy_settings import SECTION_DETECTION, SECTION_SENDING, FlowAcknowledgements

TITLE = "Privacy Settings"
TAB_SENDING = "Sending data away"
TAB_DETECTION = "Personal info: detection"
TAB_PATTERNS = "Personal info: patterns"

SENDING_INTRO = (
    "Before a run sends text or pages away from this computer, you are asked first. If you "
    "tick “Don't ask again”, the answer is remembered for exactly those settings and listed "
    "here. Take an answer back and you are asked again the next time."
)
LIST_HEADING = "You are not asked again for:"
NOTHING_REMEMBERED = (
    "No answers are remembered. You are asked before every run that sends documents away "
    "from this computer."
)
ASK_AGAIN_SELECTED = "Ask again for selected"
ASK_AGAIN_ALL = "Ask again for all"
CHANGES_ON_OK = "Changes are saved when you press OK."


class SendingDataTab(QWidget):
    """The remembered "don't ask again" answers, with a working copy changed until OK."""

    def __init__(self, acknowledgements: FlowAcknowledgements, parent: QWidget | None = None):
        super().__init__(parent)
        self._acknowledgements = acknowledgements
        # fingerprint -> readable summary; only written back by apply()
        self._remembered = {
            flow.fingerprint: flow.summary for flow in acknowledgements.remembered()
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        intro = QLabel(SENDING_INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._heading = QLabel(f"<b>{LIST_HEADING}</b>")
        layout.addWidget(self._heading)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setWordWrap(True)
        self._list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self._list, stretch=1)

        self._empty = QLabel(NOTHING_REMEMBERED)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet("color: #888;")
        layout.addWidget(self._empty)

        buttons = QHBoxLayout()
        self._forget_selected_btn = QPushButton(ASK_AGAIN_SELECTED)
        self._forget_selected_btn.clicked.connect(self._forget_selected)
        buttons.addWidget(self._forget_selected_btn)
        self._forget_all_btn = QPushButton(ASK_AGAIN_ALL)
        self._forget_all_btn.clicked.connect(self._forget_all)
        buttons.addWidget(self._forget_all_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        note = QLabel(CHANGES_ON_OK)
        note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(note)

        self._refresh()

    def remaining_fingerprints(self) -> set[str]:
        """Return the answers that are still remembered (the working copy)."""
        return set(self._remembered)

    def apply(self) -> None:
        """Write the working copy back: answers taken back here are asked about again."""
        self._acknowledgements.keep_only(self._remembered)

    # ── internal ──────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        self._list.clear()
        for fingerprint, summary in sorted(self._remembered.items(), key=lambda kv: kv[1]):
            item = QListWidgetItem(summary)
            item.setData(Qt.ItemDataRole.UserRole, fingerprint)
            item.setToolTip(summary)
            self._list.addItem(item)
        has_any = bool(self._remembered)
        self._heading.setVisible(has_any)
        self._list.setVisible(has_any)
        self._empty.setVisible(not has_any)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self._forget_selected_btn.setEnabled(bool(self._list.selectedItems()))
        self._forget_all_btn.setEnabled(bool(self._remembered))

    def _forget_selected(self) -> None:
        for item in self._list.selectedItems():
            self._remembered.pop(str(item.data(Qt.ItemDataRole.UserRole)), None)
        self._refresh()

    def _forget_all(self) -> None:
        self._remembered.clear()
        self._refresh()


class PrivacySettingsDialog(AnonymizerConfigDialog):
    """Sending data away + personal-info detection + patterns, in one window."""

    def __init__(
        self,
        language: str,
        anonymizer_config: object,
        app_service,
        parent: QWidget | None = None,
        *,
        acknowledgements: FlowAcknowledgements,
        section: str = SECTION_DETECTION,
        sample_text: str = "",
    ) -> None:
        super().__init__(language, anonymizer_config, app_service, parent, sample_text=sample_text)
        self.setWindowTitle(TITLE)
        self._sending_tab = SendingDataTab(acknowledgements)
        self._tabs.setTabText(0, TAB_DETECTION)
        self._tabs.setTabText(1, TAB_PATTERNS)
        self._tabs.insertTab(0, self._sending_tab, TAB_SENDING)
        self._tabs.setCurrentIndex(0 if section == SECTION_SENDING else 1)

    def sending_tab(self) -> SendingDataTab:
        """Return the "Sending data away" tab."""
        return self._sending_tab

    def accept(self) -> None:
        """Apply the remembered-answer changes, then close with the anonymizer settings."""
        self._sending_tab.apply()
        super().accept()
