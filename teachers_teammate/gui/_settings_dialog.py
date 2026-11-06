"""Settings dialogs for OCR, correction, evaluation, connections, and output options."""

from __future__ import annotations

import atexit
from collections.abc import Callable
import html
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
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..application.service import ProcessingApplicationService
from ..config import DEFAULTS, OcrConfig
from ..domain.data_location import StageLocation
from ..domain.formatted_text import TextFormat
from ..domain.model_catalog import (
    HardwareDetection,
    HardwareProfile,
    ModelCategory,
    ModelRating,
    ModelUses,
)
from ..domain.model_listing import ModelListing
from . import _user_text as txt
from ._constants import _LANGUAGES
from ._data_flow_summary import LocationHint, text_note
from ._model_guidance import PROVIDER_PREFIX, OpenDownloads
from ._model_wording import SAME_MODEL_TIP
from ._prompt_editor import PromptEditor
from ._worker import _ConnectionCheckThread  # noqa: F401  kept for external callers

PACKAGE_MISSING = "{name} support is not installed yet."
INSTALL_BUTTON = "Install…"
INSTALL_BUTTON_TOOLTIP = "Opens Downloads, where this package can be installed with one click."
PIP_HINT = "Or run:  pip install {pip}"


class _PackageHint(QWidget):
    """Says an AI service's package is missing and offers to install it from Downloads."""

    #: Emitted after Downloads closed, so the dialog can look again.
    downloads_closed = Signal()

    def __init__(self, open_downloads: OpenDownloads | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._open_downloads = open_downloads
        self._provider = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel()
        self._label.setStyleSheet("color: #c0392b;")
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._label, stretch=1)
        self._button = QPushButton(INSTALL_BUTTON)
        self._button.setToolTip(INSTALL_BUTTON_TOOLTIP)
        self._button.setVisible(open_downloads is not None)
        self._button.clicked.connect(self._on_install)
        layout.addWidget(self._button, alignment=Qt.AlignmentFlag.AlignTop)
        self.setVisible(False)

    def show_missing(self, provider: str, display_name: str, pip_name: str) -> None:
        """Show that *provider*'s package *pip_name* is missing."""
        self._provider = provider
        text = f"⚠ {PACKAGE_MISSING.format(name=display_name)}"
        if not getattr(sys, "frozen", False):
            text += f"\n{PIP_HINT.format(pip=pip_name)}"
        self._label.setText(text)
        self.setVisible(True)

    def clear(self) -> None:
        """Hide the hint (the package is installed, or the service needs none)."""
        self._provider = ""
        self._label.clear()
        self.setVisible(False)

    def message(self) -> str:
        """Return the shown text (empty while hidden)."""
        return self._label.text() if not self.isHidden() else ""

    def _on_install(self) -> None:
        if self._open_downloads is None or not self._provider:
            return
        self._open_downloads([f"{PROVIDER_PREFIX}{self._provider}"], self.window())
        self.downloads_closed.emit()


# ── background threads ────────────────────────────────────────────────────────


class _SettingsAppService(Protocol):
    def list_providers(self) -> list[str]: ...

    def list_ocr_engines(self) -> list[str]: ...

    def default_preprocess_for_engine(self, engine: str) -> str: ...

    def get_provider_info(self, provider: str) -> dict: ...

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]: ...

    def list_ocr_models(self, base_url: str = "") -> list[str]: ...

    def list_provider_models_checked(
        self, provider: str, *, base_url: str = ""
    ) -> ModelListing: ...

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None: ...

    def get_cached_listing(self, provider: str, *, base_url: str = "") -> ModelListing | None: ...

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None: ...

    def is_module_importable(self, module: str) -> bool: ...

    def missing_provider_package(self, provider: str) -> str | None: ...

    def list_correction_prompt_presets(self) -> dict[str, str]: ...

    def default_correction_prompt(self) -> str: ...

    def correction_instructions(
        self,
        *,
        prompt: str,
        language: str,
        level: str = "B2",
        output_mode: str = "review",
        text_format: TextFormat = "plain",
        answer_format: str = "",
    ) -> tuple[str, str]: ...

    def default_answer_formats(self) -> tuple[str, str]: ...

    def correction_notation_hint(self) -> str: ...

    def evaluation_fixed_instructions(self, text_format: TextFormat = "plain") -> str: ...

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection: ...

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection: ...

    def classify_ollama_models(
        self, tags: list[str], ollama_url: str, *, cached_only: bool = False
    ) -> dict[str, ModelUses | None]: ...

    def rate_ollama_models(
        self,
        tags: list[str],
        task: ModelCategory,
        profile: HardwareProfile,
        ollama_url: str,
        *,
        cached_only: bool = False,
        system_memory_gb: float | None = None,
    ) -> list[ModelRating]: ...

    def detect_gpus(self, *, refresh: bool = False) -> list: ...

    def describe_ocr_location(self, engine: str, model: str, ollama_url: str) -> StageLocation: ...

    def describe_ocr_refusal(self, engine: str, model: str, ollama_url: str) -> str: ...

    def describe_llm_location(
        self, provider: str, model: str, ollama_url: str
    ) -> StageLocation: ...


class _ModelFetchThread(QThread):
    """Fetches model names in a background thread via a caller-supplied callable.

    The callable returns the names, or a :class:`ModelListing` that also says whether they
    are live.  *tag* (the provider the fetch is for) comes back with the answer, so a late
    answer for a provider the user has already left can be told apart and dropped.
    """

    models_ready = Signal(list)
    listing_ready = Signal(str, object)  # (tag, ModelListing)
    error = Signal(str)

    def __init__(self, fetch_fn: Callable[[], list[str] | ModelListing], tag: str = "") -> None:
        super().__init__()
        self._fetch_fn = fetch_fn
        self._tag = tag

    def run(self) -> None:
        try:
            result = self._fetch_fn()
        except Exception as exc:  # noqa: BLE001  # provider model fetch may raise any network/auth error; report via signals
            self.error.emit(str(exc))
            self.models_ready.emit([])
            self.listing_ready.emit(self._tag, ModelListing([], "failed", str(exc)))
            return
        listing = result if isinstance(result, ModelListing) else ModelListing(list(result))
        self.models_ready.emit(listing.models)
        self.listing_ready.emit(self._tag, listing)


class _GpuDetectThread(QThread):
    """Runs GPU detection (nvidia-smi / rocm-smi) in a background thread."""

    detect_done = Signal(list)  # list[GpuInfo]

    def __init__(self, app_service: _SettingsAppService) -> None:
        super().__init__()
        self._app_service = app_service

    def run(self) -> None:
        # "Detect GPU" is a request to look again (a driver may have been installed
        # since the first probe); the fresh result also feeds "My computer".
        self.detect_done.emit(self._app_service.detect_gpus(refresh=True))


# ── status label helpers ───────────────────────────────────────────────────────

_STATUS_LOADING = '<span style="color:#888;">● loading…</span>'
_STATUS_KEY_MISSING = '<span style="color:#e67e22;">● key not set</span>'
_STATUS_ERROR = '<span style="color:#c0392b;">● -</span>'


def _status_ok(n: int) -> str:
    return f'<span style="color:#27ae60;">● {n} model{"s" if n != 1 else ""}</span>'


def _listing_status(listing: ModelListing) -> str:
    """Return the status line for *listing*: live, built-in by design, or built-in after a failure.

    A failed query must not look like a successful one, so it says why and offers to retry.
    """
    count = len(listing.models)
    noun = "model" if count == 1 else "models"
    if listing.source == "failed":
        what = f"built-in list of {count} {noun}" if count else "no models to show"
        return (
            f'<span style="color:#e67e22;">● {what} — '
            f"{html.escape(listing.problem or 'the service could not be asked')}</span> "
            '<a href="retry">Try again</a>'
        )
    if listing.source == "builtin":
        return f'<span style="color:#888;">● {count} {noun} · built-in list</span>'
    return _status_ok(count)


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


#: Threads that outlived their dialog.  A running ``QThread`` must not be destroyed
#: (that aborts the whole process) and these run a plain function with no event
#: loop, so they cannot be interrupted either: they are parked here until they end.
_RETIRED_THREADS: set[QThread] = set()
_THREAD_RESULT_SIGNALS = ("models_ready", "listing_ready", "error", "detect_done", "finished")
_RETIRED_WAIT_MS = 3000


def _purge_retired_threads() -> None:
    """Drop retired threads that have finished."""
    for thread in [t for t in _RETIRED_THREADS if t.isFinished()]:
        _RETIRED_THREADS.discard(thread)


def _retire_thread(thread: QThread | None) -> None:
    """Detach *thread* from its dialog without blocking the GUI thread.

    Its results are disconnected — a stale answer can no longer touch a widget —
    and a thread that is still running is kept alive until it ends on its own.
    """
    _purge_retired_threads()
    if thread is None or not thread.isRunning():
        return
    for name in _THREAD_RESULT_SIGNALS:
        signal = getattr(thread, name, None)
        if signal is None:
            continue
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            pass  # nothing was connected
    _RETIRED_THREADS.add(thread)


def _wait_for_retired_threads() -> None:
    """Give retired threads a moment to finish before the interpreter shuts down."""
    for thread in list(_RETIRED_THREADS):
        thread.wait(_RETIRED_WAIT_MS)


atexit.register(_wait_for_retired_threads)


# ── API key rows ──────────────────────────────────────────────────────────────

SHOW_KEY = "Show"
HIDE_KEY = "Hide"
KEY_NOT_SET = "(not set)"
KEY_PLACEHOLDER = "Paste your API key here"
REMOVE_KEY = "Remove"
ADD_KEY = "Add key"


class _CredentialRow(QWidget):
    """One API key: the service it belongs to, a field wide enough to paste into, Show/Remove.

    The name sits above the field instead of in a column beside it, so no column can be
    squeezed to a sliver and cut the name or the buttons.
    """

    remove_requested = Signal(object)

    def __init__(
        self,
        env_key: str,
        title: str,
        value: str = "",
        *,
        placeholder: str = KEY_NOT_SET,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.env_key = env_key
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setHorizontalSpacing(6)
        layout.setColumnStretch(0, 1)

        name = QLabel(
            f"<b>{html.escape(title)}</b> <span style='color:#888;'>({html.escape(env_key)})</span>"
        )
        name.setTextFormat(Qt.TextFormat.RichText)
        name.setWordWrap(True)
        layout.addWidget(name, 0, 0)

        self.field = QLineEdit(value)
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setPlaceholderText(placeholder)
        self.field.setMinimumWidth(220)
        layout.addWidget(self.field, 1, 0)

        self._reveal = QPushButton(SHOW_KEY)
        self._reveal.setCheckable(True)
        self._reveal.toggled.connect(self._on_reveal_toggled)
        layout.addWidget(self._reveal, 1, 1)

        remove = QPushButton(REMOVE_KEY)  # shares the column with Show, so both are as wide as it
        remove.clicked.connect(lambda _=False: self.remove_requested.emit(self))
        layout.addWidget(remove, 0, 1)

    def _on_reveal_toggled(self, shown: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        self.field.setEchoMode(mode)
        self._reveal.setText(HIDE_KEY if shown else SHOW_KEY)


# ── OCR Settings dialog ───────────────────────────────────────────────────────


class OCRSettingsDialog(QDialog):
    """Focused dialog for configuring OCR engine, model, and preprocessing."""

    preprocess_preview_requested = Signal(object)  # emits the current OcrConfig
    addon_installed = Signal(str)

    def __init__(
        self,
        values: dict,
        parent: QWidget | None = None,
        *,
        app_service: _SettingsAppService | None = None,
        hardware_profile: str = "auto",
        open_downloads: OpenDownloads | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Text Recognition Settings")
        self.setMinimumWidth(480)
        screen = QApplication.primaryScreen()
        if screen:
            self.setMaximumHeight(screen.availableGeometry().height() - 60)

        self._app_service = app_service or ProcessingApplicationService()
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

        layout.addWidget(self._build_method_group(hardware_profile, open_downloads))
        layout.addStretch(1)
        main.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)

        self._load_values(values)
        # Trigger initial refresh of the selector (this is handled by load_ocr_config)

    def done(self, result: int) -> None:
        """Stop background threads before the dialog is destroyed."""
        self._ocr_config_selector.stop_threads()
        super().done(result)

    def _build_method_group(
        self, hardware_profile: str, open_downloads: OpenDownloads | None
    ) -> QGroupBox:
        """Build the 'Text Recognition Method' settings group."""
        from ._ocr_config_selector import OcrConfigSelector  # noqa: PLC0415

        group = QGroupBox("Text Recognition Method")
        layout = QVBoxLayout(group)

        # Use OcrConfigSelector for the main OCR configuration (engine, model, preprocessing, temperature)
        self._ocr_config_selector = OcrConfigSelector(
            app_service=self._app_service,
            ollama_url=self._ollama_url,
            show_preview_button=True,
            hardware_profile=hardware_profile,
            open_downloads=open_downloads,
            parent=group,
        )

        # Connect the selector's signals to our dialog signals
        self._ocr_config_selector.preprocess_preview_requested.connect(
            self.preprocess_preview_requested.emit
        )
        self._ocr_config_selector.addon_installed.connect(self.addon_installed.emit)

        layout.addWidget(self._ocr_config_selector)

        # Add debug checkbox separately (not part of OcrConfigSelector)
        self._debug = QCheckBox("Keep processed images for inspection")
        self._debug.setToolTip(
            "When enabled, the processed images used for text recognition are\n"
            "kept in a temporary folder after the run."
        )
        layout.addWidget(self._debug)

        return group

    def _load_values(self, values: dict) -> None:
        ocr_config = OcrConfig.from_settings(values)
        self._ocr_config_selector.load_ocr_config(ocr_config)

        if "debug" in values:
            self._debug.setChecked(bool(values["debug"]))

    def get_values(self) -> dict:
        """Return OCR-specific settings for ConfigPanel.update_settings()."""
        # Get OCR config from the selector
        ocr_config = self._ocr_config_selector.get_ocr_config()

        # Build the result dict from the OCR config
        result = {
            "ocr_engine": ocr_config.engine,
            "ocr_model": ocr_config.model,
            "preprocess_method": ocr_config.preprocess_method,
            "debug": self._debug.isChecked(),
            "ocr_temperature": ocr_config.temperature,
            "pdf_render_dpi": ocr_config.pdf_render_dpi,
            "pdf_text_layer": ocr_config.pdf_text_layer,
            "preprocess_dewarp": ocr_config.dewarp,
            "preprocess_deskew": ocr_config.deskew,
            "preprocess_border_crop": ocr_config.border_crop,
            "preprocess_denoise": ocr_config.denoise,
            "preprocess_gamma": ocr_config.gamma,
        }
        return result


# ── AI service dialogs (Proofreading and Content Review) ─────────────────────


class _AiServiceDialog(QDialog):
    """Instructions, provider, model and temperature: the dialog of one AI-service step.

    Proofreading and Content Review ask for the same thing -- which instructions, which AI
    service, which model -- and differ only in the settings keys and the wording.  The
    subclasses are therefore just configuration, and a fix (such as asking the service again
    after the provider changed) exists once.
    """

    #: Window title and the wording of the instructions group.
    _TITLE = ""
    _PROMPT_TITLE = ""
    _PROMPT_NOTE = ""
    _PROMPT_PLACEHOLDER = ""
    #: Settings keys of this dialog's service, and of the other step's (for the same-model tip).
    _PROVIDER_KEY = ""
    _MODEL_KEY = ""
    _TEMPERATURE_KEY = ""
    _OTHER_PROVIDER_KEY = ""
    _OTHER_MODEL_KEY = ""
    _TEMPERATURE_TOOLTIP = ""
    #: Whether the instructions use the ``{level}`` placeholder (proofreading only).
    _LEVEL_AWARE = False

    def __init__(
        self,
        values: dict,
        prompt: str,
        parent: QWidget | None = None,
        *,
        app_service: _SettingsAppService | None = None,
        hardware_profile: str = "auto",
        open_downloads: OpenDownloads | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self._TITLE)
        self.setMinimumWidth(480)
        screen = QApplication.primaryScreen()
        if screen:
            self.setMaximumHeight(screen.availableGeometry().height() - 60)

        self._app_service: _SettingsAppService = app_service or ProcessingApplicationService()
        self._fetch_thread: _ModelFetchThread | None = None
        self._ollama_url = str(values.get("ollama_url", DEFAULTS["ollama_url"]))
        self._values = values
        self._open_downloads = open_downloads

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
        group = self._build_output_group(values)
        if group is not None:
            layout.addWidget(group)
        layout.addWidget(self._build_model_group(hardware_profile))
        layout.addStretch(1)
        main.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)

        self._load_service_values(values)

    def done(self, result: int) -> None:
        """Stop background threads before the dialog is destroyed."""
        _retire_thread(self._fetch_thread)
        super().done(result)

    def _build_prompt_group(self, prompt: str) -> QGroupBox:
        """Build the instructions group: the editable instructions and what the app adds."""
        prompt_group = QGroupBox(self._PROMPT_TITLE)
        prompt_layout = QVBoxLayout(prompt_group)
        self._prompt_editor = PromptEditor(
            prompt,
            prompt_group,
            builtin_text=self._builtin_prompt(),
            fixed_text=self._fixed_instructions(),
            answer_formats=self._answer_formats(),
            saved_formats=self._saved_answer_formats(),
            level_aware=self._LEVEL_AWARE,
        )
        prompt_layout.addWidget(self._prompt_editor)
        return prompt_group

    def _builtin_prompt(self) -> str:
        """The built-in policy of this step for the document language."""
        return self._app_service.list_correction_prompt_presets().get(
            str(self._values.get("language", DEFAULTS["language"])).lower(),
            self._app_service.default_correction_prompt(),
        )

    def _fixed_instructions(self) -> str:
        """The read-only note the app always adds to the instructions (``""`` = none)."""
        return ""

    def _answer_formats(self) -> tuple[str, str] | None:
        """The built-in answer formats this step lets the teacher edit (``None`` = none)."""
        return None

    def _saved_answer_formats(self) -> tuple[str, str]:
        """The teacher's own answer formats ``(with explanations, corrections only)``."""
        return (
            str(self._values.get("answer_format_review", "")),
            str(self._values.get("answer_format_text", "")),
        )

    def _build_output_group(self, values: dict) -> QGroupBox | None:
        """Build the group holding the explanation language and the output mode."""
        output_group = QGroupBox("Explanations")
        form = QFormLayout(output_group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._explanation_language = QComboBox()
        self._explanation_language.addItem(txt.SAME_AS_TEXT, "")
        for language in _LANGUAGES:  # the item data is what get_values() reads back
            self._explanation_language.addItem(language, language)
        explanation = str(values.get("explanation_language", DEFAULTS["explanation_language"]))
        _set_combo(self._explanation_language, explanation)
        self._explanation_language.setToolTip(txt.EXPLANATION_LANGUAGE_TOOLTIP)
        form.addRow("Explanation language:", self._explanation_language)

        self._corrections_only = QCheckBox(txt.CORRECTIONS_ONLY)
        self._corrections_only.setToolTip(txt.CORRECTIONS_ONLY_TOOLTIP)
        self._corrections_only.setChecked(
            str(values.get("correction_output", DEFAULTS["correction_output"])) == "text"
        )
        form.addRow("", self._corrections_only)
        return output_group

    def _build_model_group(self, hardware_profile: str = "auto") -> QGroupBox:
        """Build the AI-service (provider + model) group."""
        from ._hardware_profile_selector import HardwareProfileSelector  # noqa: PLC0415
        from ._model_guidance import OllamaModelGuidance  # noqa: PLC0415

        model_group = QGroupBox("AI Service")
        model_form = QFormLayout(model_group)
        model_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._hw_selector = HardwareProfileSelector(
            app_service=self._app_service,
            setting=hardware_profile,
            ollama_url=self._ollama_url,
            read_only=True,
            parent=model_group,
        )
        model_form.addRow(self._hw_selector)

        self._provider = QComboBox()
        self._provider.addItems(_available_providers(self._app_service))
        self._provider.currentTextChanged.connect(self._on_provider_selected)
        model_form.addRow("AI service:", self._provider)

        self._where_hint = LocationHint()
        model_form.addRow("", self._where_hint)

        self._package_hint = _PackageHint(self._open_downloads)
        self._package_hint.downloads_closed.connect(self._on_downloads_closed)
        model_form.addRow("", self._package_hint)

        self._model = QComboBox()
        self._model.setEditable(True)
        model_form.addRow("AI model:", self._model)

        self._status_lbl = QLabel(_STATUS_LOADING)
        self._status_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._status_lbl.setWordWrap(True)
        self._status_lbl.linkActivated.connect(lambda _link: self._refetch_models())
        model_form.addRow("", self._status_lbl)

        self._guidance = OllamaModelGuidance(
            combo=self._model,
            app_service=self._app_service,
            category="general",
            profile_selector=self._hw_selector,
            ollama_url=self._ollama_url,
            open_downloads=self._open_downloads,
            refetch=self._refetch_models,
            parent=model_group,
        )
        model_form.addRow("", self._guidance)

        self._same_model_tip_lbl = QLabel(SAME_MODEL_TIP)
        self._same_model_tip_lbl.setWordWrap(True)
        self._same_model_tip_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self._same_model_tip_lbl.setVisible(False)
        model_form.addRow("", self._same_model_tip_lbl)

        self._model.currentTextChanged.connect(lambda _txt: self._update_same_model_tip())
        self._model.currentTextChanged.connect(lambda _txt: self._update_where_hint())
        self._hw_selector.resolved_changed.connect(lambda _d: self._update_same_model_tip())

        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setSingleStep(0.1)
        self._temperature.setDecimals(1)
        self._temperature.setValue(DEFAULTS[self._TEMPERATURE_KEY])
        self._temperature.setToolTip(self._TEMPERATURE_TOOLTIP)
        model_form.addRow("Temperature:", self._temperature)
        return model_group

    def _load_service_values(self, values: dict) -> None:
        """Apply the saved provider, model and temperature, then ask the service for models.

        The provider is set without its change handler: the handler also starts a fetch,
        and here exactly one is wanted, after the saved model is in place.
        """
        if self._PROVIDER_KEY in values:
            self._provider.blockSignals(True)
            _set_combo(self._provider, str(values[self._PROVIDER_KEY]))
            self._provider.blockSignals(False)
        self._on_provider_changed(self._provider.currentText())
        if self._MODEL_KEY in values:
            self._model.setCurrentText(str(values[self._MODEL_KEY]))
        if self._TEMPERATURE_KEY in values:
            self._temperature.setValue(float(values[self._TEMPERATURE_KEY]))
        self._auto_fetch()

    # ── provider change ───────────────────────────────────────────────────────

    def _on_provider_selected(self, provider: str) -> None:
        """The user picked another AI service: show its models, then ask it for the live list."""
        self._on_provider_changed(provider)
        self._auto_fetch()

    def _on_provider_changed(self, provider: str) -> None:
        """Show *provider*'s built-in models and any warning; the live list follows separately.

        A model name from the previous service is kept only if this one offers it: an Ollama
        tag in the Mistral field would be sent to Mistral as it stands.
        """
        info = self._app_service.get_provider_info(provider)
        previous = self._model.currentText().strip()
        models = list(info.get("models", []))
        self._model.clear()
        if models:
            self._model.addItems(models)
        if previous and previous in models:
            self._model.setCurrentText(previous)
        else:
            self._model.setCurrentText(info.get("default_model", ""))
        if provider == "ollama":
            # The local list changes whenever a model is pulled or removed, and is cheap to
            # ask for.  An online service's list fetched a moment ago is still good (a failed
            # one is never cached), so it is kept; "Try again" drops it on request.
            self._app_service.invalidate_model_cache(provider, self._ollama_url)
        # Hint line and "Use best choice" button exist only for Ollama.
        self._guidance.setVisible(provider == "ollama")
        self._update_package_hint(provider)
        self._update_where_hint()

    def _update_package_hint(self, provider: str) -> None:
        """Show the install hint when *provider*'s package is missing, else hide it."""
        pip_name = self._app_service.missing_provider_package(provider)
        if not pip_name:
            self._package_hint.clear()
            return
        name = self._app_service.get_provider_info(provider).get("display_name", provider)
        self._package_hint.show_missing(provider, str(name), pip_name)

    def _on_downloads_closed(self) -> None:
        """Downloads may have installed the package: look again, and ask the service."""
        self._update_package_hint(self._provider.currentText())
        self._refetch_models()

    def _update_where_hint(self) -> None:
        """Say where the chosen AI service runs, so online use is never a surprise."""
        location = self._app_service.describe_llm_location(
            self._provider.currentText(),
            self._model.currentText().strip(),
            self._ollama_url,
        )
        self._where_hint.set_location(location, text_note(location))

    def _update_same_model_tip(self) -> None:
        """Show the same-model tip for cpu/8gb profiles with differing Ollama models."""
        detection = self._hw_selector.resolved()
        profile = detection.profile if detection is not None else None
        other_provider = str(self._values.get(self._OTHER_PROVIDER_KEY, ""))
        other_model = str(self._values.get(self._OTHER_MODEL_KEY, ""))
        show = (
            profile in ("cpu", "8gb")
            and self._provider.currentText() == "ollama"
            and other_provider == "ollama"
            and self._model.currentText().strip() != other_model
        )
        self._same_model_tip_lbl.setVisible(show)

    # ── model list ────────────────────────────────────────────────────────────

    def _refetch_models(self) -> None:
        """Invalidate the model cache of the current service and fetch the list again."""
        provider = self._provider.currentText()
        self._app_service.invalidate_model_cache(
            provider, self._ollama_url if provider == "ollama" else ""
        )
        self._auto_fetch()

    def _auto_fetch(self) -> None:
        # A fetch still running belongs to a service that may no longer be the chosen one.
        _retire_thread(self._fetch_thread)
        provider = self._provider.currentText()
        info = self._app_service.get_provider_info(provider)
        if info.get("needs_api_key") and not os.environ.get(info.get("env_key", "")):
            self._status_lbl.setText(_STATUS_KEY_MISSING)
            return
        url = self._ollama_url if provider == "ollama" else ""
        # Ollama always goes through the thread (a cache hit there is instant):
        # classifying custom models must not run on the GUI thread.
        cached = (
            None
            if provider == "ollama"
            else self._app_service.get_cached_listing(provider, base_url=url)
        )
        if cached is not None:
            self._show_listing(cached)
            return
        self._status_lbl.setText(_STATUS_LOADING)
        if provider == "ollama":

            def fetch() -> ModelListing:
                tags = self._app_service.list_provider_models(provider, base_url=url)
                # Classify here so set_models() only needs cache lookups.
                self._app_service.classify_ollama_models(tags, url)
                return ModelListing(tags)

        else:

            def fetch() -> ModelListing:
                return self._app_service.list_provider_models_checked(provider, base_url=url)

        self._fetch_thread = _ModelFetchThread(fetch, tag=provider)
        self._fetch_thread.listing_ready.connect(self._on_listing_ready)
        self._fetch_thread.start()

    def _on_listing_ready(self, provider: str, listing: object) -> None:
        """Show a fetched list, unless it is for a service the user has already left."""
        if provider != self._provider.currentText() or not isinstance(listing, ModelListing):
            return
        self._show_listing(listing)

    def _on_models_fetched(self, models: list) -> None:
        self._show_listing(ModelListing(list(models)))

    def _show_listing(self, listing: ModelListing) -> None:
        """Fill the model field and the status line from *listing*."""
        models = listing.models
        if not models:
            # A failed answer says why (and offers a retry); only a silent empty one is a dash.
            self._status_lbl.setText(
                _listing_status(listing) if listing.source == "failed" else _STATUS_ERROR
            )
            return
        self._status_lbl.setText(_listing_status(listing))
        if self._provider.currentText() == "ollama":
            self._guidance.set_models(models)
            self._update_same_model_tip()  # set_models() blocks the combo's signals
            return
        previous = self._model.currentText()
        self._model.clear()
        self._model.addItems(models)
        idx = self._model.findText(previous)
        if idx >= 0:
            self._model.setCurrentIndex(idx)
        else:
            self._model.setCurrentText(previous)

    def get_prompt(self) -> str:
        """Return the edited policy text ("" means use the built-in prompt)."""
        return self._prompt_editor.prompt()

    def get_values(self) -> dict:
        """Return this step's provider, model and temperature for ConfigPanel.update_settings()."""
        return {
            self._PROVIDER_KEY: self._provider.currentText(),
            self._MODEL_KEY: self._model.currentText().strip(),
            self._TEMPERATURE_KEY: self._temperature.value(),
        }


# ── Correction Settings dialog ────────────────────────────────────────────────


class CorrectionSettingsDialog(_AiServiceDialog):
    """Focused dialog for configuring the correction system prompt, provider, and model."""

    _TITLE = "Proofreading Settings"
    _PROMPT_TITLE = "Proofreading Instructions"
    _PROVIDER_KEY = "correction_provider"
    _MODEL_KEY = "correction_model"
    _TEMPERATURE_KEY = "correction_temperature"
    _OTHER_PROVIDER_KEY = "evaluate_provider"
    _OTHER_MODEL_KEY = "evaluate_model"
    _LEVEL_AWARE = True
    _TEMPERATURE_TOOLTIP = (
        "Sampling temperature for the proofreading model\n"
        "(0.0 = deterministic, higher = more creative)."
    )

    def get_values(self) -> dict:
        """Correction adds the explanation language and the output mode."""
        values = super().get_values()
        values["explanation_language"] = str(self._explanation_language.currentData() or "")
        values["correction_output"] = "text" if self._corrections_only.isChecked() else "review"
        values.update(self._prompt_editor.answer_format_values())
        return values

    def _fixed_instructions(self) -> str:
        """The note about formatted texts; the answer formats have their own editors."""
        return txt.formatted_text_note(self._app_service.correction_notation_hint())

    def _answer_formats(self) -> tuple[str, str] | None:
        return self._app_service.default_answer_formats()


# ── Evaluation Settings dialog ────────────────────────────────────────────────


class EvaluationSettingsDialog(_AiServiceDialog):
    """Focused dialog for configuring the evaluation system prompt, provider, and model."""

    _TITLE = "Content Review Settings"
    _PROMPT_TITLE = "Content Review Instructions"
    _PROVIDER_KEY = "evaluate_provider"
    _MODEL_KEY = "evaluate_model"
    _TEMPERATURE_KEY = "evaluate_temperature"
    _OTHER_PROVIDER_KEY = "correction_provider"
    _OTHER_MODEL_KEY = "correction_model"
    _TEMPERATURE_TOOLTIP = (
        "Sampling temperature for the content review model\n"
        "(0.0 = deterministic, higher = more creative)."
    )

    def _builtin_prompt(self) -> str:
        """Content Review has a fixed built-in policy."""
        return str(DEFAULTS["evaluate_prompt"])

    def _fixed_instructions(self) -> str:
        """The fixed part of the review instructions."""
        return txt.formatted_text_note(
            str(self._app_service.evaluation_fixed_instructions("markdown"))
        )

    def _build_output_group(self, values: dict) -> QGroupBox | None:
        _ = values  # Content Review has no explanation-language or output-mode settings
        return None


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
        self._hardware_profile_setting = str(values.get("hardware_profile", "auto"))
        # Known before any widget is built: "My computer" must detect for the
        # configured server, not for the default one.
        self._initial_ollama_url = str(values.get("ollama_url") or DEFAULTS["ollama_url"])

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

    def done(self, result: int) -> None:
        """Detach the GPU-detection thread before the dialog is destroyed."""
        _retire_thread(self._gpu_detect_thread)
        super().done(result)

    def _build_ollama_group(self) -> QGroupBox:
        """Build the Ollama server-URL group."""
        ollama_group = QGroupBox("Ollama")
        ollama_form = QFormLayout(ollama_group)
        ollama_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._ollama_url_label = QLabel("Server URL:")
        self._ollama_url = QLineEdit(self._initial_ollama_url)
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
        from ._hardware_profile_selector import HardwareProfileSelector  # noqa: PLC0415

        hw_group = QGroupBox("Performance")
        hw_form = QFormLayout(hw_group)
        hw_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._hardware_selector = HardwareProfileSelector(
            app_service=self._app_service,
            setting=self._hardware_profile_setting,
            ollama_url=self._ollama_url.text(),
        )
        # editingFinished, not textChanged: a half-typed URL must not trigger detection.
        self._ollama_url.editingFinished.connect(
            lambda: self._hardware_selector.set_ollama_url(self._ollama_url.text().strip())
        )
        hw_form.addRow(self._hardware_selector)

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
        """Build the provider API-keys group (rows are loaded separately)."""
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

        self._cred_rows: list[_CredentialRow] = []
        self._cred_rows_layout = QVBoxLayout()
        self._cred_rows_layout.setSpacing(2)
        cred_vbox.addLayout(self._cred_rows_layout)

        self._cred_add_row = QWidget()
        add_layout = QHBoxLayout(self._cred_add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        self._cred_add_combo = QComboBox()
        add_layout.addWidget(self._cred_add_combo, stretch=1)
        self._cred_add_btn = QPushButton(ADD_KEY)
        self._cred_add_btn.clicked.connect(self._add_new_cred_row)
        add_layout.addWidget(self._cred_add_btn)
        cred_vbox.addWidget(self._cred_add_row)
        return cred_group

    # ── credentials rows ───────────────────────────────────────────────────────

    def _key_titles(self) -> dict[str, str]:
        """Return ``{env_key: title}``: providers that share one key (Mistral, Mistral EU) share a row."""
        names: dict[str, list[str]] = {}
        for provider in self._app_service.list_providers():
            info = self._app_service.get_provider_info(provider)
            env_key: str = info.get("env_key", "")
            if env_key:
                names.setdefault(env_key, []).append(str(info.get("display_name", provider)))
        return {env_key: " · ".join(titles) for env_key, titles in names.items()}

    def _load_credentials_rows(self) -> None:
        qs = QSettings("TeachersTeammate", "TeachersTeammate")
        qs.beginGroup("credentials")
        stored_keys = set(qs.childKeys())
        qs.endGroup()
        for env_key in self._key_titles():
            value = os.environ.get(env_key, "")
            if not value:
                qs2 = QSettings("TeachersTeammate", "TeachersTeammate")
                value = str(qs2.value(f"credentials/{env_key}", ""))
            if value or env_key in stored_keys:
                self._add_cred_row(env_key, value)
        self._refresh_add_combo()

    def _used_env_keys(self) -> set[str]:
        return {row.env_key for row in self._cred_rows}

    def _credential_field(self, env_key: str) -> QLineEdit | None:
        """Return the input field of the row for *env_key*, or ``None`` when it has no row."""
        for row in self._cred_rows:
            if row.env_key == env_key:
                return row.field
        return None

    def _add_cred_row(
        self, env_key: str, value: str = "", *, placeholder: str = KEY_NOT_SET
    ) -> _CredentialRow:
        row = _CredentialRow(
            env_key,
            self._key_titles().get(env_key, env_key),
            value,
            placeholder=placeholder,
        )
        row.remove_requested.connect(self._remove_cred_row)
        self._cred_rows.append(row)
        self._cred_rows_layout.addWidget(row)
        return row

    def _refresh_add_combo(self) -> None:
        """List the services that have no key row yet; hide the picker when all have one."""
        used = self._used_env_keys()
        self._cred_add_combo.clear()
        for env_key, title in self._key_titles().items():
            if env_key not in used:
                self._cred_add_combo.addItem(title, userData=env_key)
        self._cred_add_row.setVisible(self._cred_add_combo.count() > 0)

    def _add_new_cred_row(self) -> None:
        env_key = self._cred_add_combo.currentData()
        if not env_key:
            return
        row = self._add_cred_row(str(env_key), placeholder=KEY_PLACEHOLDER)
        self._refresh_add_combo()
        row.field.setFocus()

    def _remove_cred_row(self, row: object) -> None:
        if not isinstance(row, _CredentialRow) or row not in self._cred_rows:
            return
        self._cred_rows.remove(row)
        self._cred_rows_layout.removeWidget(row)
        row.deleteLater()
        self._refresh_add_combo()

    def _apply_credentials(self) -> None:
        qs = QSettings("TeachersTeammate", "TeachersTeammate")
        qs.beginGroup("credentials")
        existing_keys = set(qs.childKeys())
        qs.endGroup()

        new_creds: dict[str, str] = {}
        for row in self._cred_rows:
            value = row.field.text().strip()
            if value:
                new_creds[row.env_key] = value

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
        self._gpu_detect_thread = _GpuDetectThread(self._app_service)
        self._gpu_detect_thread.detect_done.connect(self._on_gpu_detected)
        self._gpu_detect_thread.finished.connect(lambda: self._gpu_detect_btn.setEnabled(True))
        self._gpu_detect_thread.start()

    def _on_gpu_detected(self, gpus: list) -> None:
        self._detected_gpus = gpus
        # The probe was just repeated: let "My computer" describe the new result.
        self._hardware_selector.refresh_status()
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
            "hardware_profile": self._hardware_selector.setting(),
        }


# ── Output settings dialog ────────────────────────────────────────────────────


class OutputSettingsDialog(QDialog):
    """Modal dialog for choosing the output folder and which files a run saves there."""

    def __init__(self, values: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Output Settings")
        self.setMinimumWidth(340)

        main = QVBoxLayout(self)
        main.setSpacing(12)

        out_group = QGroupBox("Output Files")
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
        self._add_text_file_rows(out_form)

        main.addWidget(out_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
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
        self._text_enabled.setChecked(bool(values.get("text_enabled", False)))
        self._text_anonymized_enabled.setChecked(bool(values.get("text_anonymized_enabled", False)))
        self._update_visibility()

    def _add_text_file_rows(self, out_form: QFormLayout) -> None:
        self._text_enabled = QCheckBox("Create text files")
        self._text_enabled.setToolTip(
            "Save the extracted text of each input file as a text file (name.txt)."
        )
        out_form.addRow(self._text_enabled)

        self._text_anonymized_enabled = QCheckBox("Create anonymized text files")
        self._text_anonymized_enabled.setToolTip(
            "Save the extracted text with names and other personal data replaced by\n"
            "placeholders (name_anonymized.txt). Runs on this computer and needs the\n"
            "privacy add-on (spaCy)."
        )
        out_form.addRow(self._text_anonymized_enabled)

        text_hint = QLabel(
            "Text files need an output folder outside the input folder; otherwise the next "
            "run would read them as new documents."
        )
        text_hint.setWordWrap(True)
        text_hint.setStyleSheet("color: #888; font-size: 8pt;")
        out_form.addRow(text_hint)

    def _on_accept(self) -> None:
        # Blank means "next to your documents", i.e. the input folder.
        wants_text = self._text_enabled.isChecked() or self._text_anonymized_enabled.isChecked()
        if wants_text and not self._output_dir.text().strip():
            QMessageBox.warning(
                self,
                "Output folder needed",
                "Choose a folder outside the input folder to save text files to.",
            )
            return
        self.accept()

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
            "text_enabled": self._text_enabled.isChecked(),
            "text_anonymized_enabled": self._text_anonymized_enabled.isChecked(),
        }
