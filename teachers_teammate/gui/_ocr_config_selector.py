"""Reusable OCR configuration selector widget.

Encapsulates the engine / model / preprocessing / temperature controls plus the
background model-fetch behaviour, so both the main settings dialog and the
benchmark app drive OCR selection through one widget.  All discovery
(providers, models, preprocess defaults) is delegated to the injected
application service — the widget contains presentation only, no business logic.

The shared, stateless helpers (provider availability filtering, the model-fetch
thread, status-label rendering, the native-engine set) live in
:mod:`teachers_teammate.gui._settings_dialog` and are reused here so the logic
has a single source.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from ..config import DEFAULTS, OcrConfig
from ._settings_dialog import (
    _NATIVE_OCR_ENGINES,
    _STATUS_ERROR,
    _STATUS_KEY_MISSING,
    _STATUS_LOADING,
    _available_providers,
    _install_hint,
    _ModelFetchThread,
    _set_combo,
    _SettingsAppService,
    _status_ok,
    _stop_thread,
)


class OcrConfigSelector(QWidget):
    """Engine/model/preprocess/temperature picker producing an :class:`OcrConfig`."""

    preprocess_preview_requested = Signal(str)
    addon_installed = Signal(str)

    def __init__(
        self,
        *,
        app_service: _SettingsAppService,
        ollama_url: str = DEFAULTS["ollama_url"],
        show_preview_button: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_service = app_service
        self._ollama_url = ollama_url
        self._fetch_thread: _ModelFetchThread | None = None

        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self._build_engine_row(form)
        self._build_model_row(form)
        self._build_preprocess_row(form, show_preview_button)
        self._build_temperature_row(form)

    def _build_engine_row(self, form: QFormLayout) -> None:
        self._ocr_engine = QComboBox()
        native = ["ollama", "tesseract", "paddleocr"]
        provider_engines = [
            p for p in _available_providers(self._app_service) if p not in _NATIVE_OCR_ENGINES
        ]
        self._ocr_engine.addItems(native + provider_engines)
        self._ocr_engine.currentTextChanged.connect(self._on_engine_changed)
        form.addRow("Recognition method:", self._ocr_engine)

        self._extra_warn = QLabel()
        self._extra_warn.setStyleSheet("color: #c0392b;")
        self._extra_warn.setWordWrap(True)
        self._extra_warn.setVisible(False)
        form.addRow("", self._extra_warn)

        self._install_btn = QPushButton("Install PaddleOCR Addon…")
        self._install_btn.setVisible(False)
        self._install_btn.clicked.connect(self._on_install_paddle_addon)
        form.addRow("", self._install_btn)

    def _build_model_row(self, form: QFormLayout) -> None:
        self._ocr_model_label = QLabel("Model:")
        self._ocr_model_row = QWidget()
        ml = QHBoxLayout(self._ocr_model_row)
        ml.setContentsMargins(0, 0, 0, 0)
        self._ocr_model = QComboBox()
        self._ocr_model.setEditable(True)
        self._ocr_model.addItem(DEFAULTS["ocr_model"])
        self._ocr_model.setCurrentText(DEFAULTS["ocr_model"])
        self._status_lbl = QLabel(_STATUS_LOADING)
        self._status_lbl.setTextFormat(Qt.TextFormat.RichText)
        ml.addWidget(self._ocr_model, stretch=1)
        ml.addWidget(self._status_lbl)
        form.addRow(self._ocr_model_label, self._ocr_model_row)

    def _build_preprocess_row(self, form: QFormLayout, show_preview_button: bool) -> None:
        preprocess_row = QWidget()
        ph = QHBoxLayout(preprocess_row)
        ph.setContentsMargins(0, 0, 0, 0)
        self._preprocess = QComboBox()
        self._preprocess.addItems(["adaptive_threshold", "clahe", "grayscale", "none"])
        self._preprocess.setToolTip(
            "Contrast enhancement applied before text recognition.\n\n"
            "Enhanced contrast (adaptive_threshold) — binary output; robust to uneven\n"
            "  lighting; best for Ollama and most handwriting.\n"
            "Balanced contrast (clahe) — grayscale output; improves faint or low-\n"
            "  contrast text; best for Tesseract.\n"
            "Grayscale — converts to grayscale without further processing.\n"
            "None — pass the original image directly to the OCR engine."
        )
        ph.addWidget(self._preprocess, stretch=1)
        if show_preview_button:
            preview_btn = QPushButton("Preview…")
            preview_btn.setFixedWidth(72)
            preview_btn.clicked.connect(
                lambda: self.preprocess_preview_requested.emit(self._preprocess.currentText())
            )
            ph.addWidget(preview_btn)
        form.addRow("Image preparation:", preprocess_row)

        # Geometric correction checkboxes
        corrections_row = QWidget()
        cr = QHBoxLayout(corrections_row)
        cr.setContentsMargins(0, 0, 0, 0)
        self._dewarp = QCheckBox("Dewarp")
        self._dewarp.setToolTip(
            "Correct perspective distortion.\n"
            "Use when documents are photographed at an angle or from books.\n"
            "Has no effect on flat-bed scans."
        )
        self._deskew = QCheckBox("Deskew")
        self._deskew.setToolTip(
            "Correct page rotation up to ±45°.\n"
            "Use when handwriting or the scanning angle introduces a tilt.\n"
            "Skips correction if the detected angle is below 0.5°."
        )
        self._border_crop = QCheckBox("Border crop")
        self._border_crop.setToolTip(
            "Remove dark scanner borders before OCR.\n"
            "Reduces image size → faster processing.\n"
            "Not recommended for photographed documents."
        )
        for cb in (self._dewarp, self._deskew, self._border_crop):
            cr.addWidget(cb)
        cr.addStretch()
        form.addRow("Corrections:", corrections_row)

        # Tone / noise enhancements + PDF DPI
        enhancements_row = QWidget()
        er = QHBoxLayout(enhancements_row)
        er.setContentsMargins(0, 0, 0, 0)
        self._denoise = QCheckBox("Denoise")
        self._denoise.setToolTip(
            "Non-local means noise removal.\n"
            "Use for scans with visible grain or faint pencil artifacts.\n"
            "Adds ~1-2 s processing time per page."
        )
        self._gamma = QCheckBox("Brighten")
        self._gamma.setToolTip(
            "Gamma correction (gamma=0.5) to brighten dark or underexposed scans.\n"
            "Applied before the contrast step for best results."
        )
        self._pdf_dpi = QSpinBox()
        self._pdf_dpi.setRange(72, 600)
        self._pdf_dpi.setSingleStep(72)
        self._pdf_dpi.setSuffix(" DPI")
        self._pdf_dpi.setToolTip(
            "Resolution for rendering PDF pages to images before OCR.\n"
            "72 = screen quality, 150 = draft, 300 = recommended, 600 = high quality.\n"
            "Has no effect on image inputs (JPG, PNG)."
        )
        pdf_dpi_lbl = QLabel("PDF:")
        for w in (self._denoise, self._gamma, pdf_dpi_lbl, self._pdf_dpi):
            er.addWidget(w)
        er.addStretch()
        form.addRow("Enhancements:", enhancements_row)

    def _build_temperature_row(self, form: QFormLayout) -> None:
        self._ocr_temperature = QDoubleSpinBox()
        self._ocr_temperature.setRange(0.0, 2.0)
        self._ocr_temperature.setSingleStep(0.1)
        self._ocr_temperature.setDecimals(1)
        self._ocr_temperature.setValue(DEFAULTS["ocr_temperature"])
        form.addRow("Temperature:", self._ocr_temperature)

    # ── public API ─────────────────────────────────────────────────────────

    def set_ollama_url(self, url: str) -> None:
        """Update the Ollama base URL used when fetching models."""
        self._ollama_url = url or DEFAULTS["ollama_url"]

    def load_ocr_config(self, ocr: OcrConfig) -> None:
        """Populate the controls from an :class:`OcrConfig` value object."""
        engine = ocr.provider if ocr.engine == "langchain" else ocr.engine
        _set_combo(self._ocr_engine, engine)
        # Re-apply engine-dependent state first (it resets preprocess to the engine
        # default and populates the model list), then override with the saved values.
        self.refresh()
        if ocr.model:
            self._ocr_model.setCurrentText(ocr.model)
        _set_combo(self._preprocess, ocr.preprocess_method)
        self._ocr_temperature.setValue(ocr.temperature)
        self._dewarp.setChecked(ocr.dewarp)
        self._deskew.setChecked(ocr.deskew)
        self._border_crop.setChecked(ocr.border_crop)
        self._denoise.setChecked(ocr.denoise)
        self._gamma.setChecked(ocr.gamma)
        self._pdf_dpi.setValue(ocr.pdf_render_dpi)

    def get_ocr_config(self) -> OcrConfig:
        """Return the currently selected configuration as an :class:`OcrConfig`."""
        engine = self._ocr_engine.currentText()
        if engine in _NATIVE_OCR_ENGINES:
            ocr_engine, provider = engine, ""
        else:
            ocr_engine, provider = "langchain", engine
        return OcrConfig(
            engine=ocr_engine,
            model=self._ocr_model.currentText().strip() or DEFAULTS["ocr_model"],
            provider=provider,
            preprocess_method=self._preprocess.currentText(),
            temperature=self._ocr_temperature.value(),
            pdf_render_dpi=self._pdf_dpi.value(),
            dewarp=self._dewarp.isChecked(),
            deskew=self._deskew.isChecked(),
            border_crop=self._border_crop.isChecked(),
            denoise=self._denoise.isChecked(),
            gamma=self._gamma.isChecked(),
        )

    def refresh(self) -> None:
        """Re-apply engine-dependent state (visibility, defaults, model fetch)."""
        self._on_engine_changed(self._ocr_engine.currentText())

    def stop_threads(self) -> None:
        """Wait for any in-flight model-fetch thread to finish (call before closing).

        The fetch thread runs a plain function with no event loop, so ``quit()``
        cannot interrupt it; we wait for it to complete so it is not destroyed
        while still running (which aborts the process).
        """
        thread = self._fetch_thread
        if thread is not None and thread.isRunning():
            thread.wait(5000)

    # ── internal ───────────────────────────────────────────────────────────

    def _on_engine_changed(self, engine: str) -> None:
        default = self._app_service.default_preprocess_for_engine(engine)
        if default:
            idx = self._preprocess.findText(default)
            if idx >= 0:
                self._preprocess.setCurrentIndex(idx)

        is_ollama = engine == "ollama"
        is_provider_engine = engine not in _NATIVE_OCR_ENGINES
        self._ocr_model_label.setVisible(is_ollama or is_provider_engine)
        self._ocr_model_row.setVisible(is_ollama or is_provider_engine)

        paddle_missing = engine == "paddleocr" and not self._app_service.is_module_importable(
            "paddleocr"
        )
        frozen = getattr(sys, "frozen", False)
        if paddle_missing and frozen:
            warn = "paddleocr is not installed."
        elif paddle_missing:
            warn = _install_hint(self._app_service, "paddle", ["paddleocr"])
        else:
            warn = ""
        self._extra_warn.setText(warn)
        self._extra_warn.setVisible(bool(warn))
        self._install_btn.setVisible(paddle_missing and frozen)

        if is_provider_engine:
            info = self._app_service.get_provider_info(engine)
            prev = self._ocr_model.currentText()
            self._ocr_model.clear()
            models = info.get("models", [])
            if models:
                self._ocr_model.addItems(models)
            idx = self._ocr_model.findText(prev)
            if idx >= 0:
                self._ocr_model.setCurrentIndex(idx)
            elif prev:
                self._ocr_model.setCurrentText(prev)
            else:
                self._ocr_model.setCurrentText(info.get("default_model", ""))
            self._app_service.invalidate_model_cache(engine)

        self._auto_fetch()

    def _auto_fetch(self) -> None:
        engine = self._ocr_engine.currentText()
        if engine == "ollama":
            cached = self._app_service.get_cached_models("ollama", base_url=self._ollama_url)
            if cached is not None:
                self._on_models_fetched(cached)
                return
            self._status_lbl.setText(_STATUS_LOADING)
            _stop_thread(self._fetch_thread)
            url = self._ollama_url
            self._fetch_thread = _ModelFetchThread(
                lambda: self._app_service.list_provider_models("ollama", base_url=url)
            )
            self._fetch_thread.models_ready.connect(self._on_models_fetched)
            self._fetch_thread.start()
        elif engine not in _NATIVE_OCR_ENGINES:
            info = self._app_service.get_provider_info(engine)
            if info.get("needs_api_key") and not os.environ.get(info.get("env_key", "")):
                self._status_lbl.setText(_STATUS_KEY_MISSING)
                return
            cached = self._app_service.get_cached_models(engine)
            if cached is not None:
                self._on_models_fetched(cached)
                return
            self._status_lbl.setText(_STATUS_LOADING)
            _stop_thread(self._fetch_thread)
            self._fetch_thread = _ModelFetchThread(
                lambda p=engine: self._app_service.list_provider_models(p)
            )
            self._fetch_thread.models_ready.connect(self._on_models_fetched)
            self._fetch_thread.start()
        else:
            self._status_lbl.setText("")

    def _on_models_fetched(self, models: list) -> None:
        if not models:
            self._status_lbl.setText(_STATUS_ERROR)
            return
        self._status_lbl.setText(_status_ok(len(models)))
        prev = self._ocr_model.currentText()
        self._ocr_model.clear()
        self._ocr_model.addItems(models)
        idx = self._ocr_model.findText(prev)
        if idx >= 0:
            self._ocr_model.setCurrentIndex(idx)
        else:
            self._ocr_model.setCurrentText(prev)

    def _on_install_paddle_addon(self) -> None:
        from ._addon_installer_dialog import AddonInstallerDialog  # noqa: PLC0415

        dlg = AddonInstallerDialog("paddle", self)
        dlg.installed.connect(self.addon_installed)
        dlg.exec()
