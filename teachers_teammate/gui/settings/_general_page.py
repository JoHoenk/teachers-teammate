"""Settings → General: how the app behaves while it works, and what it does at start."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ...settings_schema import DEFAULTS, LogDetail, log_detail_of
from ...texts import settings as st
from ._page import SettingsContext, SettingsPage, SettingsPageBase

#: The two levels the window offers: errors only is for a start that has to be read afterwards.
_LOG_DETAILS = (
    (st.LOG_DETAIL_NORMAL, LogDetail.NORMAL),
    (st.LOG_DETAIL_DETAILED, LogDetail.DETAILED),
)


class GeneralPage(SettingsPageBase):
    """The settings that belong to no single step: keeping the computer awake, the update check."""

    page = SettingsPage.GENERAL
    title = st.PAGE_GENERAL
    description = st.PAGE_INTRO_GENERAL

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        group = QGroupBox(st.WHILE_PROCESSING_GROUP)
        group_layout = QVBoxLayout(group)
        self._keep_awake = QCheckBox(st.KEEP_AWAKE_LABEL)
        self._keep_awake.setToolTip(st.KEEP_AWAKE_TOOLTIP)
        group_layout.addWidget(self._keep_awake)
        note = QLabel(st.KEEP_AWAKE_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #888888;")
        group_layout.addWidget(note)
        layout.addWidget(group)

        start_group = QGroupBox(st.AT_START_GROUP)
        start_layout = QVBoxLayout(start_group)
        self._check_updates = QCheckBox(st.CHECK_UPDATES_LABEL)
        self._check_updates.setToolTip(st.CHECK_UPDATES_TOOLTIP)
        start_layout.addWidget(self._check_updates)
        start_note = QLabel(st.CHECK_UPDATES_NOTE)
        start_note.setWordWrap(True)
        start_note.setStyleSheet("color: #888888;")
        start_layout.addWidget(start_note)
        layout.addWidget(start_group)

        log_group = QGroupBox(st.LOG_GROUP)
        log_layout = QVBoxLayout(log_group)
        log_row = QHBoxLayout()
        detail_label = QLabel(st.LOG_DETAIL_LABEL)
        detail_label.setToolTip(st.LOG_DETAIL_TOOLTIP)
        self._log_detail = QComboBox()
        self._log_detail.setToolTip(st.LOG_DETAIL_TOOLTIP)
        for label, member in _LOG_DETAILS:
            self._log_detail.addItem(label, member.value)
        log_row.addWidget(detail_label)
        log_row.addWidget(self._log_detail)
        log_row.addStretch(1)
        log_layout.addLayout(log_row)
        log_note = QLabel(st.LOG_DETAIL_NOTE)
        log_note.setWordWrap(True)
        log_note.setStyleSheet("color: #888888;")
        log_layout.addWidget(log_note)
        layout.addWidget(log_group)

        layout.addStretch(1)
        # Valid right after construction: an unset key shows the default.
        self._keep_awake.setChecked(
            bool(context.settings.get("keep_awake", DEFAULTS["keep_awake"]))
        )
        self._check_updates.setChecked(
            bool(context.settings.get("check_for_updates", DEFAULTS["check_for_updates"]))
        )
        self._show_log_detail(context.settings.get("log_detail", DEFAULTS["log_detail"]))

    def _show_log_detail(self, value: object) -> None:
        """Show *value* in the combo; the level a flag chose has no item, and shows Normal."""
        index = self._log_detail.findData(log_detail_of(value).value)
        self._log_detail.setCurrentIndex(max(index, 0))

    def load(self, values: Mapping[str, Any]) -> None:
        if "keep_awake" in values:
            self._keep_awake.setChecked(bool(values["keep_awake"]))
        if "check_for_updates" in values:
            self._check_updates.setChecked(bool(values["check_for_updates"]))
        if "log_detail" in values:
            self._show_log_detail(values["log_detail"])

    def values(self) -> dict[str, Any]:
        return {
            "keep_awake": self._keep_awake.isChecked(),
            "check_for_updates": self._check_updates.isChecked(),
            "log_detail": str(self._log_detail.currentData()),
        }

    def owned_keys(self) -> frozenset[str]:
        return frozenset({"keep_awake", "check_for_updates", "log_detail"})
