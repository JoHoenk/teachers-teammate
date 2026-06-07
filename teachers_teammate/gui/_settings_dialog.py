"""Settings dialogs for OCR, correction, evaluation, connections, and output options."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import sys
from typing import Protocol

from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..application.service import ProcessingApplicationService
from ..config import DEFAULTS
from ._worker import _ConnectionCheckThread  # noqa: F401  kept for external callers


def _install_hint(service: _SettingsAppService, extra: str, packages: list[str]) -> str:
    """Return a warning string when *packages* are not importable, else empty string."""
    if all(service.is_module_importable(p) for p in packages):
        return ""
    if getattr(sys, "frozen", False):
        return "⚠ Not included in this build — download the full-featured release."
    return f'⚠ Not installed — run:  pip install "teachers-teammate[{extra}]"'


_PROVIDER_EXTRA: dict[str, tuple[str, list[str]]] = {
    "mistral": ("providers", ["langchain_mistralai"]),
    "cohere": ("providers", ["langchain_cohere"]),
}

# Engines that map directly to a pipeline stage without a LangChain provider wrapper.
_NATIVE_OCR_ENGINES: frozenset[str] = frozenset({"ollama", "tesseract", "paddleocr"})


# ── background threads ────────────────────────────────────────────────────────


class _SettingsAppService(Protocol):
    def list_providers(self) -> list[str]: ...

    def list_ocr_engines(self) -> list[str]: ...

    def default_preprocess_for_engine(self, engine: str) -> str: ...

    def get_provider_info(self, provider: str) -> dict: ...

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]: ...

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None: ...

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None: ...

    def is_module_importable(self, module: str) -> bool: ...


class _ModelFetchThread(QThread):
    """Fetches model names in a background thread via a caller-supplied callable."""

    models_ready = Signal(list)
    error = Signal(str)

    def __init__(self, fetch_fn: Callable[[], list[str]]) -> None:
        super().__init__()
        self._fetch_fn = fetch_fn

    def run(self) -> None:
        try:
            self.models_ready.emit(self._fetch_fn())
        except Exception as exc:  # noqa: BLE001  # provider model fetch may raise any network/auth error; report via signals
            self.error.emit(str(exc))
            self.models_ready.emit([])


class _GpuDetectThread(QThread):
    """Runs GPU detection (nvidia-smi / rocm-smi) in a background thread."""

    detect_done = Signal(list)  # list[GpuInfo]

    def run(self) -> None:
        self.detect_done.emit(ProcessingApplicationService().detect_gpus())


# ── status label helpers ───────────────────────────────────────────────────────

_STATUS_LOADING = '<span style="color:#888;">● loading…</span>'
_STATUS_KEY_MISSING = '<span style="color:#e67e22;">● key not set</span>'
_STATUS_ERROR = '<span style="color:#c0392b;">● -</span>'


def _status_ok(n: int) -> str:
    return f'<span style="color:#27ae60;">● {n} model{"s" if n != 1 else ""}</span>'


# ── module-level helpers ──────────────────────────────────────────────────────


def _set_combo(combo: QComboBox, value: str) -> None:
    idx = combo.findText(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)


def _available_providers(app_service: _SettingsAppService) -> list[str]:
    """Return providers that need no API key or already have one configured."""
    result = []
    for p in app_service.list_providers():
        info = app_service.get_provider_info(p)
        env_key: str = info.get("env_key", "")
        if not env_key or os.environ.get(env_key):
            result.append(p)
    return result


def _stop_thread(thread: _ModelFetchThread | None) -> None:
    if thread is not None and thread.isRunning():
        thread.quit()
        thread.wait(200)


# ── OCR Settings dialog ───────────────────────────────────────────────────────


class OCRSettingsDialog(QDialog):
    """Focused dialog for configuring OCR engine, model, and preprocessing."""

    preprocess_preview_requested = Signal(str)
    addon_installed = Signal(str)

    def __init__(
        self,
        values: dict,
        parent: QWidget | None = None,
        *,
        app_service: _SettingsAppService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Text Recognition Settings")
        self.setMinimumWidth(480)
        screen = QApplication.primaryScreen()
        if screen:
            self.setMaximumHeight(screen.availableGeometry().height() - 60)

        self._app_service = app_service or ProcessingApplicationService()
        self._ocr_fetch_thread: _ModelFetchThread | None = None
        self._ollama_url = str(values.get("ollama_url", DEFAULTS["ollama_url"]))

        main = QVBoxLayout(self)
        main.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setSpacing(12)

        layout.addWidget(self._build_method_group())
        layout.addStretch(1)
        main.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)

        self._load_values(values)
        self._on_engine_changed(self._ocr_engine.currentText())

    def _build_method_group(self) -> QGroupBox:  # noqa: PLR0915
        """Build the 'Text Recognition Method' settings group."""
        group = QGroupBox("Text Recognition Method")
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._ocr_engine = QComboBox()
        _native = ["ollama", "tesseract", "paddleocr"]
        _provider_engines = [
            p for p in _available_providers(self._app_service) if p not in _NATIVE_OCR_ENGINES
        ]
        self._ocr_engine.addItems(_native + _provider_engines)
        self._ocr_engine.currentTextChanged.connect(self._on_engine_changed)
        form.addRow("Recognition method:", self._ocr_engine)

        self._ocr_extra_warn = QLabel()
        self._ocr_extra_warn.setStyleSheet("color: #c0392b;")
        self._ocr_extra_warn.setWordWrap(True)
        self._ocr_extra_warn.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._ocr_extra_warn.setVisible(False)
        form.addRow("", self._ocr_extra_warn)

        self._ocr_install_btn = QPushButton("Install PaddleOCR Addon…")
        self._ocr_install_btn.setVisible(False)
        self._ocr_install_btn.clicked.connect(self._on_install_paddle_addon)
        form.addRow("", self._ocr_install_btn)

        self._ocr_model_label = QLabel("Model:")
        self._ocr_model_row = QWidget()
        ml = QHBoxLayout(self._ocr_model_row)
        ml.setContentsMargins(0, 0, 0, 0)
        self._ocr_model = QComboBox()
        self._ocr_model.setEditable(True)
        self._ocr_model.addItem(DEFAULTS["ocr_model"])
        self._ocr_model.setCurrentText(DEFAULTS["ocr_model"])
        self._ocr_status_lbl = QLabel(_STATUS_LOADING)
        self._ocr_status_lbl.setTextFormat(Qt.TextFormat.RichText)
        ml.addWidget(self._ocr_model, stretch=1)
        ml.addWidget(self._ocr_status_lbl)
        form.addRow(self._ocr_model_label, self._ocr_model_row)

        preprocess_row = QWidget()
        ph = QHBoxLayout(preprocess_row)
        ph.setContentsMargins(0, 0, 0, 0)
        self._preprocess = QComboBox()
        self._preprocess.addItems(["adaptive_threshold", "clahe", "grayscale", "none"])
        self._preprocess.setToolTip(
            "Image preparation applied before text recognition.\n\n"
            "Enhanced contrast — sharpens text on white background;\n"
            "  best for Ollama with handwriting.\n"
            "Balanced contrast — improves faint or low-contrast text;\n"
            "  best for Tesseract.\n"
            "Grayscale — converts to black and white without extra processing.\n"
            "None — use the original image as-is."
        )
        self._preview_btn = QPushButton("Preview…")
        self._preview_btn.setFixedWidth(72)
        self._preview_btn.setToolTip(
            "See a side-by-side comparison of the original and prepared image."
        )
        self._preview_btn.clicked.connect(
            lambda: self.preprocess_preview_requested.emit(self._preprocess.currentText())
        )
        ph.addWidget(self._preprocess, stretch=1)
        ph.addWidget(self._preview_btn)
        form.addRow("Image preparation:", preprocess_row)

        self._debug = QCheckBox("Keep processed images for inspection")
        self._debug.setToolTip(
            "When enabled, the processed images used for text recognition are\n"
            "kept in a temporary folder after the run."
        )
        form.addRow("", self._debug)

        self._ocr_temperature = QDoubleSpinBox()
        self._ocr_temperature.setRange(0.0, 2.0)
        self._ocr_temperature.setSingleStep(0.1)
        self._ocr_temperature.setDecimals(1)
        self._ocr_temperature.setValue(DEFAULTS["ocr_temperature"])
        self._ocr_temperature.setToolTip(
            "Sampling temperature for the AI model (0.0 = deterministic, higher = more creative).\n"
            "For OCR, keep at 0.0 for consistent, repeatable results."
        )
        form.addRow("Temperature:", self._ocr_temperature)
        return group

    def _load_values(self, values: dict) -> None:
        if "ocr_engine" in values:
            engine = str(values["ocr_engine"])
            if engine == "langchain" and "ocr_provider" in values:
                _set_combo(self._ocr_engine, str(values["ocr_provider"]))
            else:
                _set_combo(self._ocr_engine, engine)
        if "ocr_model" in values:
            self._ocr_model.setCurrentText(str(values["ocr_model"]))
        if "preprocess_method" in values:
            _set_combo(self._preprocess, str(values["preprocess_method"]))
        if "debug" in values:
            self._debug.setChecked(bool(values["debug"]))
        if "ocr_temperature" in values:
            self._ocr_temperature.setValue(float(values["ocr_temperature"]))

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
        self._ocr_extra_warn.setText(warn)
        self._ocr_extra_warn.setVisible(bool(warn))
        self._ocr_install_btn.setVisible(paddle_missing and frozen)

        if engine not in _NATIVE_OCR_ENGINES:
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
            self._ocr_status_lbl.setText(_STATUS_LOADING)
            _stop_thread(self._ocr_fetch_thread)
            url = self._ollama_url
            self._ocr_fetch_thread = _ModelFetchThread(
                lambda: self._app_service.list_provider_models("ollama", base_url=url)
            )
            self._ocr_fetch_thread.models_ready.connect(self._on_models_fetched)
            self._ocr_fetch_thread.start()
        elif engine not in _NATIVE_OCR_ENGINES:
            info = self._app_service.get_provider_info(engine)
            if info.get("needs_api_key") and not os.environ.get(info.get("env_key", "")):
                self._ocr_status_lbl.setText(_STATUS_KEY_MISSING)
                return
            cached = self._app_service.get_cached_models(engine)
            if cached is not None:
                self._on_models_fetched(cached)
                return
            self._ocr_status_lbl.setText(_STATUS_LOADING)
            _stop_thread(self._ocr_fetch_thread)
            self._ocr_fetch_thread = _ModelFetchThread(
                lambda p=engine: self._app_service.list_provider_models(p)
            )
            self._ocr_fetch_thread.models_ready.connect(self._on_models_fetched)
            self._ocr_fetch_thread.start()
        else:
            self._ocr_status_lbl.setText("")

    def _on_models_fetched(self, models: list) -> None:
        if not models:
            self._ocr_status_lbl.setText(_STATUS_ERROR)
            return
        self._ocr_status_lbl.setText(_status_ok(len(models)))
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

    def get_values(self) -> dict:
        """Return OCR-specific settings for ConfigPanel.update_settings()."""
        engine = self._ocr_engine.currentText()
        if engine in _NATIVE_OCR_ENGINES:
            ocr_engine, ocr_provider = engine, ""
        else:
            ocr_engine, ocr_provider = "langchain", engine
        return {
            "ocr_engine": ocr_engine,
            "ocr_provider": ocr_provider,
            "ocr_model": self._ocr_model.currentText().strip() or DEFAULTS["ocr_model"],
            "preprocess_method": self._preprocess.currentText(),
            "debug": self._debug.isChecked(),
            "ocr_temperature": self._ocr_temperature.value(),
        }


# ── Correction Settings dialog ────────────────────────────────────────────────


class CorrectionSettingsDialog(QDialog):
    """Focused dialog for configuring the correction system prompt, provider, and model."""

    def __init__(
        self,
        values: dict,
        prompt: str,
        parent: QWidget | None = None,
        *,
        app_service: _SettingsAppService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Proofreading Settings")
        self.setMinimumWidth(480)
        screen = QApplication.primaryScreen()
        if screen:
            self.setMaximumHeight(screen.availableGeometry().height() - 60)

        self._app_service = app_service or ProcessingApplicationService()
        self._corr_fetch_thread: _ModelFetchThread | None = None
        self._ollama_url = str(values.get("ollama_url", DEFAULTS["ollama_url"]))

        main = QVBoxLayout(self)
        main.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setSpacing(12)

        layout.addWidget(self._build_prompt_group(prompt))
        layout.addWidget(self._build_model_group())
        layout.addStretch(1)
        main.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)

        # Seed model list for initial provider
        self._on_provider_changed(self._correction_provider.currentText())

        # Load saved values
        if "correction_provider" in values:
            _set_combo(self._correction_provider, str(values["correction_provider"]))
        if "correction_model" in values:
            self._correction_model.setCurrentText(str(values["correction_model"]))
        if "correction_temperature" in values:
            self._corr_temperature.setValue(float(values["correction_temperature"]))

        self._auto_fetch()

    def _build_prompt_group(self, prompt: str) -> QGroupBox:
        """Build the proofreading-instructions (system prompt) group."""
        prompt_group = QGroupBox("Proofreading Instructions")
        prompt_layout = QVBoxLayout(prompt_group)
        prompt_note = QLabel(
            "Leave empty to use the built-in default instructions for the selected language."
        )
        prompt_note.setWordWrap(True)
        prompt_note.setStyleSheet("color: #888; font-size: 10px;")
        prompt_layout.addWidget(prompt_note)
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlainText(prompt)
        self._prompt_edit.setPlaceholderText(
            "Leave empty to use the built-in default proofreading instructions."
        )
        self._prompt_edit.setMinimumHeight(120)
        prompt_layout.addWidget(self._prompt_edit)
        return prompt_group

    def _build_model_group(self) -> QGroupBox:
        """Build the AI-service (provider + model) group."""
        model_group = QGroupBox("AI Service")
        model_form = QFormLayout(model_group)
        model_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._correction_provider = QComboBox()
        self._correction_provider.addItems(_available_providers(self._app_service))
        self._correction_provider.currentTextChanged.connect(self._on_provider_changed)
        model_form.addRow("AI service:", self._correction_provider)

        self._correction_extra_warn = QLabel()
        self._correction_extra_warn.setStyleSheet("color: #c0392b;")
        self._correction_extra_warn.setWordWrap(True)
        self._correction_extra_warn.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._correction_extra_warn.setVisible(False)
        model_form.addRow("", self._correction_extra_warn)

        self._correction_model_row = QWidget()
        cl = QHBoxLayout(self._correction_model_row)
        cl.setContentsMargins(0, 0, 0, 0)
        self._correction_model = QComboBox()
        self._correction_model.setEditable(True)
        self._corr_status_lbl = QLabel(_STATUS_LOADING)
        self._corr_status_lbl.setTextFormat(Qt.TextFormat.RichText)
        cl.addWidget(self._correction_model, stretch=1)
        cl.addWidget(self._corr_status_lbl)
        model_form.addRow("AI model:", self._correction_model_row)

        self._corr_temperature = QDoubleSpinBox()
        self._corr_temperature.setRange(0.0, 2.0)
        self._corr_temperature.setSingleStep(0.1)
        self._corr_temperature.setDecimals(1)
        self._corr_temperature.setValue(DEFAULTS["correction_temperature"])
        self._corr_temperature.setToolTip(
            "Sampling temperature for the proofreading model\n"
            "(0.0 = deterministic, higher = more creative)."
        )
        model_form.addRow("Temperature:", self._corr_temperature)
        return model_group

    def _on_provider_changed(self, provider: str) -> None:
        info = self._app_service.get_provider_info(provider)
        prev = self._correction_model.currentText()
        self._correction_model.clear()
        models = info.get("models", [])
        if models:
            self._correction_model.addItems(models)
        idx = self._correction_model.findText(prev)
        if idx >= 0:
            self._correction_model.setCurrentIndex(idx)
        elif prev:
            self._correction_model.setCurrentText(prev)
        else:
            self._correction_model.setCurrentText(info.get("default_model", ""))
        self._app_service.invalidate_model_cache(
            provider, self._ollama_url if provider == "ollama" else ""
        )

        if provider in _PROVIDER_EXTRA:
            _extra, _pkgs = _PROVIDER_EXTRA[provider]
            warn = _install_hint(self._app_service, _extra, _pkgs)
        else:
            warn = ""
        self._correction_extra_warn.setText(warn)
        self._correction_extra_warn.setVisible(bool(warn))

    def _auto_fetch(self) -> None:
        provider = self._correction_provider.currentText()
        info = self._app_service.get_provider_info(provider)
        if info.get("needs_api_key") and not os.environ.get(info.get("env_key", "")):
            self._corr_status_lbl.setText(_STATUS_KEY_MISSING)
            return
        url = self._ollama_url if provider == "ollama" else ""
        cached = self._app_service.get_cached_models(provider, base_url=url)
        if cached is not None:
            self._on_models_fetched(cached)
            return
        self._corr_status_lbl.setText(_STATUS_LOADING)
        _stop_thread(self._corr_fetch_thread)
        self._corr_fetch_thread = _ModelFetchThread(
            lambda p=provider, u=url: self._app_service.list_provider_models(p, base_url=u)
        )
        self._corr_fetch_thread.models_ready.connect(self._on_models_fetched)
        self._corr_fetch_thread.start()

    def _on_models_fetched(self, models: list) -> None:
        if not models:
            self._corr_status_lbl.setText(_STATUS_ERROR)
            return
        self._corr_status_lbl.setText(_status_ok(len(models)))
        prev = self._correction_model.currentText()
        self._correction_model.clear()
        self._correction_model.addItems(models)
        idx = self._correction_model.findText(prev)
        if idx >= 0:
            self._correction_model.setCurrentIndex(idx)
        else:
            self._correction_model.setCurrentText(prev)

    def get_prompt(self) -> str:
        """Return the edited system prompt text."""
        return self._prompt_edit.toPlainText().strip()

    def get_values(self) -> dict:
        """Return correction-specific settings for ConfigPanel.update_settings()."""
        model = self._correction_model.currentText().strip()
        return {
            "correction_provider": self._correction_provider.currentText(),
            "correction_model": model,
            "correction_temperature": self._corr_temperature.value(),
        }


# ── Evaluation Settings dialog ────────────────────────────────────────────────


class EvaluationSettingsDialog(QDialog):
    """Focused dialog for configuring the evaluation system prompt, provider, and model."""

    def __init__(
        self,
        values: dict,
        prompt: str,
        parent: QWidget | None = None,
        *,
        app_service: _SettingsAppService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Content Review Settings")
        self.setMinimumWidth(480)
        screen = QApplication.primaryScreen()
        if screen:
            self.setMaximumHeight(screen.availableGeometry().height() - 60)

        self._app_service = app_service or ProcessingApplicationService()
        self._eval_fetch_thread: _ModelFetchThread | None = None
        self._ollama_url = str(values.get("ollama_url", DEFAULTS["ollama_url"]))

        main = QVBoxLayout(self)
        main.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setSpacing(12)

        layout.addWidget(self._build_prompt_group(prompt))
        layout.addWidget(self._build_model_group())
        layout.addStretch(1)
        main.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)

        # Seed model list for initial provider
        self._on_provider_changed(self._evaluation_provider.currentText())

        # Load saved values
        if "evaluate_provider" in values:
            _set_combo(self._evaluation_provider, str(values["evaluate_provider"]))
        if "evaluate_model" in values:
            self._evaluation_model.setCurrentText(str(values["evaluate_model"]))
        if "evaluate_temperature" in values:
            self._eval_temperature.setValue(float(values["evaluate_temperature"]))

        self._auto_fetch()

    def _build_prompt_group(self, prompt: str) -> QGroupBox:
        """Build the content-review-instructions (system prompt) group."""
        prompt_group = QGroupBox("Content Review Instructions")
        prompt_layout = QVBoxLayout(prompt_group)
        prompt_note = QLabel("Leave empty to use the built-in default content review instructions.")
        prompt_note.setWordWrap(True)
        prompt_note.setStyleSheet("color: #888; font-size: 10px;")
        prompt_layout.addWidget(prompt_note)
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlainText(prompt)
        self._prompt_edit.setPlaceholderText(
            "Leave empty to use the built-in default content review instructions."
        )
        self._prompt_edit.setMinimumHeight(120)
        prompt_layout.addWidget(self._prompt_edit)
        return prompt_group

    def _build_model_group(self) -> QGroupBox:
        """Build the AI-service (provider + model) group."""
        model_group = QGroupBox("AI Service")
        model_form = QFormLayout(model_group)
        model_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._evaluation_provider = QComboBox()
        self._evaluation_provider.addItems(_available_providers(self._app_service))
        self._evaluation_provider.currentTextChanged.connect(self._on_provider_changed)
        model_form.addRow("AI service:", self._evaluation_provider)

        self._eval_extra_warn = QLabel()
        self._eval_extra_warn.setStyleSheet("color: #c0392b;")
        self._eval_extra_warn.setWordWrap(True)
        self._eval_extra_warn.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._eval_extra_warn.setVisible(False)
        model_form.addRow("", self._eval_extra_warn)

        self._evaluation_model_row = QWidget()
        el = QHBoxLayout(self._evaluation_model_row)
        el.setContentsMargins(0, 0, 0, 0)
        self._evaluation_model = QComboBox()
        self._evaluation_model.setEditable(True)
        self._eval_status_lbl = QLabel(_STATUS_LOADING)
        self._eval_status_lbl.setTextFormat(Qt.TextFormat.RichText)
        el.addWidget(self._evaluation_model, stretch=1)
        el.addWidget(self._eval_status_lbl)
        model_form.addRow("AI model:", self._evaluation_model_row)

        self._eval_temperature = QDoubleSpinBox()
        self._eval_temperature.setRange(0.0, 2.0)
        self._eval_temperature.setSingleStep(0.1)
        self._eval_temperature.setDecimals(1)
        self._eval_temperature.setValue(DEFAULTS["evaluate_temperature"])
        self._eval_temperature.setToolTip(
            "Sampling temperature for the content review model\n"
            "(0.0 = deterministic, higher = more creative)."
        )
        model_form.addRow("Temperature:", self._eval_temperature)
        return model_group

    def _on_provider_changed(self, provider: str) -> None:
        info = self._app_service.get_provider_info(provider)
        prev = self._evaluation_model.currentText()
        self._evaluation_model.clear()
        models = info.get("models", [])
        if models:
            self._evaluation_model.addItems(models)
        idx = self._evaluation_model.findText(prev)
        if idx >= 0:
            self._evaluation_model.setCurrentIndex(idx)
        elif prev:
            self._evaluation_model.setCurrentText(prev)
        else:
            self._evaluation_model.setCurrentText(info.get("default_model", ""))
        self._app_service.invalidate_model_cache(
            provider, self._ollama_url if provider == "ollama" else ""
        )

        if provider in _PROVIDER_EXTRA:
            _extra, _pkgs = _PROVIDER_EXTRA[provider]
            warn = _install_hint(self._app_service, _extra, _pkgs)
        else:
            warn = ""
        self._eval_extra_warn.setText(warn)
        self._eval_extra_warn.setVisible(bool(warn))

    def _auto_fetch(self) -> None:
        provider = self._evaluation_provider.currentText()
        info = self._app_service.get_provider_info(provider)
        if info.get("needs_api_key") and not os.environ.get(info.get("env_key", "")):
            self._eval_status_lbl.setText(_STATUS_KEY_MISSING)
            return
        url = self._ollama_url if provider == "ollama" else ""
        cached = self._app_service.get_cached_models(provider, base_url=url)
        if cached is not None:
            self._on_models_fetched(cached)
            return
        self._eval_status_lbl.setText(_STATUS_LOADING)
        _stop_thread(self._eval_fetch_thread)
        self._eval_fetch_thread = _ModelFetchThread(
            lambda p=provider, u=url: self._app_service.list_provider_models(p, base_url=u)
        )
        self._eval_fetch_thread.models_ready.connect(self._on_models_fetched)
        self._eval_fetch_thread.start()

    def _on_models_fetched(self, models: list) -> None:
        if not models:
            self._eval_status_lbl.setText(_STATUS_ERROR)
            return
        self._eval_status_lbl.setText(_status_ok(len(models)))
        prev = self._evaluation_model.currentText()
        self._evaluation_model.clear()
        self._evaluation_model.addItems(models)
        idx = self._evaluation_model.findText(prev)
        if idx >= 0:
            self._evaluation_model.setCurrentIndex(idx)
        else:
            self._evaluation_model.setCurrentText(prev)

    def get_prompt(self) -> str:
        """Return the edited system prompt text."""
        return self._prompt_edit.toPlainText().strip()

    def get_values(self) -> dict:
        """Return evaluation-specific settings for ConfigPanel.update_settings()."""
        model = self._evaluation_model.currentText().strip()
        return {
            "evaluate_provider": self._evaluation_provider.currentText(),
            "evaluate_model": model,
            "evaluate_temperature": self._eval_temperature.value(),
        }


# ── Connections & Credentials dialog ──────────────────────────────────────────


class SettingsDialog(QDialog):
    """Modal dialog for configuring Ollama connection, hardware, and provider API keys.

    OCR, correction, and evaluation settings live in their own focused dialogs
    (OCRSettingsDialog, CorrectionSettingsDialog, EvaluationSettingsDialog).
    """

    addon_installed = Signal(str)
    open_downloads_requested = Signal()

    def __init__(
        self,
        values: dict,
        parent: QWidget | None = None,
        *,
        input_dir: str = "",
        app_service: _SettingsAppService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connections & API Keys")
        self.setMinimumWidth(520)
        screen = QApplication.primaryScreen()
        if screen:
            self.setMaximumHeight(screen.availableGeometry().height() - 60)

        self._gpu_detect_thread: _GpuDetectThread | None = None
        self._detected_gpus: list = []
        self._input_dir = input_dir
        self._app_service = app_service or ProcessingApplicationService()

        main = QVBoxLayout(self)
        main.setSpacing(8)

        conn_content = QWidget()
        conn_layout = QVBoxLayout(conn_content)
        conn_layout.setSpacing(12)

        conn_layout.addWidget(self._build_ollama_group())
        conn_layout.addWidget(self._build_performance_group())
        cred_group = self._build_credentials_group()
        self._load_credentials_rows()
        conn_layout.addWidget(cred_group)
        conn_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(conn_content)
        main.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)

        if "ollama_url" in values:
            self._ollama_url.setText(str(values["ollama_url"]))

    def _build_ollama_group(self) -> QGroupBox:
        """Build the Ollama server-URL group."""
        ollama_group = QGroupBox("Ollama")
        ollama_form = QFormLayout(ollama_group)
        ollama_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._ollama_url_label = QLabel("Server URL:")
        self._ollama_url = QLineEdit(DEFAULTS["ollama_url"])
        self._ollama_url.setToolTip(
            "The address of your Ollama server. Used for all text recognition,\n"
            "proofreading, and content review steps that use Ollama."
        )
        ollama_form.addRow(self._ollama_url_label, self._ollama_url)

        self._pull_model_btn = QPushButton("Pull Model…")
        self._pull_model_btn.setFixedWidth(120)
        self._pull_model_btn.setToolTip(
            "Opens the Downloads dialog to download an Ollama AI model."
        )
        self._pull_model_btn.clicked.connect(self._on_pull_model)
        ollama_form.addRow("", self._pull_model_btn)
        return ollama_group

    def _build_performance_group(self) -> QGroupBox:
        """Build the GPU-detection / performance group."""
        hw_group = QGroupBox("Performance")
        hw_form = QFormLayout(hw_group)
        hw_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._gpu_detect_btn = QPushButton("Detect GPU")
        self._gpu_detect_btn.setFixedWidth(120)
        self._gpu_detect_btn.setToolTip(
            "Check whether a graphics card is available —\n"
            "this can significantly speed up text recognition."
        )
        self._gpu_detect_btn.clicked.connect(self._on_detect_gpu)
        self._gpu_status_lbl = QLabel("—")
        self._gpu_status_lbl.setWordWrap(True)
        gpu_detect_row = QWidget()
        gdr = QHBoxLayout(gpu_detect_row)
        gdr.setContentsMargins(0, 0, 0, 0)
        gdr.addWidget(self._gpu_detect_btn)
        gdr.addWidget(self._gpu_status_lbl, stretch=1)
        hw_form.addRow("GPU:", gpu_detect_row)

        self._gpu_install_btn = QPushButton("Install GPU Addon…")
        self._gpu_install_btn.setVisible(False)
        self._gpu_install_btn.clicked.connect(self._on_install_gpu_addon)
        hw_form.addRow("", self._gpu_install_btn)
        return hw_group

    def _build_credentials_group(self) -> QGroupBox:
        """Build the provider API-keys table group (rows are loaded separately)."""
        cred_group = QGroupBox("Provider API Keys")
        cred_vbox = QVBoxLayout(cred_group)

        cred_note = QLabel(
            "Enter your API keys for AI services. These are saved securely and loaded "
            "automatically when you start the app. A key you set manually before launching "
            "takes priority."
        )
        cred_note.setWordWrap(True)
        cred_note.setStyleSheet("color: #555; font-size: 11px;")
        cred_vbox.addWidget(cred_note)

        self._cred_table = QTableWidget(0, 3)
        self._cred_table.setHorizontalHeaderLabels(["Provider", "API Key", ""])
        self._cred_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._cred_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._cred_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._cred_table.verticalHeader().setVisible(False)
        self._cred_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._cred_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._cred_table.setMinimumHeight(100)
        self._cred_table.setMaximumHeight(200)
        cred_vbox.addWidget(self._cred_table)

        cred_btn_row = QWidget()
        cred_btn_layout = QHBoxLayout(cred_btn_row)
        cred_btn_layout.setContentsMargins(0, 0, 0, 0)
        self._cred_add_btn = QPushButton("Add")
        self._cred_add_btn.setFixedWidth(72)
        self._cred_add_btn.clicked.connect(self._add_new_cred_row)
        cred_btn_layout.addWidget(self._cred_add_btn)
        cred_btn_layout.addStretch(1)
        cred_vbox.addWidget(cred_btn_row)
        return cred_group

    # ── credentials table ──────────────────────────────────────────────────────

    def _load_credentials_rows(self) -> None:
        qs = QSettings("TeachersTeammate", "TeachersTeammate")
        qs.beginGroup("credentials")
        stored_keys = set(qs.childKeys())
        qs.endGroup()
        for p in self._app_service.list_providers():
            info = self._app_service.get_provider_info(p)
            env_key: str = info.get("env_key", "")
            if not env_key:
                continue
            value = os.environ.get(env_key, "")
            if not value:
                qs2 = QSettings("TeachersTeammate", "TeachersTeammate")
                value = str(qs2.value(f"credentials/{env_key}", ""))
            if value or env_key in stored_keys:
                self._add_cred_row(p, env_key, value)

    def _used_env_keys(self) -> set[str]:
        keys: set[str] = set()
        for row in range(self._cred_table.rowCount()):
            combo = self._cred_table.cellWidget(row, 0)
            if isinstance(combo, QComboBox):
                data = combo.currentData()
                if isinstance(data, tuple) and len(data) == 2:
                    keys.add(data[1])
        return keys

    def _add_cred_row(self, provider: str, env_key: str, value: str = "") -> None:
        row = self._cred_table.rowCount()
        self._cred_table.insertRow(row)

        combo = QComboBox()
        combo.addItem(f"{provider}  ({env_key})", userData=(provider, env_key))
        combo.setEnabled(False)
        self._cred_table.setCellWidget(row, 0, combo)

        field = QLineEdit(value)
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText("(not set)")
        self._cred_table.setCellWidget(row, 1, field)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(
            lambda _=False, btn=remove_btn: self._remove_cred_row_by_widget(btn)
        )
        self._cred_table.setCellWidget(row, 2, remove_btn)
        self._cred_table.setRowHeight(row, 32)

    def _add_new_cred_row(self) -> None:
        used = self._used_env_keys()
        available: list[tuple[str, str]] = []
        for p in self._app_service.list_providers():
            info = self._app_service.get_provider_info(p)
            env_key: str = info.get("env_key", "")
            if env_key and env_key not in used:
                available.append((p, env_key))
        if not available:
            QMessageBox.information(self, "No more providers", "All providers already have keys.")
            return

        row = self._cred_table.rowCount()
        self._cred_table.insertRow(row)

        combo = QComboBox()
        for p, env_key in available:
            combo.addItem(f"{p}  ({env_key})", userData=(p, env_key))
        self._cred_table.setCellWidget(row, 0, combo)

        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText("Enter API key…")
        self._cred_table.setCellWidget(row, 1, field)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(
            lambda _=False, btn=remove_btn: self._remove_cred_row_by_widget(btn)
        )
        self._cred_table.setCellWidget(row, 2, remove_btn)
        self._cred_table.setRowHeight(row, 32)
        field.setFocus()

    def _remove_cred_row_by_widget(self, widget: QPushButton) -> None:
        for row in range(self._cred_table.rowCount()):
            if self._cred_table.cellWidget(row, 2) == widget:
                self._cred_table.removeRow(row)
                return

    def _apply_credentials(self) -> None:
        qs = QSettings("TeachersTeammate", "TeachersTeammate")
        qs.beginGroup("credentials")
        existing_keys = set(qs.childKeys())
        qs.endGroup()

        new_creds: dict[str, str] = {}
        for row in range(self._cred_table.rowCount()):
            combo = self._cred_table.cellWidget(row, 0)
            if not isinstance(combo, QComboBox):
                continue
            data = combo.currentData()
            if not isinstance(data, tuple) or len(data) != 2:
                continue
            _, env_key = data
            field = self._cred_table.cellWidget(row, 1)
            if isinstance(field, QLineEdit):
                value = field.text().strip()
                if value:
                    new_creds[env_key] = value

        for env_key, value in new_creds.items():
            qs.setValue(f"credentials/{env_key}", value)
            os.environ[env_key] = value
        for env_key in existing_keys:
            if env_key not in new_creds:
                qs.remove(f"credentials/{env_key}")
                os.environ.pop(env_key, None)

    # ── hardware ───────────────────────────────────────────────────────────────

    def _on_pull_model(self) -> None:
        """Redirect to the Downloads dialog (Ollama Models tab)."""
        self.open_downloads_requested.emit()

    def _on_detect_gpu(self) -> None:
        self._gpu_detect_btn.setEnabled(False)
        self._gpu_status_lbl.setText("Detecting…")
        self._gpu_install_btn.setVisible(False)
        self._gpu_detect_thread = _GpuDetectThread()
        self._gpu_detect_thread.detect_done.connect(self._on_gpu_detected)
        self._gpu_detect_thread.finished.connect(lambda: self._gpu_detect_btn.setEnabled(True))
        self._gpu_detect_thread.start()

    def _on_gpu_detected(self, gpus: list) -> None:
        self._detected_gpus = gpus
        if not gpus:
            self._gpu_status_lbl.setText("No NVIDIA or AMD GPU detected.")
            self._gpu_install_btn.setVisible(False)
            return
        names = ", ".join(f"{g.name} ({g.vendor.upper()})" for g in gpus)
        self._gpu_status_lbl.setText(names)
        self._gpu_install_btn.setVisible(getattr(sys, "frozen", False))

    def _on_install_gpu_addon(self) -> None:
        from ._addon_installer_dialog import GpuAddonDialog  # noqa: PLC0415

        dlg = GpuAddonDialog(self._detected_gpus, self)
        dlg.installed.connect(self.addon_installed)
        dlg.exec()

    # ── public API ─────────────────────────────────────────────────────────────

    def accept(self) -> None:
        self._apply_credentials()
        super().accept()

    def get_values(self) -> dict:
        """Return connection settings for ConfigPanel.update_settings()."""
        return {
            "ollama_url": self._ollama_url.text().strip() or DEFAULTS["ollama_url"],
        }


# ── Output settings dialog ────────────────────────────────────────────────────


class OutputSettingsDialog(QDialog):
    """Modal dialog for configuring DOCX output options."""

    def __init__(self, values: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Output Settings")
        self.setMinimumWidth(340)

        main = QVBoxLayout(self)
        main.setSpacing(12)

        out_group = QGroupBox("Word Document Output")
        out_form = QFormLayout(out_group)
        out_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        output_row = QWidget()
        orl = QHBoxLayout(output_row)
        orl.setContentsMargins(0, 0, 0, 0)
        self._output_dir = QLineEdit()
        self._output_dir.setPlaceholderText("Leave blank to save next to your documents")
        self._output_dir.setToolTip("Folder where Word documents and text files will be saved.")
        output_browse = QPushButton("Browse…")
        output_browse.setFixedWidth(80)
        output_browse.clicked.connect(self._on_browse_output_dir)
        orl.addWidget(self._output_dir, stretch=1)
        orl.addWidget(output_browse)
        out_form.addRow("Save to folder:", output_row)

        self._docx_enabled = QCheckBox("Create Word documents")
        self._docx_enabled.setChecked(True)
        self._docx_enabled.setToolTip(
            "Generate a Word document for each input file containing the image,\n"
            "extracted text, and (if enabled) the proofread text."
        )
        self._docx_enabled.stateChanged.connect(self._update_visibility)
        out_form.addRow(self._docx_enabled)

        self._docx_format_label = QLabel("Document layout:")
        self._docx_format = QComboBox()
        self._docx_format.addItems(["table", "comments"])
        self._docx_format.setToolTip(
            "Table — side-by-side columns: Image | Extracted text | Proofread text.\n"
            "Comments — two columns with tracked changes shown as Word comments."
        )
        out_form.addRow(self._docx_format_label, self._docx_format)

        main.addWidget(out_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)

        if "output_dir" in values:
            self._output_dir.setText(str(values["output_dir"]))
        if "docx_enabled" in values:
            self._docx_enabled.setChecked(bool(values["docx_enabled"]))
        if "docx_format" in values:
            idx = self._docx_format.findText(str(values["docx_format"]))
            if idx >= 0:
                self._docx_format.setCurrentIndex(idx)
        self._update_visibility()

    def _on_browse_output_dir(self) -> None:
        start = self._output_dir.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Select output folder", start)
        if chosen:
            self._output_dir.setText(chosen)

    def _update_visibility(self) -> None:
        enabled = self._docx_enabled.isChecked()
        self._docx_format_label.setVisible(enabled)
        self._docx_format.setVisible(enabled)

    def get_values(self) -> dict:
        return {
            "output_dir": self._output_dir.text().strip(),
            "docx_enabled": self._docx_enabled.isChecked(),
            "docx_format": self._docx_format.currentText(),
        }
