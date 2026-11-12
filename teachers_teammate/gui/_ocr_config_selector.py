"""Reusable OCR configuration widgets.

Three widgets, one per question:

- :class:`OcrModelPicker` — *which method and model read the pages* (plus where the engine
  runs and the package warnings).
- :class:`ScanQualityControls` — *how a page is prepared before it is read*: image
  preparation, corrections, enhancements, PDF DPI and the PDF text layer (and, where
  the owner has no Expert section of its own, the recognition temperature).
- :class:`OcrConfigSelector` — the two side by side, producing one :class:`OcrConfig`.
  Kept for the benchmark app, which needs the whole configuration in one place.

All discovery (engines, models, preprocess defaults, where an engine runs) is delegated to
the injected application service — the widgets contain presentation only, no business
logic.  Only engines that read pages on a computer the user controls are offered: online
vision models would receive the whole page, names included.

The shared, stateless helpers (the model-fetch thread, status-label rendering) live in
:mod:`teachers_teammate.gui._model_fetch` and are reused here so the logic has a single
source.
"""

from __future__ import annotations

from collections.abc import Callable
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
    QVBoxLayout,
    QWidget,
)

from ..config import DEFAULTS, OcrConfig
from ..domain.data_location import DataLocation
from ..domain.model_catalog import OCR_TEMPERATURE
from ..domain.pdf_text_layer import (
    PDF_TEXT_LAYER_MODES,
    PdfTextLayerMode,
    normalize_pdf_text_layer,
)
from ._collapsible_section import CollapsibleSection
from ._data_flow_summary import LocationHint
from ._hardware_profile_selector import HardwareProfileSelector
from ._model_fetch import (
    _STATUS_ERROR,
    _STATUS_LOADING,
    _BusyIndicator,
    _ModelFetchThread,
    _retire_thread,
    _set_combo,
    _SettingsAppService,
    _status_busy,
    _status_ok,
)
from ._model_guidance import OllamaModelGuidance, OpenDownloads
from ._model_wording import (
    EXPERT_SETTINGS,
    TEMPERATURE_TOOLTIP,
    asking_for_models_text,
    checking_models_text,
)

#: Image preparation presets, in the order they are offered.
PREPROCESS_METHODS = ("adaptive_threshold", "clahe", "grayscale", "none")


class OcrModelPicker(QWidget):
    """Recognition method and model, with the hints that explain both.

    Emits :attr:`engine_changed` whenever the method changes, so the owner can refresh
    whatever depends on it — the default image preparation, for one.
    """

    #: The recognition method changed; carries the new method's name.
    engine_changed = Signal(str)
    addon_installed = Signal(str)

    def __init__(
        self,
        *,
        app_service: _SettingsAppService,
        ollama_url: str = DEFAULTS["ollama_url"],
        hardware_profile: str = "auto",
        open_downloads: OpenDownloads | None = None,
        profile_selector: HardwareProfileSelector | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the picker.

        *profile_selector* is an owner's "Suggestions are for …" selector: with it the picker
        follows that one (the owner keeps it up to date) instead of resolving this computer
        again for itself, and shows no line of its own.
        """
        super().__init__(parent)
        self._app_service = app_service
        self._ollama_url = ollama_url
        self._fetch_thread: _ModelFetchThread | None = None
        self._shared_selector = profile_selector

        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        if profile_selector is not None:
            self._hw_selector = profile_selector
        else:
            self._hw_selector = HardwareProfileSelector(
                app_service=app_service,
                setting=hardware_profile,
                ollama_url=ollama_url,
                read_only=True,
                parent=self,
            )
            form.addRow(self._hw_selector)

        self._build_engine_row(form)
        self._build_model_row(form, open_downloads)
        self._update_where_hint()

    # ── construction ────────────────────────────────────────────────────────

    def _build_engine_row(self, form: QFormLayout) -> None:
        self._ocr_engine = QComboBox()
        self._ocr_engine.addItems(self._app_service.list_ocr_engines())
        self._ocr_engine.setToolTip(
            "Pages are always read on a computer you control. Online services are not offered: "
            "they would receive the whole page, including names and grades."
        )
        self._ocr_engine.currentTextChanged.connect(self._on_engine_changed)
        form.addRow("Recognition method:", self._ocr_engine)

        self._where_hint = LocationHint()
        form.addRow("", self._where_hint)

        self._extra_warn = QLabel()
        self._extra_warn.setStyleSheet("color: #c0392b;")
        self._extra_warn.setWordWrap(True)
        self._extra_warn.setVisible(False)
        form.addRow("", self._extra_warn)

        self._install_btn = QPushButton("Install PaddleOCR Addon…")
        self._install_btn.setVisible(False)
        self._install_btn.clicked.connect(self._on_install_paddle_addon)
        form.addRow("", self._install_btn)

    def _build_model_row(self, form: QFormLayout, open_downloads: OpenDownloads | None) -> None:
        self._ocr_model_label = QLabel("Model:")
        self._ocr_model_row = QWidget()
        ml = QHBoxLayout(self._ocr_model_row)
        ml.setContentsMargins(0, 0, 0, 0)
        self._ocr_model = QComboBox()
        self._ocr_model.setEditable(True)
        self._ocr_model.addItem(DEFAULTS["ocr_model"])
        self._ocr_model.setCurrentText(DEFAULTS["ocr_model"])
        self._ocr_model.currentTextChanged.connect(lambda _text: self._update_where_hint())
        self._status_lbl = QLabel(_STATUS_LOADING)
        self._status_lbl.setTextFormat(Qt.TextFormat.RichText)
        ml.addWidget(self._ocr_model, stretch=1)
        ml.addWidget(self._status_lbl)
        # Says the list is still coming, so a field that stays put for a moment is not a hang.
        self._busy = _BusyIndicator()
        ml.addWidget(self._busy)
        form.addRow(self._ocr_model_label, self._ocr_model_row)

        # Suitability hint + "Use best choice" button (Ollama engine only).
        self._guidance = OllamaModelGuidance(
            combo=self._ocr_model,
            app_service=self._app_service,
            category="ocr",
            profile_selector=self._hw_selector,
            ollama_url=self._ollama_url,
            open_downloads=open_downloads,
            parent=self,
        )
        form.addRow("", self._guidance)

    # ── public API ──────────────────────────────────────────────────────────

    def engine(self) -> str:
        """Return the chosen recognition method."""
        return self._ocr_engine.currentText()

    def set_engine(self, engine: str) -> None:
        """Choose the recognition method (applies its defaults through the signal)."""
        _set_combo(self._ocr_engine, engine)

    def model(self) -> str:
        """Return the chosen model name, falling back to the default when blank."""
        return self._ocr_model.currentText().strip() or DEFAULTS["ocr_model"]

    def set_model(self, model: str) -> None:
        """Choose the model by name."""
        if model:
            self._ocr_model.setCurrentText(model)

    def set_ollama_url(self, url: str) -> None:
        """Update the Ollama base URL used when fetching models (and ask the new server)."""
        new_url = url or DEFAULTS["ollama_url"]
        changed = new_url != self._ollama_url
        self._ollama_url = new_url
        if self._shared_selector is None:
            self._hw_selector.set_ollama_url(self._ollama_url)
        self._guidance.set_ollama_url(self._ollama_url)
        self._update_where_hint()
        if changed and self._ocr_engine.currentText() == "ollama":
            self._auto_fetch()

    def refresh(self) -> None:
        """Re-apply engine-dependent state (visibility, model list, warnings)."""
        self._on_engine_changed(self._ocr_engine.currentText())

    def on_packages_changed(self) -> None:
        """An add-on or model was installed: check the engine's package and the list again."""
        if self._ocr_engine.currentText() == "ollama":
            self._app_service.invalidate_model_cache("ollama", base_url=self._ollama_url)
        self.refresh()

    def stop_threads(self) -> None:
        """Detach any in-flight model-fetch thread (call before closing).

        The fetch thread runs a plain function with no event loop, so it cannot be
        interrupted and must not be destroyed while running (that aborts the
        process).  It is parked until it ends on its own, without blocking the
        GUI thread.  Hardware detection needs no handling: it runs on the shared
        background pool.
        """
        _retire_thread(self._fetch_thread)

    # ── internal ────────────────────────────────────────────────────────────

    def _on_engine_changed(self, engine: str) -> None:
        self.engine_changed.emit(engine)

        is_ollama = engine == "ollama"
        self._ocr_model_label.setVisible(is_ollama)
        self._ocr_model_row.setVisible(is_ollama)
        # Hint line and "Use best choice" button exist only for Ollama.
        self._guidance.setVisible(is_ollama)

        paddle_missing = engine == "paddleocr" and not self._app_service.is_module_importable(
            "paddleocr"
        )
        frozen = getattr(sys, "frozen", False)
        if paddle_missing and frozen:
            warn = "paddleocr is not installed."
        elif paddle_missing:
            warn = '⚠ Not installed — run:  pip install "teachers-teammate[paddle]"'
        else:
            warn = ""
        self._extra_warn.setText(warn)
        self._extra_warn.setVisible(bool(warn))
        self._install_btn.setVisible(paddle_missing and frozen)

        self._auto_fetch()
        self._update_where_hint()

    def _update_where_hint(self) -> None:
        """Say where the chosen engine reads pages (and warn about cloud models)."""
        engine = self._ocr_engine.currentText()
        model = self._ocr_model.currentText().strip()
        location = self._app_service.describe_ocr_location(engine, model, self._ollama_url)
        refusal = self._app_service.describe_ocr_refusal(engine, model, self._ollama_url)
        details = ""
        if refusal:
            note = "not allowed"
            details = refusal
        elif location.location is DataLocation.THIS_COMPUTER:
            note = "pages never leave it"
        else:
            note = "pages are sent there"
        self._where_hint.set_location(location, note, details)

    def _auto_fetch(self) -> None:
        engine = self._ocr_engine.currentText()
        # A fetch still running belongs to an engine that may no longer be the chosen one.
        _retire_thread(self._fetch_thread)
        if engine == "ollama":
            # Always fetch in the thread (a cache hit there is instant): the
            # classification of custom models must not run on the GUI thread.
            url = self._ollama_url
            self._set_busy(True, asking_for_models_text("Ollama"))

            def fetch(report: Callable[[str], None]) -> list[str]:
                tags = self._app_service.list_ocr_models(url)
                # Classify here so set_models() only needs cache lookups.  It costs one
                # request per model, so say how many there are before it starts.
                report(checking_models_text(len(tags)))
                self._app_service.classify_ollama_models(tags, url)
                return tags

            self._fetch_thread = _ModelFetchThread(fetch)
            self._fetch_thread.models_ready.connect(self._on_models_fetched)
            self._fetch_thread.progress.connect(self._on_fetch_progress)
            self._fetch_thread.start()
        else:
            self._set_busy(False)
            self._status_lbl.setText("")

    def _set_busy(self, busy: bool, text: str = "") -> None:
        """Show or hide the busy bar; while the list is on its way, no stale hints."""
        self._busy.setVisible(busy)
        if not busy:
            return
        self._status_lbl.setText(_status_busy(text))
        self._guidance.setVisible(False)

    def _on_fetch_progress(self, _tag: str, text: str) -> None:
        """Say what the running fetch is doing."""
        self._set_busy(True, text)

    def _on_models_fetched(self, models: list) -> None:
        if self._ocr_engine.currentText() != "ollama":
            return  # an answer for an engine the user has already left
        self._set_busy(False)
        # Waiting hid the hint; show it again even when Ollama did not answer, so the
        # button to download the best choice is still there.
        self._guidance.setVisible(True)
        if not models:
            self._status_lbl.setText(_STATUS_ERROR)
            self._guidance.set_models([])
            return
        self._status_lbl.setText(_status_ok(len(models), "Ollama"))
        # Guidance sorts by suitability and decorates via roles; the combo text stays the
        # raw tag.
        self._guidance.set_models(models)

    def _on_install_paddle_addon(self) -> None:
        from ._addon_installer_dialog import AddonInstallerDialog  # noqa: PLC0415

        dlg = AddonInstallerDialog("paddle", self.window())
        dlg.installed.connect(self.addon_installed)
        dlg.exec()


class ScanQualityControls(QWidget):
    """How a page is prepared before it is read: presets, corrections, PDF handling."""

    #: The user asked to preview the current image preparation.
    preprocess_preview_requested = Signal()

    def __init__(
        self,
        *,
        app_service: _SettingsAppService,
        show_preview_button: bool = True,
        show_pdf_text_layer: bool = True,
        show_temperature: bool = True,
        expert_collapsed: bool = True,
        pdf_options_inline: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """Build the controls.

        With ``pdf_options_inline=False`` the PDF resolution and text-layer mode are not
        laid out here: the owner places :meth:`pdf_options_widget` itself (the Settings
        window keeps them under "Expert settings").
        """
        super().__init__(parent)
        self._app_service = app_service
        self._pdf_options_inline = pdf_options_inline
        self._pdf_options: QWidget | None = None
        self._pdf_text_layer: QComboBox | None = None
        self._expert_section: CollapsibleSection | None = None
        self._show_pdf_text_layer = show_pdf_text_layer

        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._build_preprocess_row(form, show_preview_button)
        self._build_temperature_row(form, collapsed=expert_collapsed, shown=show_temperature)
        if show_pdf_text_layer:
            self._build_pdf_text_layer_row(form)

    # ── construction ────────────────────────────────────────────────────────

    def _build_preprocess_row(self, form: QFormLayout, show_preview_button: bool) -> None:
        preprocess_row = QWidget()
        ph = QHBoxLayout(preprocess_row)
        ph.setContentsMargins(0, 0, 0, 0)
        self._preprocess = QComboBox()
        self._preprocess.addItems(list(PREPROCESS_METHODS))
        self._preprocess.setToolTip(
            "Contrast enhancement applied before text recognition.\n\n"
            "Enhanced contrast (adaptive_threshold) — binary output; robust to uneven\n"
            "  lighting; best for Ollama and most handwriting.\n"
            "Balanced contrast (clahe) — grayscale output; improves faint or low-\n"
            "  contrast text; best for Tesseract.\n"
            "Grayscale — converts to grayscale without further processing.\n"
            "None — pass the original image directly to the OCR engine.\n"
            "  Disables the corrections and enhancements below, which have no effect."
        )
        self._preprocess.currentTextChanged.connect(self._on_preprocess_method_changed)
        ph.addWidget(self._preprocess, stretch=1)
        if show_preview_button:
            preview_btn = QPushButton("Preview…")
            preview_btn.setFixedWidth(72)
            preview_btn.clicked.connect(self.preprocess_preview_requested.emit)
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
        er.addWidget(self._denoise)
        er.addWidget(self._gamma)
        if self._pdf_options_inline:
            er.addWidget(QLabel("PDF:"))
            er.addWidget(self._pdf_dpi)
        er.addStretch()
        form.addRow("Enhancements:", enhancements_row)

        # "None" skips every pre-step (HandwritingPreprocessor.preprocess returns the
        # original file unchanged), so the checkboxes above have no effect under it.
        self._on_preprocess_method_changed(self._preprocess.currentText())

    def _build_temperature_row(
        self, form: QFormLayout, *, collapsed: bool = True, shown: bool = True
    ) -> None:
        """Build the temperature control; without *shown* it only keeps the loaded value.

        The Settings window shows the recognition temperature in the Expert section of
        AI models, so its Scan quality page passes ``shown=False``.
        """
        self._ocr_temperature = QDoubleSpinBox(self)
        self._ocr_temperature.setRange(0.0, 2.0)
        self._ocr_temperature.setSingleStep(0.1)
        self._ocr_temperature.setDecimals(1)
        self._ocr_temperature.setValue(OCR_TEMPERATURE)
        self._ocr_temperature.setToolTip(TEMPERATURE_TOOLTIP)
        if not shown:
            self._ocr_temperature.setVisible(False)
            return

        self._expert_section = CollapsibleSection(EXPERT_SETTINGS, collapsed=collapsed)
        inner = QFormLayout(self._expert_section.content_widget)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addRow("Temperature:", self._ocr_temperature)
        form.addRow(self._expert_section)

    def _build_pdf_text_layer_row(self, form: QFormLayout) -> None:
        self._pdf_text_layer = QComboBox()
        self._pdf_text_layer.addItems(list(PDF_TEXT_LAYER_MODES))
        self._pdf_text_layer.setToolTip(
            "Use the text embedded in a PDF instead of recognising it.\n\n"
            "auto — only when every page is clean, text-only (no scans, no\n"
            "  handwriting, annotations or form fields); otherwise the pages\n"
            "  are recognised.\n"
            "always — whenever every page contains any text.\n"
            "never — always recognise the rendered pages.\n"
            "Has no effect on image or text files."
        )
        if self._pdf_options_inline:
            form.addRow("PDF text layer:", self._pdf_text_layer)

    # ── public API ──────────────────────────────────────────────────────────

    def pdf_options_widget(self) -> QWidget:
        """Return the PDF resolution (and text-layer mode, if shown) as one form to place.

        Only for ``pdf_options_inline=False``; the widgets move into the returned form.
        """
        if self._pdf_options is None:
            holder = QWidget()
            form = QFormLayout(holder)
            form.setContentsMargins(0, 0, 0, 0)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
            form.addRow("PDF resolution:", self._pdf_dpi)
            if self._pdf_text_layer is not None:
                form.addRow("PDF text layer:", self._pdf_text_layer)
            self._pdf_options = holder
        return self._pdf_options

    def set_engine_default(self, engine: str) -> None:
        """Apply the image preparation this recognition method works best with."""
        default = self._app_service.default_preprocess_for_engine(engine)
        if default:
            _set_combo(self._preprocess, default)

    def preprocess_method(self) -> str:
        """Return the chosen image preparation preset."""
        return self._preprocess.currentText()

    def set_preprocess_method(self, method: str) -> None:
        """Choose the image preparation preset."""
        _set_combo(self._preprocess, method)

    def temperature(self) -> float:
        """Return the recognition temperature."""
        return self._ocr_temperature.value()

    def set_temperature(self, value: float) -> None:
        """Set the recognition temperature and reveal Expert settings if it was changed."""
        self._ocr_temperature.setValue(value)
        if self._expert_section is not None:
            # A temperature the user chose is the reason to open "Expert settings" at all.
            self._expert_section.set_expanded(value != OCR_TEMPERATURE)

    def load(self, ocr: OcrConfig) -> None:
        """Populate the controls from an :class:`OcrConfig` value object."""
        self.set_preprocess_method(ocr.preprocess_method)
        self.set_temperature(ocr.temperature)
        self._dewarp.setChecked(ocr.dewarp)
        self._deskew.setChecked(ocr.deskew)
        self._border_crop.setChecked(ocr.border_crop)
        self._denoise.setChecked(ocr.denoise)
        self._gamma.setChecked(ocr.gamma)
        self._pdf_dpi.setValue(ocr.pdf_render_dpi)
        if self._pdf_text_layer is not None:
            _set_combo(self._pdf_text_layer, ocr.pdf_text_layer)
        self._on_preprocess_method_changed(self._preprocess.currentText())

    def pdf_text_layer(self) -> PdfTextLayerMode:
        """Return the chosen PDF text-layer mode."""
        if self._pdf_text_layer is None:
            return DEFAULTS["pdf_text_layer"]
        return normalize_pdf_text_layer(self._pdf_text_layer.currentText())

    def ocr_config(self, engine: str, model: str) -> OcrConfig:
        """Combine the recognition choice with this widget's scan-quality choices."""
        return OcrConfig(
            engine=engine,
            model=model,
            preprocess_method=self.preprocess_method(),
            temperature=self.temperature(),
            pdf_render_dpi=self._pdf_dpi.value(),
            pdf_text_layer=self.pdf_text_layer(),
            dewarp=self._dewarp.isChecked(),
            deskew=self._deskew.isChecked(),
            border_crop=self._border_crop.isChecked(),
            denoise=self._denoise.isChecked(),
            gamma=self._gamma.isChecked(),
        )

    # ── internal ────────────────────────────────────────────────────────────

    def _on_preprocess_method_changed(self, method: str) -> None:
        """Disable the pre-step checkboxes under "none" -- they would have no effect."""
        enabled = method != "none"
        for cb in (self._dewarp, self._deskew, self._border_crop, self._denoise, self._gamma):
            cb.setEnabled(enabled)


class OcrConfigSelector(QWidget):
    """Engine/model/preprocess/temperature picker producing an :class:`OcrConfig`.

    Composes :class:`OcrModelPicker` and :class:`ScanQualityControls`; the benchmark app
    drives OCR selection through this one widget.
    """

    preprocess_preview_requested = Signal(object)  # emits the current OcrConfig
    addon_installed = Signal(str)

    def __init__(
        self,
        *,
        app_service: _SettingsAppService,
        ollama_url: str = DEFAULTS["ollama_url"],
        show_preview_button: bool = True,
        show_pdf_text_layer: bool = True,
        hardware_profile: str = "auto",
        expert_collapsed: bool = True,
        open_downloads: OpenDownloads | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._picker = OcrModelPicker(
            app_service=app_service,
            ollama_url=ollama_url,
            hardware_profile=hardware_profile,
            open_downloads=open_downloads,
            parent=self,
        )
        self._picker.addon_installed.connect(self.addon_installed.emit)
        layout.addWidget(self._picker)

        self._scan = ScanQualityControls(
            app_service=app_service,
            show_preview_button=show_preview_button,
            show_pdf_text_layer=show_pdf_text_layer,
            expert_collapsed=expert_collapsed,
            parent=self,
        )
        layout.addWidget(self._scan)

        self._picker.engine_changed.connect(self._scan.set_engine_default)
        self._scan.preprocess_preview_requested.connect(
            lambda: self.preprocess_preview_requested.emit(self.get_ocr_config())
        )

    # ── public API ─────────────────────────────────────────────────────────

    def set_ollama_url(self, url: str) -> None:
        """Update the Ollama base URL used when fetching models."""
        self._picker.set_ollama_url(url)

    def load_ocr_config(self, ocr: OcrConfig) -> None:
        """Populate the controls from an :class:`OcrConfig` value object."""
        self._picker.set_engine(ocr.engine)
        # Re-apply engine-dependent state first (it resets preprocess to the engine
        # default and populates the model list), then override with the saved values.
        self.refresh()
        self._picker.set_model(ocr.model)
        self._scan.load(ocr)

    def get_ocr_config(self) -> OcrConfig:
        """Return the currently selected configuration as an :class:`OcrConfig`."""
        return self._scan.ocr_config(self._picker.engine(), self._picker.model())

    def refresh(self) -> None:
        """Re-apply engine-dependent state (visibility, defaults, model fetch)."""
        self._picker.refresh()

    def stop_threads(self) -> None:
        """Detach any in-flight model-fetch thread (call before closing)."""
        self._picker.stop_threads()
