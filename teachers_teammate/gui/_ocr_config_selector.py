"""Reusable OCR configuration widgets.

Three widgets, one per question:

- :class:`OcrModelPicker` — *which method and model read the pages* (plus where the engine
  runs and the package warnings).
- :class:`ScanQualityControls` — *how a page is prepared before it is read*: Automatic
  or Original page, PDF DPI and the PDF text layer (and, where the owner has no Expert
  section of its own, the recognition temperature).
- :class:`OcrConfigSelector` — the two side by side, producing one :class:`OcrConfig`.
  Kept for the benchmark app, which needs the whole configuration in one place.

All discovery (engines, models, where an engine runs) is delegated to
the injected application service — the widgets contain presentation only, no business
logic.  Only engines that read pages on a computer the user controls are offered: online
vision models would receive the whole page, names included.

The shared, stateless helpers (the model-fetch thread, status-label rendering) live in
:mod:`teachers_teammate.gui._model_fetch` and are reused here so the logic has a single
source.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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

from ..config import DEFAULTS, PREPROCESS_METHODS, OcrConfig
from ..domain.data_location import ENGINE_OLLAMA, ENGINE_PPOCR, DataLocation
from ..domain.local_models import PAGE_MODELS_ADDON_ID, READER_ADDON_ID
from ..domain.model_catalog import OCR_TEMPERATURE
from ..domain.pdf_text_layer import (
    PDF_TEXT_LAYER_MODES,
    PdfTextLayerMode,
    normalize_pdf_text_layer,
)
from . import _user_text as txt
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
    _start_thread,
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
        # Labels for the teacher, engine ids as data: nothing else reads the shown text.
        for engine in self._app_service.list_ocr_engines():
            self._ocr_engine.addItem(txt.OCR_ENGINE_LABELS.get(engine, engine), engine)
        self._ocr_engine.setToolTip(
            "Pages are always read on a computer you control. Online services are not offered: "
            "they would receive the whole page, including names and grades."
        )
        self._ocr_engine.currentIndexChanged.connect(self._on_engine_index_changed)
        form.addRow("Recognition method:", self._ocr_engine)

        self._engine_hint = QLabel()
        self._engine_hint.setWordWrap(True)
        self._engine_hint.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow("", self._engine_hint)

        self._where_hint = LocationHint()
        form.addRow("", self._where_hint)

        self._extra_warn = QLabel()
        self._extra_warn.setStyleSheet("color: #c0392b;")
        self._extra_warn.setWordWrap(True)
        self._extra_warn.setVisible(False)
        form.addRow("", self._extra_warn)

        self._install_btn = QPushButton(txt.READER_DOWNLOAD_LABEL)
        self._install_btn.setVisible(False)
        self._install_btn.clicked.connect(self._on_download_reader)
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
        return str(self._ocr_engine.currentData() or "")

    def set_engine(self, engine: str) -> None:
        """Choose the recognition method (applies its defaults through the signal)."""
        index = self._ocr_engine.findData(engine)
        if index >= 0:
            self._ocr_engine.setCurrentIndex(index)

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
        if changed and self.engine() == ENGINE_OLLAMA:
            self._auto_fetch()

    def refresh(self) -> None:
        """Re-apply engine-dependent state (visibility, model list, warnings)."""
        self._on_engine_changed(self.engine())

    def on_packages_changed(self) -> None:
        """An add-on or model was installed: check the engine's package and the list again."""
        if self.engine() == ENGINE_OLLAMA:
            self._app_service.invalidate_model_cache("ollama", base_url=self._ollama_url)
        self.refresh()

    def set_hardware_profile(self, setting: str) -> None:
        """Follow another "My computer" choice ("auto", "cpu", "8gb", "16gb").

        Does nothing while the picker follows a profile selector it does not own, or when the
        choice is the one it has: the selector resolves the computer again on every change.
        """
        if self._shared_selector is None and setting != self._hw_selector.setting():
            self._hw_selector.set_setting(setting)

    def reload_models(self) -> None:
        """Ask the server for the model list again (for an Ollama choice), keeping the rest.

        For a picker whose fetch was cut short by :meth:`stop_threads` — its window was closed
        and is shown again.  Unlike :meth:`refresh` it leaves the chosen model and the
        preparation method alone.
        """
        self._auto_fetch()

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

    def _on_engine_index_changed(self, _index: int) -> None:
        self._on_engine_changed(self.engine())

    def _on_engine_changed(self, engine: str) -> None:
        self.engine_changed.emit(engine)

        is_ollama = engine == ENGINE_OLLAMA
        self._ocr_model_label.setVisible(is_ollama)
        self._ocr_model_row.setVisible(is_ollama)
        # Hint line and "Use best choice" button exist only for Ollama.
        self._guidance.setVisible(is_ollama)
        self._engine_hint.setText(txt.OCR_ENGINE_HINTS.get(engine, ""))

        # The built-in reader has to be downloaded once; say so and offer the download here.
        reader_missing = engine == ENGINE_PPOCR and not self._app_service.reader_installed()
        self._extra_warn.setText(txt.READER_NOT_DOWNLOADED if reader_missing else "")
        self._extra_warn.setVisible(reader_missing)
        self._install_btn.setVisible(reader_missing)

        self._auto_fetch()
        self._update_where_hint()

    def _update_where_hint(self) -> None:
        """Say where the chosen engine reads pages (and warn about cloud models)."""
        engine = self.engine()
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
        engine = self.engine()
        # A fetch still running belongs to an engine that may no longer be the chosen one.
        _retire_thread(self._fetch_thread)
        if engine == ENGINE_OLLAMA:
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
            _start_thread(self._fetch_thread)
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
        if self.engine() != ENGINE_OLLAMA:
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

    def _on_download_reader(self) -> None:
        from ._addon_installer_dialog import ReaderDownloadDialog  # noqa: PLC0415

        dlg = ReaderDownloadDialog(self._app_service, self.window())
        dlg.downloaded.connect(lambda: self.addon_installed.emit(READER_ADDON_ID))
        dlg.downloaded.connect(self.refresh)
        dlg.exec()


class ScanQualityControls(QWidget):
    """How a page is prepared before it is read: Automatic or Original page, PDF handling."""

    #: The user asked to preview the current image preparation.
    preprocess_preview_requested = Signal()
    #: The page models were downloaded from the line under the combo (the add-on id).
    addon_installed = Signal(str)

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
        for method in PREPROCESS_METHODS:
            self._preprocess.addItem(txt.PREPROCESS_LABELS[method], userData=method)
        self._preprocess.setToolTip(txt.PREPROCESS_TOOLTIP)
        self._preprocess.currentIndexChanged.connect(self._update_page_models_line)
        ph.addWidget(self._preprocess, stretch=1)
        if show_preview_button:
            preview_btn = QPushButton("Preview…")
            preview_btn.setFixedWidth(72)
            preview_btn.clicked.connect(self.preprocess_preview_requested.emit)
            ph.addWidget(preview_btn)
        form.addRow("Image preparation:", preprocess_row)

        hint = QLabel(txt.PREPROCESS_HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow(hint)

        # The page models are only ever downloaded on request; Automatic runs without them.
        self._page_models_row = QWidget()
        pm = QHBoxLayout(self._page_models_row)
        pm.setContentsMargins(0, 0, 0, 0)
        pm_label = QLabel(txt.PAGE_MODELS_MISSING_LINE)
        pm_label.setWordWrap(True)
        pm_label.setStyleSheet("color: #888; font-size: 11px;")
        pm.addWidget(pm_label, stretch=1)
        self._page_models_btn = QPushButton(txt.PAGE_MODELS_DOWNLOAD_BUTTON)
        self._page_models_btn.clicked.connect(self._on_download_page_models)
        pm.addWidget(self._page_models_btn)
        form.addRow(self._page_models_row)

        self._pdf_dpi = QSpinBox()
        self._pdf_dpi.setRange(72, 600)
        self._pdf_dpi.setSingleStep(72)
        self._pdf_dpi.setSuffix(" DPI")
        self._pdf_dpi.setToolTip(
            "Resolution for rendering PDF pages to images before OCR.\n"
            "72 = screen quality, 150 = draft, 300 = recommended, 600 = high quality.\n"
            "Has no effect on image inputs (JPG, PNG)."
        )
        if self._pdf_options_inline:
            dpi_row = QWidget()
            dr = QHBoxLayout(dpi_row)
            dr.setContentsMargins(0, 0, 0, 0)
            dr.addWidget(QLabel("PDF:"))
            dr.addWidget(self._pdf_dpi)
            dr.addStretch()
            form.addRow("PDF resolution:", dpi_row)
        self._update_page_models_line()

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

    def preprocess_method(self) -> str:
        """Return the chosen image preparation (``auto`` or ``none``)."""
        return str(self._preprocess.currentData())

    def set_preprocess_method(self, method: str) -> None:
        """Choose the image preparation."""
        index = self._preprocess.findData(method)
        if index >= 0:
            self._preprocess.setCurrentIndex(index)

    def refresh_page_models(self) -> None:
        """Show or hide the download line again (the page models may have been downloaded)."""
        self._update_page_models_line()

    def showEvent(self, event: object) -> None:
        """Look again whether the page models are there: the Add-ons page may have fetched them."""
        self._update_page_models_line()
        super().showEvent(event)  # ty: ignore[invalid-argument-type]  # typed object to match the Qt override

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
        self._pdf_dpi.setValue(ocr.pdf_render_dpi)
        if self._pdf_text_layer is not None:
            _set_combo(self._pdf_text_layer, ocr.pdf_text_layer)
        self._update_page_models_line()

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
        )

    # ── internal ────────────────────────────────────────────────────────────

    def _update_page_models_line(self, *_args: object) -> None:
        """Show the download line only under Automatic while the page models are missing."""
        missing = (
            self.preprocess_method() == "auto" and not self._app_service.page_models_installed()
        )
        self._page_models_row.setVisible(missing)

    def _on_download_page_models(self) -> None:
        from ._addon_installer_dialog import PageModelsDownloadDialog  # noqa: PLC0415

        dlg = PageModelsDownloadDialog(self._app_service, self.window())
        dlg.downloaded.connect(self._on_page_models_downloaded)
        dlg.exec()

    def _on_page_models_downloaded(self) -> None:
        self._update_page_models_line()
        self.addon_installed.emit(PAGE_MODELS_ADDON_ID)


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

        self._scan.addon_installed.connect(self.addon_installed.emit)
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
        # Re-apply engine-dependent state first (it populates the model list), then override
        # with the saved values.
        self.refresh()
        self._picker.set_model(ocr.model)
        self._scan.load(ocr)

    def get_ocr_config(self) -> OcrConfig:
        """Return the currently selected configuration as an :class:`OcrConfig`."""
        return self._scan.ocr_config(self._picker.engine(), self._picker.model())

    def refresh(self) -> None:
        """Re-apply engine-dependent state (visibility, defaults, model fetch)."""
        self._picker.refresh()

    def set_hardware_profile(self, setting: str) -> None:
        """Follow another "My computer" choice."""
        self._picker.set_hardware_profile(setting)

    def reload_models(self) -> None:
        """Ask the server for the model list again, keeping the chosen model."""
        self._picker.reload_models()

    def stop_threads(self) -> None:
        """Detach any in-flight model-fetch thread (call before closing)."""
        self._picker.stop_threads()
