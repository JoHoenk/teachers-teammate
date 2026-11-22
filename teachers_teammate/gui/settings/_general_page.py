"""Settings → General: how the app behaves while it works, and what it does at start."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout, QWidget

from ...config import DEFAULTS
from ...texts import settings as st
from ._page import SettingsContext, SettingsPage, SettingsPageBase


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

        layout.addStretch(1)
        # Valid right after construction: an unset key shows the default.
        self._keep_awake.setChecked(
            bool(context.settings.get("keep_awake", DEFAULTS["keep_awake"]))
        )
        self._check_updates.setChecked(
            bool(context.settings.get("check_for_updates", DEFAULTS["check_for_updates"]))
        )

    def load(self, values: Mapping[str, Any]) -> None:
        if "keep_awake" in values:
            self._keep_awake.setChecked(bool(values["keep_awake"]))
        if "check_for_updates" in values:
            self._check_updates.setChecked(bool(values["check_for_updates"]))

    def values(self) -> dict[str, Any]:
        return {
            "keep_awake": self._keep_awake.isChecked(),
            "check_for_updates": self._check_updates.isChecked(),
        }

    def owned_keys(self) -> frozenset[str]:
        return frozenset({"keep_awake", "check_for_updates"})
