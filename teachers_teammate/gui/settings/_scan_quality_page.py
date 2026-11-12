"""Settings → Scan quality: how a page is prepared before it is read."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QGroupBox, QVBoxLayout, QWidget

from ...config import DEFAULTS, OcrConfig
from .._collapsible_section import CollapsibleSection
from .._model_wording import EXPERT_SETTINGS
from .._ocr_config_selector import ScanQualityControls
from ._page import SettingsContext, SettingsPage, SettingsPageBase

TITLE = "Scan quality"
DESCRIPTION = (
    "How scans and photos are cleaned up before the text is read. The defaults suit most "
    "scans; Preview… shows the effect on one of your documents."
)
GROUP_TITLE = "Preparing scans and photos"
DEBUG_LABEL = "Keep processed images for inspection"
DEBUG_TOOLTIP = (
    "When enabled, the processed images used for text recognition are\n"
    "kept in a temporary folder after the run."
)

#: The settings keys :class:`ScanQualityControls` shows (the temperature lives on AI models).
SCAN_KEYS = (
    "preprocess_method",
    "preprocess_dewarp",
    "preprocess_deskew",
    "preprocess_border_crop",
    "preprocess_denoise",
    "preprocess_gamma",
    "pdf_render_dpi",
    "pdf_text_layer",
)


class ScanQualityPage(SettingsPageBase):
    """Image preparation preset, corrections, enhancements; Expert: PDF handling, debug."""

    page = SettingsPage.SCAN_QUALITY
    title = TITLE
    description = DESCRIPTION

    #: The user asked to preview the image preparation (the window adds the OCR choice).
    preview_requested = Signal()

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        group = QGroupBox(GROUP_TITLE)
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
        group_layout.addWidget(self._controls)
        layout.addWidget(group)

        self._expert_section = CollapsibleSection(EXPERT_SETTINGS, collapsed=True)
        expert_layout = QVBoxLayout(self._expert_section.content_widget)
        expert_layout.setContentsMargins(0, 0, 0, 0)
        expert_layout.addWidget(self._controls.pdf_options_widget())
        self._debug = QCheckBox(DEBUG_LABEL)
        self._debug.setToolTip(DEBUG_TOOLTIP)
        expert_layout.addWidget(self._debug)
        layout.addWidget(self._expert_section)
        layout.addStretch(1)
        # Valid right after construction, as the old dialog was: unset keys show the defaults.
        self._controls.load(OcrConfig.from_settings(context.settings))
        self._debug.setChecked(bool(context.settings.get("debug", DEFAULTS["debug"])))

    def load(self, values: Mapping[str, Any]) -> None:
        self._controls.load(OcrConfig.from_settings(values))
        if "debug" in values:
            self._debug.setChecked(bool(values["debug"]))
        # A setting the user chose is the reason to open "Expert settings" at all.
        saved = OcrConfig.from_settings(values)
        self._expert_section.set_expanded(
            self._debug.isChecked() != DEFAULTS["debug"]
            or saved.pdf_render_dpi != DEFAULTS["pdf_render_dpi"]
            or saved.pdf_text_layer != DEFAULTS["pdf_text_layer"]
        )

    def values(self) -> dict[str, Any]:
        ocr = self._controls.ocr_config(DEFAULTS["ocr_engine"], DEFAULTS["ocr_model"])
        return {
            "preprocess_method": ocr.preprocess_method,
            "preprocess_dewarp": ocr.dewarp,
            "preprocess_deskew": ocr.deskew,
            "preprocess_border_crop": ocr.border_crop,
            "preprocess_denoise": ocr.denoise,
            "preprocess_gamma": ocr.gamma,
            "pdf_render_dpi": ocr.pdf_render_dpi,
            "pdf_text_layer": ocr.pdf_text_layer,
            "debug": self._debug.isChecked(),
        }

    def owned_keys(self) -> frozenset[str]:
        return frozenset((*SCAN_KEYS, "debug"))

    def set_engine_default(self, engine: str) -> None:
        """Apply the image preparation the newly chosen recognition method works best with."""
        self._controls.set_engine_default(engine)

    def ocr_config(self, engine: str, model: str) -> OcrConfig:
        """Return the full :class:`OcrConfig` for *engine*/*model* with this page's choices."""
        return self._controls.ocr_config(engine, model)
