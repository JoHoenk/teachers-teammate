"""Settings → Privacy: what leaves this computer, and how personal info is found and replaced.

The first tab lists the answers the user chose not to be asked about again ("Don't ask
again" before a run that sends content away) and lets them be taken back; the other two tabs
are the personal-info detection and pattern rules of
:class:`~.._anonymizer_config_widget.AnonymizerConfigWidget`.

The detection tab also carries the "hide personal information in the texts shown in this app"
choice.  It is a GUI preference, applied by the main window after OK.

Every change on this page is applied with OK, like the rest of the window.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import DEFAULTS
from .. import _user_text as txt
from .._anonymizer_config_widget import AnonymizerConfigWidget
from .._privacy_settings import DISPLAY_ANONYMIZED_DEFAULT, FlowAcknowledgements
from ._page import (
    FOCUS_DETECTION,
    FOCUS_PATTERNS,
    FOCUS_SENDING,
    SettingsContext,
    SettingsPage,
    SettingsPageBase,
)

TITLE = "Privacy"
DESCRIPTION = (
    "What may leave this computer, and how names and other personal information are found "
    "and replaced."
)
TAB_SENDING = "Sending data away"
TAB_DETECTION = "Personal info: detection"
TAB_PATTERNS = "Personal info: patterns"
SHOWING_TEXTS_GROUP = "Showing texts"

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

_TAB_INDEX = {FOCUS_SENDING: 0, FOCUS_DETECTION: 1, FOCUS_PATTERNS: 2}

#: The settings keys behind the anonymizer rules (written through ``AnonymizerConfig``).
ANONYMIZER_KEYS = ("anonymizer_primary_model", "anonymizer_secondary_model", "anonymizer_patterns")


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


class PrivacyPage(SettingsPageBase):
    """Sending data away + personal-info detection + patterns."""

    page = SettingsPage.PRIVACY
    title = TITLE
    description = DESCRIPTION

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._rules = AnonymizerConfigWidget(
            context.language, context.anonymizer_config, context.app_service, self
        )
        self._rules.validity_changed.connect(self.validity_changed.emit)
        self._rules.setMinimumHeight(560)
        layout.addWidget(self._rules)

        self._sending_tab = SendingDataTab(context.acknowledgements)
        tabs = self._rules.tabs()
        tabs.setTabText(0, TAB_DETECTION)
        tabs.setTabText(1, TAB_PATTERNS)
        tabs.insertTab(0, self._sending_tab, TAB_SENDING)
        tabs.setCurrentIndex(0)
        self._available = context.privacy_available
        self._build_showing_texts_group(context.display_anonymized, context.privacy_available)

    def _build_showing_texts_group(self, checked: bool, available: bool) -> None:
        """Add the "hide personal information" choice above the detection models.

        It sits on the detection tab because hiding *uses* these detection settings, and the
        preview below shows the effect straight away.
        """
        group = QGroupBox(SHOWING_TEXTS_GROUP)
        layout = QVBoxLayout(group)
        self._hide_personal_cb = QCheckBox(txt.HIDE_PERSONAL_INFO_CHECKBOX)
        self._hide_personal_cb.setChecked(checked and available)
        self._hide_personal_cb.setEnabled(available)
        self._hide_personal_cb.setToolTip(
            txt.HIDE_PERSONAL_INFO_TIP if available else txt.HIDE_PERSONAL_INFO_UNAVAILABLE_TIP
        )
        layout.addWidget(self._hide_personal_cb)
        self._rules.detection_layout().insertWidget(0, group)

    # ── public API ────────────────────────────────────────────────────────────

    def anonymizer_config(self) -> object:
        """Return the ``AnonymizerConfig`` as currently shown."""
        return self._rules.anonymizer_config

    def display_anonymized(self) -> bool:
        """Whether the app should show placeholders instead of names (read after OK)."""
        return self._hide_personal_cb.isChecked()

    def hide_personal_checkbox(self) -> QCheckBox:
        """Return the "hide personal information in the texts shown" checkbox."""
        return self._hide_personal_cb

    def sending_tab(self) -> SendingDataTab:
        """Return the "Sending data away" tab."""
        return self._sending_tab

    def rules(self) -> AnonymizerConfigWidget:
        """Return the detection and pattern rules widget."""
        return self._rules

    # ── page protocol ─────────────────────────────────────────────────────────

    def owned_keys(self) -> frozenset[str]:
        return frozenset(ANONYMIZER_KEYS)

    def restore_defaults(self) -> None:
        """Reset the detection models, the patterns and the "hide names" choice.

        The remembered answers are kept: they have their own "Ask again" buttons.
        """
        from ...application.service import (  # noqa: PLC0415
            DEFAULT_ANONYMIZER_PATTERNS,
            AnonymizerConfig,
        )

        self._rules.set_config(
            AnonymizerConfig(
                primary_model=DEFAULTS["anonymizer_primary_model"] or None,
                secondary_model=DEFAULTS["anonymizer_secondary_model"],
                patterns=tuple(DEFAULT_ANONYMIZER_PATTERNS),
            )
        )
        if self._available:
            self._hide_personal_cb.setChecked(DISPLAY_ANONYMIZED_DEFAULT)

    def apply(self) -> None:
        """Write back the remembered answers taken back on the first tab."""
        self._sending_tab.apply()

    def is_valid(self) -> bool:
        return self._rules.is_valid()

    def focus(self, focus: str, preselect: Sequence[str] = ()) -> None:
        _ = preselect
        index = _TAB_INDEX.get(focus)
        if index is not None:
            self._rules.tabs().setCurrentIndex(index)

    def on_packages_changed(self) -> None:
        self._rules.reload_models()

    def shutdown(self) -> None:
        self._rules.shutdown()

    def values(self) -> dict[str, Any]:
        """The rules are handed back through :meth:`anonymizer_config`, not the dict."""
        return {}
