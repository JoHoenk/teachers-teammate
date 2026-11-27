"""Settings → Scan quality: how a page is prepared before it is read, and marked when checked.

The ``debug`` key (keep the images of the preparation steps) is a tool for developers
(``--debug``): the page shows no switch for it and leaves the key alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout, QWidget

from ...config import OcrConfig
from ...settings_schema import DEFAULTS
from ...texts import settings as st
from ...texts.models import EXPERT_SETTINGS
from .._collapsible_section import CollapsibleSection
from .._ocr_config_selector import ScanQualityControls
from ._page import SettingsContext, SettingsPage, SettingsPageBase

#: The settings keys :class:`ScanQualityControls` shows (the temperature lives on AI models).
SCAN_KEYS = (
    "preprocess_method",
    "pdf_render_dpi",
    "pdf_text_layer",
)

#: The key of the scan's mark of the sentence under the cursor, shown below the preparation.
_MARKS_KEY = "scan_marks"


class ScanQualityPage(SettingsPageBase):
    """Image preparation (Automatic or Original page), the scan's marks; Expert: PDF handling."""

    page = SettingsPage.SCAN_QUALITY
    title = st.PAGE_SCAN_QUALITY
    description = st.PAGE_INTRO_SCAN_QUALITY

    #: The user asked to preview the image preparation (the window adds the OCR choice).
    preview_requested = Signal()
    #: The page models were downloaded from the line under the combo.
    addon_installed = Signal(str)

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        group = QGroupBox(st.PREPARING_GROUP)
        group_layout = QVBoxLayout(group)
        self._controls = ScanQualityControls(
            app_service=context.app_service,
            show_preview_button=True,
            show_pdf_text_layer=True,
            show_temperature=False,
            pdf_options_inline=False,
            parent=group,
        )
        self._controls.preprocess_preview_requested.connect(self.preview_requested.emit)
        self._controls.addon_installed.connect(self.addon_installed.emit)
        group_layout.addWidget(self._controls)
        layout.addWidget(group)

        marks_group = QGroupBox(st.SCAN_MARKS_GROUP)
        marks_layout = QVBoxLayout(marks_group)
        self._scan_marks = QCheckBox(st.SCAN_MARKS_LABEL)
        self._scan_marks.setToolTip(st.SCAN_MARKS_TOOLTIP)
        marks_layout.addWidget(self._scan_marks)
        marks_note = QLabel(st.SCAN_MARKS_NOTE)
        marks_note.setWordWrap(True)
        marks_note.setStyleSheet("color: #888888;")
        marks_layout.addWidget(marks_note)
        layout.addWidget(marks_group)

        self._expert_section = CollapsibleSection(EXPERT_SETTINGS, collapsed=True)
        expert_layout = QVBoxLayout(self._expert_section.content_widget)
        expert_layout.setContentsMargins(0, 0, 0, 0)
        expert_layout.addWidget(self._controls.pdf_options_widget())
        layout.addWidget(self._expert_section)
        layout.addStretch(1)
        # Valid right after construction, as the old dialog was: unset keys show the defaults.
        self._controls.load(OcrConfig.from_settings(context.settings))
        self._scan_marks.setChecked(bool(context.settings.get(_MARKS_KEY, DEFAULTS[_MARKS_KEY])))

    def load(self, values: Mapping[str, Any]) -> None:
        self._controls.load(OcrConfig.from_settings(values))
        if _MARKS_KEY in values:
            self._scan_marks.setChecked(bool(values[_MARKS_KEY]))
        # A setting the user chose is the reason to open "Expert settings" at all.
        saved = OcrConfig.from_settings(values)
        self._expert_section.set_expanded(
            saved.pdf_render_dpi != DEFAULTS["pdf_render_dpi"]
            or saved.pdf_text_layer != DEFAULTS["pdf_text_layer"]
        )

    def values(self) -> dict[str, Any]:
        ocr = self._controls.ocr_config(DEFAULTS["ocr_engine"], DEFAULTS["ocr_model"])
        return {
            "preprocess_method": ocr.preprocess_method,
            "pdf_render_dpi": ocr.pdf_render_dpi,
            "pdf_text_layer": ocr.pdf_text_layer,
            _MARKS_KEY: self._scan_marks.isChecked(),
        }

    def owned_keys(self) -> frozenset[str]:
        return frozenset((*SCAN_KEYS, _MARKS_KEY))

    def ocr_config(self, engine: str, model: str) -> OcrConfig:
        """Return the full :class:`OcrConfig` for *engine*/*model* with this page's choices."""
        return self._controls.ocr_config(engine, model)
