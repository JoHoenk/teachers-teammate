"""Configuration panel — main pipeline settings.

Advanced model/provider settings (OCR engine, model, Ollama URL, correction
provider/model, evaluation provider/model, preprocessing method, and output options) are managed via the
Settings dialog (accessible from the menu bar) and stored in ``_settings_dict``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config import DEFAULTS, Config, OcrConfig
from ._constants import _LANGUAGES
from ._types import SettingsDict


class _PromptDialog(QDialog):
    """Simple modal dialog for viewing and editing a system prompt."""

    def __init__(self, title: str, prompt: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(500, 320)
        vbox = QVBoxLayout(self)
        self._edit = QPlainTextEdit()
        self._edit.setPlainText(prompt)
        self._edit.setPlaceholderText(
            "Leave empty to use the built-in default instructions for the selected language."
        )
        vbox.addWidget(self._edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)

    def prompt(self) -> str:
        return self._edit.toPlainText().strip()


class ConfigPanel(QScrollArea):
    """Left-hand panel: main pipeline configuration as a numbered workflow."""

    paths_changed = Signal()
    anonymizer_configure_requested = Signal(str, object)  # language, AnonymizerConfig
    open_downloads_requested = Signal(int)  # bubbled from DependencyGuardDialog; int = tab index

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        preset_prompts: dict[str, str] | None = None,
        default_prompt: str = "",
    ) -> None:
        super().__init__(parent)
        self._preset_prompts: dict[str, str] = preset_prompts or {}
        self._default_prompt: str = default_prompt
        self._service: object | None = None  # ProcessingApplicationService, set via set_service()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(340)
        self.setMaximumWidth(420)

        # Advanced settings stored here; edited via the Settings dialog.
        self._settings_dict: SettingsDict = {
            "ocr_engine": DEFAULTS["ocr_engine"],
            "ocr_provider": DEFAULTS["ocr_provider"],
            "ocr_model": DEFAULTS["ocr_model"],
            "ollama_url": DEFAULTS["ollama_url"],
            "ocr_timeout": DEFAULTS["ocr_timeout"],
            "preprocess_method": DEFAULTS["preprocess_method"],
            "correction_provider": DEFAULTS["correction_provider"],
            "correction_model": DEFAULTS["correction_model"],
            "evaluate_provider": DEFAULTS["evaluate_provider"],
            "evaluate_model": DEFAULTS["evaluate_model"],
            "output_dir": "",
            "docx_format": DEFAULTS["docx_format"],
            "docx_enabled": DEFAULTS["docx_enabled"],
            "evaluation_enabled": DEFAULTS["evaluation_enabled"],
            "anonymization_enabled": DEFAULTS["anonymization_enabled"],
            "debug": DEFAULTS["debug"],
        }
        self._settings_dict["anonymizer_secondary_model"] = None
        self._settings_dict["anonymizer_patterns"] = None

        # Input selection state: either a folder or an explicit file list.
        self._selected_folder: Path | None = None
        self._selected_files: list[Path] = []

        # Prompt texts stored internally (not live QPlainTextEdits in the panel).
        self._correction_prompt_text: str = ""
        self._evaluation_prompt_text: str = DEFAULTS["evaluate_prompt"]

        inner = QWidget()
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(6)

        self._build_step1_select_input()
        self._layout.addWidget(self._separator())
        self._build_step2_language()
        self._layout.addWidget(self._separator())
        self._build_step3_anonymize()
        self._layout.addWidget(self._separator())
        self._build_step4_correct()
        self._layout.addWidget(self._separator())
        self._build_step5_evaluate()
        self._layout.addStretch()

        self.setWidget(inner)

        self._language.currentTextChanged.connect(self._on_language_changed)
        self._set_auto_prompt(self._language.currentText())

        self._update_correction_visibility()
        self._update_anonymize_visibility()

    # ── step header helper ────────────────────────────────────────────────────────

    @staticmethod
    def _step_header(text: str, tooltip: str) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 2)
        lbl = QLabel(text.upper())
        lbl.setStyleSheet("QLabel { font-size: 11pt; font-weight: bold; color: #aaa; }")
        info = QLabel("i")
        info.setStyleSheet(
            "QLabel { color: white; background: #2980b9; border-radius: 9px; "
            "padding: 1px 5px; font-size: 11pt; font-weight: normal; }"
        )
        info.setToolTip(tooltip)
        h.addWidget(lbl)
        h.addStretch()
        h.addWidget(info)
        return row

    # ── section builders ──────────────────────────────────────────────────────────

    def _build_step1_select_input(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "Step 1: Select Your Documents",
                "Choose the documents you want to process.\n"
                "You can pick individual files or a whole folder.\n"
                "Subfolders are included automatically.",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(6)

        btn_row = QWidget()
        h = QHBoxLayout(btn_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        self._select_folder_btn = QPushButton("Select Folder…")
        self._select_folder_btn.setToolTip(
            "Choose a folder — all supported files inside will be processed, including subfolders."
        )
        self._select_folder_btn.clicked.connect(self._on_select_folder)

        self._select_files_btn = QPushButton("Select Files…")
        self._select_files_btn.setToolTip(
            "Choose one or more individual files (PDF, images, or plain text)."
        )
        self._select_files_btn.clicked.connect(self._on_select_files)

        h.addWidget(self._select_folder_btn)
        h.addWidget(self._select_files_btn)
        v.addWidget(btn_row)

        self._input_selection_label = QLabel("No input selected")
        self._input_selection_label.setWordWrap(True)
        self._input_selection_label.setStyleSheet("color: #888; font-size: 8pt;")
        v.addWidget(self._input_selection_label)

        self._layout.addWidget(inner)

    def _build_step2_language(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "Step 2: Document Language",
                "Select the language the documents are written in.\n"
                "This helps the app recognise text accurately and\n"
                "proofread in the correct language.",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(4)

        self._language = QComboBox()
        self._language.addItems(_LANGUAGES)
        self._language.setEditable(True)
        self._language.setCurrentText("English")
        self._language.activated.connect(self._language.hidePopup)
        v.addWidget(self._language)

        self._layout.addWidget(inner)

    def _build_step3_anonymize(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "Step 3: Remove Personal Information",
                "Automatically hides names, email addresses, and phone numbers\n"
                "before sending text to the AI service for proofreading.\n"
                "The original information is restored in the final document.\n"
                "Requires the Privacy add-on (see Downloads).",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(6)

        self._anonymization_enabled = QCheckBox("Remove personal info")
        self._anonymization_enabled.setChecked(False)
        self._anonymization_enabled.toggled.connect(self._on_anonymization_changed)
        v.addWidget(self._anonymization_enabled)

        self._anon_configure_btn = QPushButton("Privacy rules…")
        self._anon_configure_btn.setVisible(True)
        self._anon_configure_btn.setToolTip(
            "Set which types of personal information to hide and how."
        )
        self._anon_configure_btn.clicked.connect(self._on_configure_anonymizer)
        v.addWidget(self._anon_configure_btn)

        self._layout.addWidget(inner)

    def _build_step4_correct(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "Step 4: Proofread",
                "Use an AI service to fix spelling and grammar in the extracted text.\n"
                "Requires a connected AI service (see Settings → Proofreading Settings).",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(6)

        self._correction_enabled = QCheckBox("Enable proofreading")
        self._correction_enabled.setChecked(True)
        self._correction_enabled.toggled.connect(self._update_correction_visibility)
        v.addWidget(self._correction_enabled)

        self._correction_prompt_btn = QPushButton("Instructions…")
        self._correction_prompt_btn.setToolTip(
            "Customize the instructions sent to the AI service for proofreading.\n"
            "Leave empty to use the built-in default for the selected language."
        )
        self._correction_prompt_btn.clicked.connect(self._on_configure_correction_prompt)
        v.addWidget(self._correction_prompt_btn)

        self._layout.addWidget(inner)

    def _build_step5_evaluate(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "Step 5: Content Review",
                "Let an AI service review the proofread text and generate a content review report.",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(6)

        self._evaluation_enabled = QCheckBox("Enable content review")
        self._evaluation_enabled.setChecked(False)
        self._evaluation_enabled.toggled.connect(self._on_evaluation_enabled_changed)
        v.addWidget(self._evaluation_enabled)

        self._evaluation_prompt_btn = QPushButton("Instructions…")
        self._evaluation_prompt_btn.setToolTip(
            "Customize the instructions used to generate the content review report."
        )
        self._evaluation_prompt_btn.clicked.connect(self._on_configure_evaluation_prompt)
        v.addWidget(self._evaluation_prompt_btn)

        self._layout.addWidget(inner)

    # ── static helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _separator() -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(0)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #444;")
        layout.addWidget(line)
        return container

    # ── input selection ───────────────────────────────────────────────────────────

    def _on_select_folder(self) -> None:
        start = str(self._selected_folder or Path.home())
        options = QFileDialog.Option(0)
        if sys.platform == "win32":
            options |= QFileDialog.Option.DontUseNativeDialog
        path = QFileDialog.getExistingDirectory(None, "Select Input Folder", start, options)
        if path:
            self._selected_folder = Path(path)
            self._selected_files = []
            self._update_input_label()
            self.paths_changed.emit()

    def _on_select_files(self) -> None:
        start = str(self._selected_folder or Path.home())
        options = QFileDialog.Option(0)
        if sys.platform == "win32":
            options |= QFileDialog.Option.DontUseNativeDialog
        paths, _ = QFileDialog.getOpenFileNames(
            None,
            "Select Input Files",
            start,
            "Supported files (*.pdf *.PDF *.png *.PNG *.jpg *.JPG *.jpeg *.JPEG *.tiff *.TIFF *.tif *.TIF *.txt *.TXT);;All files (*)",
            options=options,
        )
        if paths:
            self._selected_files = [Path(p) for p in paths]
            self._selected_folder = None
            self._update_input_label()
            self.paths_changed.emit()

    def _update_input_label(self) -> None:
        if self._selected_files:
            n = len(self._selected_files)
            parent = self._selected_files[0].parent
            self._input_selection_label.setText(f"{n} file{'s' if n != 1 else ''} from {parent}")
            self._input_selection_label.setStyleSheet("color: #ddd; font-size: 8pt;")
        elif self._selected_folder is not None:
            self._input_selection_label.setText(f"Folder: {self._selected_folder}")
            self._input_selection_label.setStyleSheet("color: #ddd; font-size: 8pt;")
        else:
            self._input_selection_label.setText("No input selected")
            self._input_selection_label.setStyleSheet("color: #888; font-size: 8pt;")

    # ── prompt dialogs ────────────────────────────────────────────────────────────

    def _on_configure_correction_prompt(self) -> None:
        dlg = _PromptDialog("Configure Correction Prompt", self._correction_prompt_text, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._correction_prompt_text = dlg.prompt()

    def _on_configure_evaluation_prompt(self) -> None:
        dlg = _PromptDialog("Configure Evaluation Prompt", self._evaluation_prompt_text, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._evaluation_prompt_text = dlg.prompt()

    # ── language change ───────────────────────────────────────────────────────────

    def _on_language_changed(self, language: str) -> None:
        current = self._correction_prompt_text.strip()
        if not current or current in frozenset(self._preset_prompts.values()):
            self._set_auto_prompt(language)

    def _set_auto_prompt(self, language: str) -> None:
        prompt = self._preset_prompts.get(language.lower(), self._default_prompt)
        self._correction_prompt_text = prompt

    # ── visibility updates ────────────────────────────────────────────────────────

    def _update_correction_visibility(self, checked: bool = True) -> None:
        if not checked:
            self._settings_dict["correction_enabled"] = False
            return
        if not self._check_stage_and_guard(self._correction_enabled, "correction"):
            self._correction_enabled.blockSignals(True)
            self._correction_enabled.setChecked(False)
            self._correction_enabled.blockSignals(False)
            self._settings_dict["correction_enabled"] = False
            return
        self._settings_dict["correction_enabled"] = True
        self._correction_prompt_btn.setEnabled(True)

    def _update_anonymize_visibility(self) -> None:
        pass  # driven by _on_anonymization_changed

    def _on_evaluation_enabled_changed(self, checked: bool) -> None:
        if not checked:
            self._settings_dict["evaluation_enabled"] = False
            return
        if not self._check_stage_and_guard(self._evaluation_enabled, "evaluation"):
            self._evaluation_enabled.blockSignals(True)
            self._evaluation_enabled.setChecked(False)
            self._evaluation_enabled.blockSignals(False)
            self._settings_dict["evaluation_enabled"] = False
            return
        self._settings_dict["evaluation_enabled"] = True

    def _on_anonymization_changed(self, checked: bool = True) -> None:
        if not checked:
            self._settings_dict["anonymization_enabled"] = False
            return
        if not self._check_stage_and_guard(self._anonymization_enabled, "anonymizer"):
            self._anonymization_enabled.blockSignals(True)
            self._anonymization_enabled.setChecked(False)
            self._anonymization_enabled.blockSignals(False)
            self._settings_dict["anonymization_enabled"] = False
            return
        self._settings_dict["anonymization_enabled"] = True

    def build_anonymizer_config(self) -> object:
        """Build an :class:`AnonymizerConfig` from the current settings dict.

        ``anonymizer_patterns`` of ``None`` means "use the default patterns";
        a non-empty list overrides them (mirrors ``Config.anonymizer_patterns``).
        Centralised here so callers (this panel and the main window's privacy
        preview) do not each rebuild the config from raw settings keys.
        """
        from ..application.service import (  # noqa: PLC0415
            DEFAULT_ANONYMIZER_PATTERNS,
            AnonymizerConfig,
        )

        patterns = self._settings_dict.get("anonymizer_patterns")
        return AnonymizerConfig(
            secondary_model=self._settings_dict.get("anonymizer_secondary_model") or None,
            patterns=tuple(patterns)
            if patterns is not None
            else tuple(DEFAULT_ANONYMIZER_PATTERNS),
        )

    def _on_configure_anonymizer(self) -> None:
        language = self._language.currentText().strip()
        self.anonymizer_configure_requested.emit(language, self.build_anonymizer_config())

    def request_anonymizer_config(self) -> None:
        """Emit anonymizer_configure_requested (called from the menu action)."""
        self._on_configure_anonymizer()

    def update_anonymizer_config(self, config: object) -> None:
        """Write an updated AnonymizerConfig back into the settings dict."""
        from ..application.service import AnonymizerConfig  # noqa: PLC0415

        if not isinstance(config, AnonymizerConfig):
            return
        self._settings_dict["anonymizer_secondary_model"] = config.secondary_model
        self._settings_dict["anonymizer_patterns"] = (
            list(config.patterns) if config.patterns else None
        )

    # ── public API ────────────────────────────────────────────────────────────────

    def get_input_dir(self) -> str:
        """Return the current input directory as a string.

        Returns the selected folder, or the common parent of selected files, or empty string.
        """
        if self._selected_folder is not None:
            return str(self._selected_folder)
        if self._selected_files:
            return str(self._selected_files[0].parent)
        return ""

    def set_input_dir(self, path: str) -> None:
        """Set folder-mode input (e.g. from drag-and-drop). Clears any explicit file selection."""
        if path:
            self._selected_folder = Path(path)
        else:
            self._selected_folder = None
        self._selected_files = []
        self._update_input_label()
        self.paths_changed.emit()

    def get_selected_files(self) -> list[Path] | None:
        """Return the explicitly selected file list, or None when using folder-based discovery."""
        return list(self._selected_files) if self._selected_files else None

    def set_selected_files(self, paths: list[Path]) -> None:
        """Set an explicit list of input files (e.g. from drag-and-drop of individual files)."""
        self._selected_files = list(paths)
        self._selected_folder = None
        self._update_input_label()
        self.paths_changed.emit()

    def get_correction_prompt(self) -> str:
        """Return the current correction system prompt text."""
        return self._correction_prompt_text

    def set_correction_prompt(self, text: str) -> None:
        """Update the correction system prompt (e.g. from a focused settings dialog)."""
        self._correction_prompt_text = text

    def get_evaluation_prompt(self) -> str:
        """Return the current evaluation system prompt text."""
        return self._evaluation_prompt_text

    def set_evaluation_prompt(self, text: str) -> None:
        """Update the evaluation system prompt (e.g. from a focused settings dialog)."""
        self._evaluation_prompt_text = text

    def get_output_dir(self) -> str:
        """Return the configured output directory (set via the Output Settings dialog)."""
        return str(self._settings_dict.get("output_dir", "")).strip()

    def get_settings_dict(self) -> dict:
        """Return advanced runtime settings used by the settings dialog."""
        return dict(self._settings_dict)

    def update_settings(self, settings: dict) -> None:
        """Apply values returned by :class:`SettingsDialog`."""
        self._settings_dict.update(settings)

    def to_config(self) -> Config:
        """Build and return a Config from current field values."""
        input_text = self.get_input_dir()
        output_text = str(self._settings_dict.get("output_dir", "")).strip()
        docx_enabled = bool(self._settings_dict.get("docx_enabled", True))
        if not input_text:
            raise ValueError("Input folder is required.")
        if not output_text and docx_enabled:
            raise ValueError("Output folder is required.")
        input_dir = Path(input_text)
        if not input_dir.is_dir():
            raise ValueError(f"Input folder does not exist: {input_dir}")
        output_dir = Path(output_text) if output_text else input_dir

        return Config(
            input_dir=input_dir,
            output_dir=output_dir,
            # The GUI is intentionally always-recursive; no toggle is exposed.
            recursive=True,
            debug=bool(self._settings_dict.get("debug", DEFAULTS["debug"])),
            ocr=OcrConfig(
                engine=self._settings_dict.get("ocr_engine", DEFAULTS["ocr_engine"]),
                provider=self._settings_dict.get("ocr_provider", DEFAULTS["ocr_provider"]),
                model=self._settings_dict.get("ocr_model", DEFAULTS["ocr_model"]),
                preprocess_method=self._settings_dict.get(
                    "preprocess_method", DEFAULTS["preprocess_method"]
                ),
                temperature=float(
                    self._settings_dict.get("ocr_temperature", DEFAULTS["ocr_temperature"])
                ),
            ),
            language=self._language.currentText().strip() or DEFAULTS["language"],
            ollama_url=self._settings_dict.get("ollama_url", DEFAULTS["ollama_url"]),
            ocr_timeout=int(self._settings_dict.get("ocr_timeout", DEFAULTS["ocr_timeout"])),
            correction_enabled=self._correction_enabled.isChecked(),
            anonymization_enabled=self._anonymization_enabled.isChecked(),
            anonymizer_secondary_model=self._settings_dict.get(
                "anonymizer_secondary_model", DEFAULTS["anonymizer_secondary_model"]
            )
            or None,
            anonymizer_patterns=self._settings_dict.get("anonymizer_patterns") or None,
            correction_provider=self._settings_dict.get(
                "correction_provider", DEFAULTS["correction_provider"]
            ),
            correction_model=self._settings_dict.get(
                "correction_model", DEFAULTS["correction_model"]
            ),
            correction_prompt=self._correction_prompt_text,
            evaluation_enabled=self._evaluation_enabled.isChecked(),
            evaluate_provider=self._settings_dict.get(
                "evaluate_provider", DEFAULTS["evaluate_provider"]
            ),
            evaluate_model=self._settings_dict.get("evaluate_model", DEFAULTS["evaluate_model"]),
            evaluate_prompt=self._evaluation_prompt_text,
            evaluate_temperature=float(
                self._settings_dict.get("evaluate_temperature", DEFAULTS["evaluate_temperature"])
            ),
            correction_temperature=float(
                self._settings_dict.get(
                    "correction_temperature", DEFAULTS["correction_temperature"]
                )
            ),
            docx_format=self._settings_dict.get("docx_format", DEFAULTS["docx_format"]),
            docx_enabled=docx_enabled,
        )

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def load_from_dict(self, values: dict) -> None:
        """Populate fields from a dict (e.g. loaded from ocr.toml)."""
        if "input" in values:
            path = Path(str(values["input"]))
            if path.is_dir():
                self._selected_folder = path
                self._selected_files = []
            self._update_input_label()
        if "output" in values:
            self._settings_dict["output_dir"] = str(values["output"])

        self._load_prompts_and_language(values)

        if "correction_enabled" in values:
            self._correction_enabled.setChecked(bool(values["correction_enabled"]))
        if "anonymization_enabled" in values:
            self._anonymization_enabled.setChecked(bool(values["anonymization_enabled"]))
        if "evaluation_enabled" in values:
            self._evaluation_enabled.setChecked(bool(values["evaluation_enabled"]))

        self._apply_settings_dict_values(values)

        self._update_correction_visibility(self._correction_enabled.isChecked())

    def _load_prompts_and_language(self, values: dict) -> None:
        """Apply the correction/evaluation prompts and language combo from *values*.

        Sets the prompts BEFORE the language so that when the language combo fires
        ``currentTextChanged -> _on_language_changed``, it can correctly decide
        whether to overwrite the prompt (empty / preset) or keep it (custom text).
        """
        self._language.currentTextChanged.disconnect(self._on_language_changed)
        try:
            if "correction_prompt" in values:
                self._correction_prompt_text = str(values["correction_prompt"])
            if "evaluate_prompt" in values:
                self._evaluation_prompt_text = str(values["evaluate_prompt"])
            if "language" in values:
                self._set_combo(self._language, str(values["language"]))
        finally:
            self._language.currentTextChanged.connect(self._on_language_changed)
        self._on_language_changed(self._language.currentText())

    def _apply_settings_dict_values(self, values: dict) -> None:
        """Copy the recognised string/scalar/flag keys from *values* into the settings dict."""
        for key in (
            "ocr_engine",
            "ocr_model",
            "ollama_url",
            "preprocess_method",
            "correction_provider",
            "correction_model",
            "evaluate_provider",
            "evaluate_model",
            "docx_format",
        ):
            if key in values:
                self._settings_dict[key] = str(values[key])

        if "ocr_timeout" in values:
            self._settings_dict["ocr_timeout"] = int(values["ocr_timeout"])

        if "debug" in values:
            self._settings_dict["debug"] = bool(values["debug"])

        if "docx_enabled" in values:
            self._settings_dict["docx_enabled"] = bool(values["docx_enabled"])
        if "evaluation_enabled" in values:
            self._settings_dict["evaluation_enabled"] = bool(values["evaluation_enabled"])
        if "anonymization_enabled" in values:
            self._settings_dict["anonymization_enabled"] = bool(values["anonymization_enabled"])
        if "anonymizer_secondary_model" in values:
            self._settings_dict["anonymizer_secondary_model"] = (
                values["anonymizer_secondary_model"] or None
            )
        if "anonymizer_patterns" in values:
            self._settings_dict["anonymizer_patterns"] = values["anonymizer_patterns"] or None

    def set_service(self, service: object) -> None:
        """Inject the application service so the panel can run requirement checks."""
        self._service = service

    def _check_stage_and_guard(self, checkbox: QCheckBox, stage: str) -> bool:
        """Run requirement checks for *stage*; show guard dialog if any fail.

        Returns True when all checks pass (checkbox should stay checked).
        Returns False when checks fail (caller should revert the checkbox).
        """
        if not checkbox.isChecked() or self._service is None:
            return True
        from ..application.service import ProcessingApplicationService  # noqa: PLC0415
        from ._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

        service: ProcessingApplicationService = self._service  # ty: ignore[invalid-assignment]  # _service is injected as object; narrowed to the concrete service type
        config = service.build_lookup_config(
            Path(self.get_input_dir() or "."),
            Path(self._settings_dict.get("output_dir") or "."),
        )
        # Overlay live panel values so checks use the currently selected providers/models
        config = dataclasses.replace(
            config,
            correction_provider=str(
                self._settings_dict.get("correction_provider", config.correction_provider)
            ),
            correction_model=str(
                self._settings_dict.get("correction_model", config.correction_model or "")
            ),
            evaluate_provider=str(
                self._settings_dict.get("evaluate_provider", config.evaluate_provider)
            ),
            evaluate_model=str(
                self._settings_dict.get("evaluate_model", config.evaluate_model or "")
            ),
            ollama_url=str(self._settings_dict.get("ollama_url", config.ollama_url)),
            language=self._language.currentText().strip(),
        )
        issues = service.check_stage_requirements(config, stage)
        if not issues:
            return True
        from ._downloads_dialog import DownloadsDialog  # noqa: PLC0415

        tab = DownloadsDialog.TAB_SPACY if stage == "anonymizer" else 0
        dlg = DependencyGuardDialog(stage, issues, self)
        dlg.open_downloads.connect(lambda: self.open_downloads_requested.emit(tab))
        dlg.exec()
        return False

    def restore_defaults(self) -> None:
        self.load_from_dict(DEFAULTS)

    def to_dict(self) -> dict:
        """Return current values as a TOML-compatible settings dict.

        Note: the GUI is intentionally always-recursive (see :meth:`to_config`),
        so ``recursive`` is deliberately not persisted — writing it would imply a
        toggle the GUI does not expose.
        """
        return {
            "input": self.get_input_dir(),
            "output": str(self._settings_dict.get("output_dir", "")).strip(),
            "language": self._language.currentText().strip(),
            "correction_enabled": self._correction_enabled.isChecked(),
            "anonymization_enabled": self._anonymization_enabled.isChecked(),
            "correction_prompt": self._correction_prompt_text,
            "evaluate_prompt": self._evaluation_prompt_text,
            **self._settings_dict,
        }
