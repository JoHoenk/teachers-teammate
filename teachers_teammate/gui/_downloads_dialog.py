"""Downloads & packages dialog — install OCR engines, providers, and models.

Extracted from the Settings dialog so it can be reached from its own menu entry.
Each downloadable category lives on its own tab; a shared log and a single
"Install / Download Selected" button act across all tabs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..application.service import ProcessingApplicationService
from ..config import DEFAULTS
from ._constants import _SPACY_MODEL_CHOICES as _SPACY_MODEL_DOWNLOAD_CHOICES

if TYPE_CHECKING:
    # Real type of the injected pull manager. Imported under TYPE_CHECKING only:
    # _main_window lazily imports this dialog, so a runtime import would be circular.
    from ._main_window import OllamaDownloadManager

# provider_name → (importable_module, pip_package_name)
_ALL_PROVIDER_PACKAGES: dict[str, tuple[str, str]] = {
    "openai": ("langchain_openai", "langchain-openai"),
    "anthropic": ("langchain_anthropic", "langchain-anthropic"),
    "google": ("langchain_google_genai", "langchain-google-genai"),
    "mistral": ("langchain_mistralai", "langchain-mistralai"),
    "cohere": ("langchain_cohere", "langchain-cohere"),
    "ollama": ("langchain_ollama", "langchain-ollama"),
}

_CURATED_OLLAMA_MODELS: list[str] = [
    "deepseek-ocr:latest",
    "qwen3-vl:8b",
    "qwen2.5vl:7b",
    "glm-ocr:q8_0",
    "mistral:latest",
    "mistral-nemo:12b",
    "gemma4:12b",
    "gpt-oss:20b",
    "qwen3.5:9b",
]

_GPU_DL_PACKAGES: dict[str, str] = {
    "gpu:nvidia": "nvidia-ml-py",
    "gpu:amd": "pyamdgpuinfo",
}

# Importable module names for detection (pip package names differ for nvidia)
_GPU_MODULE_NAMES: dict[str, str] = {
    "gpu:nvidia": "pynvml",
    "gpu:amd": "pyamdgpuinfo",
}

# provider_name → importable module name (for installation-status checking)
_ALL_PROVIDER_MODULES: dict[str, str] = {
    name: module for name, (module, _) in _ALL_PROVIDER_PACKAGES.items()
}

_PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI  (langchain-openai)",
    "anthropic": "Anthropic  (langchain-anthropic)",
    "google": "Google Gemini  (langchain-google-genai)",
    "mistral": "Mistral  (langchain-mistralai)",
    "cohere": "Cohere  (langchain-cohere)",
    "ollama": "Ollama LangChain connector  (langchain-ollama)",
}


def _human_size(n: int) -> str:
    """Return *n* bytes as a human-readable string (e.g. ``'4.7 GB'``)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n //= 1024
    return str(n)  # unreachable but keeps type-checker happy


class _DetectInstalledThread(QThread):
    """Probes all downloadable items to determine which are already installed."""

    results = Signal(dict)  # key → bool

    def __init__(self, ollama_url: str) -> None:
        super().__init__()
        self._ollama_url = ollama_url

    def run(self) -> None:
        svc = ProcessingApplicationService()
        spacy_models = [model_id for _, model_id in _SPACY_MODEL_DOWNLOAD_CHOICES]
        status = svc.get_installation_status(
            provider_modules=_ALL_PROVIDER_MODULES,
            spacy_models=spacy_models,
            gpu_packages=_GPU_MODULE_NAMES,
            ollama_models=list(_CURATED_OLLAMA_MODELS),
            ollama_url=self._ollama_url,
        )
        result: dict[str, bool | int] = dict(status)
        result["spacy"] = svc.is_module_importable("spacy")
        # Add model size information so the UI can show a Size column.
        for model, size_bytes in svc.list_ollama_model_sizes(self._ollama_url).items():
            result[f"size:{model}"] = size_bytes
        self.results.emit(result)


class _PipInstallThread(QThread):
    """Installs pip packages in a background thread."""

    log_line = Signal(str)
    finished_ok = Signal(list)  # installed packages
    finished_err = Signal(str)

    def __init__(self, packages: list[str]) -> None:
        super().__init__()
        self._packages = packages

    def run(self) -> None:
        try:
            ProcessingApplicationService().install_packages(self._packages, self.log_line.emit)
            self.finished_ok.emit(self._packages)
        except Exception as exc:  # noqa: BLE001  # surface any pip/subprocess failure to the UI via the error signal
            self.finished_err.emit(str(exc))


class DownloadsDialog(QDialog):
    """Modal dialog to install OCR engines, LLM providers, and models.

    Items are grouped onto one tab per category; a shared log and a single
    install button operate across every tab. Pass the configured Ollama server
    *ollama_url* so model detection and pulls target the right host.
    """

    #: Emitted after a successful install round so callers can refresh model lists.
    packages_changed = Signal()

    #: Index of the Ollama Models tab within the tab widget.
    TAB_OLLAMA = 2
    #: Index of the spaCy tab within the tab widget.
    TAB_SPACY = 3

    def __init__(
        self,
        ollama_url: str = "",
        start_tab: int = 0,
        parent: QWidget | None = None,
        pull_manager: OllamaDownloadManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Downloads & Packages")
        self.setMinimumWidth(520)
        self.setMinimumHeight(520)
        self._ollama_url = ollama_url.strip() or DEFAULTS["ollama_url"]
        self._detect_thread: _DetectInstalledThread | None = None
        self._dl_active_thread: QThread | None = None
        self._detection_started = False
        self._dl_checkboxes: dict[str, QCheckBox] = {}
        self._tesseract_status_lbl: QLabel | None = None
        self._pull_manager = pull_manager  # owned by MainWindow; outlives this dialog
        self._pull_remaining: list[str] = []  # queued models waiting for the current pull

        main = QVBoxLayout(self)
        main.setSpacing(8)

        tabs = QTabWidget()
        main.addWidget(tabs, stretch=1)

        tabs.addTab(self._build_ocr_engines_tab(), "OCR Engines")
        tabs.addTab(self._build_providers_tab(), "LLM Providers")
        tabs.addTab(self._build_ollama_models_tab(), "Ollama Models")
        tabs.addTab(self._build_spacy_tab(), "spaCy")
        tabs.addTab(self._build_gpu_tab(), "GPU Add-ons")

        if start_tab:
            tabs.setCurrentIndex(start_tab)

        self._build_status_and_actions(main)

    def _build_status_and_actions(self, main: QVBoxLayout) -> None:
        """Build the detection label, progress bar, log, action row, and close button."""
        self._dl_detect_lbl = QLabel("Detecting installed packages…")
        self._dl_detect_lbl.setStyleSheet("color: #888; font-size: 11px;")
        main.addWidget(self._dl_detect_lbl)

        self._dl_progress = QProgressBar()
        self._dl_progress.setRange(0, 100)
        self._dl_progress.setTextVisible(False)
        self._dl_progress.setFixedHeight(10)
        self._dl_progress.hide()
        main.addWidget(self._dl_progress)

        self._dl_log = QPlainTextEdit()
        self._dl_log.setReadOnly(True)
        self._dl_log.setMaximumHeight(100)
        self._dl_log.setPlaceholderText("Install / download log will appear here…")
        main.addWidget(self._dl_log)

        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        self._dl_install_btn = QPushButton("Install / Download Selected")
        self._dl_install_btn.clicked.connect(self._on_download_install)
        self._dl_abort_btn = QPushButton("Abort")
        self._dl_abort_btn.setToolTip("Stop the current download after the current chunk.")
        self._dl_abort_btn.clicked.connect(self._on_abort)
        self._dl_abort_btn.hide()
        action_layout.addStretch(1)
        action_layout.addWidget(self._dl_abort_btn)
        action_layout.addWidget(self._dl_install_btn)
        main.addWidget(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)

    # ── tab builders ────────────────────────────────────────────────────────────

    @staticmethod
    def _scrolled(content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def _build_tesseract_binary_note(self) -> QWidget:
        """Return a platform-specific installation note for the Tesseract system binary."""
        import sys  # noqa: PLC0415

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._tesseract_status_lbl = QLabel("<b>Tesseract binary</b>  (system package — not pip)")
        layout.addWidget(self._tesseract_status_lbl)

        if sys.platform == "win32":
            note = QLabel("Download and run the UB Mannheim installer for Windows:")
            note.setWordWrap(True)
            layout.addWidget(note)
            btn = QPushButton("Open download page…")
            btn.setFixedWidth(180)
            btn.clicked.connect(
                lambda: __import__("webbrowser").open(
                    "https://github.com/UB-Mannheim/tesseract/wiki"
                )
            )
            layout.addWidget(btn)
        elif sys.platform == "darwin":
            cmd = QLabel("<tt>brew install tesseract</tt>")
            cmd.setTextInteractionFlags(
                cmd.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(cmd)
        else:
            cmd = QLabel(
                "<tt>sudo apt install tesseract-ocr</tt>  "
                "<span style='color:#888;font-size:9pt;'>"
                "(Debian/Ubuntu — use your distro's package manager)</span>"
            )
            cmd.setWordWrap(True)
            cmd.setTextInteractionFlags(
                cmd.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(cmd)

        return container

    def _build_ocr_engines_tab(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)
        group = QGroupBox("OCR Engines")
        group_layout = QVBoxLayout(group)

        group_layout.addWidget(self._build_tesseract_binary_note())
        group_layout.addSpacing(4)

        for key, lbl in [
            ("pytesseract", "pytesseract  (Python wrapper — pip install)"),
            ("paddleocr", "PaddleOCR  (paddleocr + paddlepaddle, ~400 MB)"),
        ]:
            cb = QCheckBox(lbl)
            self._dl_checkboxes[key] = cb
            group_layout.addWidget(cb)

        layout.addWidget(group)
        layout.addStretch(1)
        return self._scrolled(content)

    def _build_providers_tab(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)
        group = QGroupBox("LLM Provider Packages")
        group_layout = QVBoxLayout(group)
        for prov, (_, pip_pkg) in _ALL_PROVIDER_PACKAGES.items():
            lbl = _PROVIDER_LABELS.get(prov, f"{prov}  ({pip_pkg})")
            cb = QCheckBox(lbl)
            self._dl_checkboxes[f"provider:{prov}"] = cb
            group_layout.addWidget(cb)
        layout.addWidget(group)
        layout.addStretch(1)
        return self._scrolled(content)

    def _build_ollama_models_tab(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)
        group = QGroupBox("Ollama Models")
        group_layout = QVBoxLayout(group)
        group_layout.addWidget(QLabel("Select models to pull from the Ollama registry:"))
        self._ollama_model_list = QTreeWidget()
        self._ollama_model_list.setFixedHeight(200)
        self._ollama_model_list.setColumnCount(3)
        self._ollama_model_list.setHeaderLabels(["Model", "Installed", "Size"])
        hdr = self._ollama_model_list.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for model in _CURATED_OLLAMA_MODELS:
            item = QTreeWidgetItem([model, "—", "—"])
            item.setData(0, Qt.ItemDataRole.UserRole, model)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            self._ollama_model_list.addTopLevelItem(item)
        group_layout.addWidget(self._ollama_model_list)
        custom_row = QWidget()
        cmr = QHBoxLayout(custom_row)
        cmr.setContentsMargins(0, 0, 0, 0)
        self._ollama_custom_input = QLineEdit()
        self._ollama_custom_input.setPlaceholderText("Custom model tag, e.g. llama3.2:latest")
        add_model_btn = QPushButton("Add to list")
        add_model_btn.setFixedWidth(90)
        add_model_btn.clicked.connect(self._on_add_custom_ollama_model)
        cmr.addWidget(self._ollama_custom_input, stretch=1)
        cmr.addWidget(add_model_btn)
        group_layout.addWidget(custom_row)
        layout.addWidget(group)
        layout.addStretch(1)
        return self._scrolled(content)

    def _build_spacy_tab(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)

        install_group = QGroupBox("spaCy Installation")
        install_layout = QVBoxLayout(install_group)
        intro = QLabel(
            "spaCy is the NLP library used for anonymization. "
            "Install it first, then download a language model below."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaa; font-size: 9pt;")
        install_layout.addWidget(intro)
        cb_spacy = QCheckBox("spaCy  (spacy)")
        self._dl_checkboxes["spacy"] = cb_spacy
        install_layout.addWidget(cb_spacy)
        layout.addWidget(install_group)

        model_group = QGroupBox("spaCy Language Models  (for anonymization)")
        model_layout = QVBoxLayout(model_group)
        for lbl, model_id in _SPACY_MODEL_DOWNLOAD_CHOICES:
            cb = QCheckBox(lbl)
            self._dl_checkboxes[f"spacy:{model_id}"] = cb
            model_layout.addWidget(cb)
        layout.addWidget(model_group)
        layout.addStretch(1)
        return self._scrolled(content)

    def _build_gpu_tab(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)
        group = QGroupBox("GPU Monitoring Add-ons")
        group_layout = QVBoxLayout(group)
        for key, lbl in [
            ("gpu:nvidia", "NVIDIA monitoring  (nvidia-ml-py)"),
            ("gpu:amd", "AMD monitoring  (pyamdgpuinfo — Linux only)"),
        ]:
            cb = QCheckBox(lbl)
            self._dl_checkboxes[key] = cb
            group_layout.addWidget(cb)
        layout.addWidget(group)
        layout.addStretch(1)
        return self._scrolled(content)

    # ── detection ────────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._detection_started:
            self._detection_started = True
            self._start_detection()
        # If a pull is already running in the manager, subscribe to show current progress
        if self._pull_manager is not None and self._pull_manager.is_active:
            model = self._pull_manager.current_model
            self._dl_log.appendPlainText(f"(pull of {model!r} already in progress…)")
            self._dl_progress.setValue(0)
            self._dl_progress.show()
            self._pull_manager.progress.connect(self._on_ollama_pull_progress)
            self._pull_manager.finished.connect(self._on_manager_pull_done)
            self._pull_manager.error.connect(self._on_manager_pull_error)

    def _start_detection(self) -> None:
        if self._detect_thread is not None and self._detect_thread.isRunning():
            return
        self._dl_detect_lbl.setText("Detecting installed packages…")
        self._detect_thread = _DetectInstalledThread(self._ollama_url)
        self._detect_thread.results.connect(self._on_detection_done)
        self._detect_thread.start()

    def _on_detection_done(self, results: dict) -> None:
        self._dl_detect_lbl.setText("Detection complete.")

        tesseract_found = results.get("tesseract", False)
        suffix = "  <span style='color:#27ae60;'>✓ binary found</span>" if tesseract_found else ""
        if self._tesseract_status_lbl is not None:
            self._tesseract_status_lbl.setText(
                f"<b>Tesseract binary</b>  (system package — not pip){suffix}"
            )

        for key, cb in self._dl_checkboxes.items():
            installed = results.get(key, False)
            base = cb.text().split("  ✓")[0]
            if installed:
                cb.setText(f"{base}  ✓ Installed")
                cb.setChecked(True)
                cb.setEnabled(False)
            else:
                cb.setText(base)
                cb.setChecked(False)
                cb.setEnabled(True)

        for i in range(self._ollama_model_list.topLevelItemCount()):
            item = self._ollama_model_list.topLevelItem(i)
            if item is None:
                continue
            model_id = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0).split("  ✓")[0]
            installed = results.get(f"ollama:{model_id}", False)
            size_bytes: int = results.get(f"size:{model_id}", 0)
            size_str = _human_size(size_bytes) if (installed and size_bytes) else "—"
            item.setText(1, "✓" if installed else "—")
            item.setText(2, size_str)
            item.setData(0, Qt.ItemDataRole.UserRole, model_id)
            if installed:
                item.setCheckState(0, Qt.CheckState.Checked)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            else:
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )

    def _on_add_custom_ollama_model(self) -> None:
        model = self._ollama_custom_input.text().strip()
        if not model:
            return
        for i in range(self._ollama_model_list.topLevelItemCount()):
            existing = self._ollama_model_list.topLevelItem(i)
            if existing and existing.data(0, Qt.ItemDataRole.UserRole) == model:
                return
        item = QTreeWidgetItem([model, "—", "—"])
        item.setData(0, Qt.ItemDataRole.UserRole, model)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setCheckState(0, Qt.CheckState.Checked)
        self._ollama_model_list.addTopLevelItem(item)
        self._ollama_custom_input.clear()

    # ── install pipeline ─────────────────────────────────────────────────────────

    def _collect_pip_packages(self) -> list[str]:
        """Return pip package names for all checked, enabled pip-installable checkboxes."""
        packages: list[str] = []
        for key, pkgs in [
            ("spacy", ["spacy"]),
            ("pytesseract", ["pytesseract"]),
            ("paddleocr", ["paddlepaddle", "paddleocr"]),
        ]:
            cb = self._dl_checkboxes.get(key)
            if cb and cb.isChecked() and cb.isEnabled():
                packages.extend(pkgs)
        for provider, (_, pip_pkg) in _ALL_PROVIDER_PACKAGES.items():
            cb = self._dl_checkboxes.get(f"provider:{provider}")
            if cb and cb.isChecked() and cb.isEnabled():
                packages.append(pip_pkg)
        for key, pkg in _GPU_DL_PACKAGES.items():
            cb = self._dl_checkboxes.get(key)
            if cb and cb.isChecked() and cb.isEnabled():
                packages.append(pkg)
        return packages

    def _on_download_install(self) -> None:
        if self._dl_active_thread is not None and self._dl_active_thread.isRunning():
            return

        pip_packages = self._collect_pip_packages()
        spacy_models: list[str] = []
        ollama_models: list[str] = []

        for _, model_id in _SPACY_MODEL_DOWNLOAD_CHOICES:
            cb = self._dl_checkboxes.get(f"spacy:{model_id}")
            if cb and cb.isChecked() and cb.isEnabled():
                spacy_models.append(model_id)

        for i in range(self._ollama_model_list.topLevelItemCount()):
            item = self._ollama_model_list.topLevelItem(i)
            if (
                item
                and item.checkState(0) == Qt.CheckState.Checked
                and bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
            ):
                ollama_models.append(item.data(0, Qt.ItemDataRole.UserRole) or item.text(0))

        if not pip_packages and not spacy_models and not ollama_models:
            return

        self._dl_log.clear()
        self._dl_install_btn.setEnabled(False)

        if pip_packages:
            self._dl_log.appendPlainText(f"pip install {' '.join(pip_packages)}")
            thread = _PipInstallThread(pip_packages)
            thread.log_line.connect(self._dl_log_append)
            thread.finished_ok.connect(
                lambda _pkgs, sm=spacy_models, om=ollama_models: self._on_pip_done(sm, om)
            )
            thread.finished_err.connect(self._on_download_err)
            thread.start()
            self._dl_active_thread = thread
        elif spacy_models:
            self._run_spacy_downloads(spacy_models, ollama_models)
        else:
            self._run_ollama_pulls(ollama_models)

    def _on_pip_done(self, spacy_models: list, ollama_models: list) -> None:
        self._dl_log.appendPlainText("✓ pip install complete.")
        ProcessingApplicationService().inject_addon_packages_dir()
        if spacy_models:
            self._run_spacy_downloads(spacy_models, ollama_models)
        elif ollama_models:
            self._run_ollama_pulls(ollama_models)
        else:
            self._dl_install_btn.setEnabled(True)
            self.packages_changed.emit()
            self._start_detection()

    def _run_spacy_downloads(self, spacy_models: list, ollama_models: list) -> None:
        from ._addon_installer_dialog import _ModelDownloadThread  # noqa: PLC0415

        model = spacy_models[0]
        remaining = spacy_models[1:]
        self._dl_log.appendPlainText(f"Downloading spaCy model: {model}")
        target = ProcessingApplicationService().addon_packages_dir()
        thread = _ModelDownloadThread(model, target)
        thread.log_line.connect(self._dl_log_append)
        thread.finished_ok.connect(
            lambda sm=remaining, om=ollama_models: self._on_spacy_done(sm, om)
        )
        thread.finished_err.connect(self._on_download_err)
        thread.start()
        self._dl_active_thread = thread

    def _on_spacy_done(self, remaining: list, ollama_models: list) -> None:
        self._dl_log.appendPlainText("✓ spaCy model downloaded.")
        ProcessingApplicationService().inject_addon_packages_dir()
        if remaining:
            self._run_spacy_downloads(remaining, ollama_models)
        elif ollama_models:
            self._run_ollama_pulls(ollama_models)
        else:
            self._dl_install_btn.setEnabled(True)
            self.packages_changed.emit()
            self._start_detection()

    def _run_ollama_pulls(self, ollama_models: list) -> None:
        model = ollama_models[0]
        remaining = ollama_models[1:]
        self._dl_log.appendPlainText(f"Pulling Ollama model: {model}")
        self._dl_progress.setValue(0)
        self._dl_progress.show()
        self._dl_abort_btn.setEnabled(True)
        self._dl_abort_btn.show()

        if self._pull_manager is not None:
            # Delegate to the main-window-owned manager so pull survives dialog close
            self._pull_remaining = remaining
            self._pull_manager.progress.connect(self._on_ollama_pull_progress)
            self._pull_manager.finished.connect(self._on_manager_pull_done)
            self._pull_manager.error.connect(self._on_manager_pull_error)
            self._pull_manager.start(self._ollama_url, model)
        else:
            from ._addon_installer_dialog import _OllamaPullThread  # noqa: PLC0415

            thread = _OllamaPullThread(self._ollama_url, model)
            thread.progress.connect(self._on_ollama_pull_progress)
            thread.finished_ok.connect(lambda _name, rm=remaining: self._on_ollama_pull_done(rm))
            thread.finished_err.connect(self._on_download_err)
            thread.start()
            self._dl_active_thread = thread

    def _on_manager_pull_done(self, _model_name: str) -> None:
        # Only connected while a manager pull is active, so _pull_manager is set.
        if self._pull_manager is None:
            return
        self._pull_manager.progress.disconnect(self._on_ollama_pull_progress)
        self._pull_manager.finished.disconnect(self._on_manager_pull_done)
        self._pull_manager.error.disconnect(self._on_manager_pull_error)
        self._on_ollama_pull_done(self._pull_remaining)
        self._pull_remaining = []

    def _on_manager_pull_error(self, msg: str) -> None:
        # Only connected while a manager pull is active, so _pull_manager is set.
        if self._pull_manager is None:
            return
        self._pull_manager.progress.disconnect(self._on_ollama_pull_progress)
        self._pull_manager.finished.disconnect(self._on_manager_pull_done)
        self._pull_manager.error.disconnect(self._on_manager_pull_error)
        self._pull_remaining = []
        self._on_download_err(msg)

    def _on_ollama_pull_progress(self, status: str, completed: int, total: int) -> None:
        if total > 0:
            mb_done = completed / 1_048_576
            mb_total = total / 1_048_576
            self._dl_detect_lbl.setText(f"{status}: {mb_done:.1f} / {mb_total:.1f} MB")
            self._dl_progress.setValue(int(100 * completed / total))
        else:
            self._dl_detect_lbl.setText(status)

    def _on_ollama_pull_done(self, remaining: list) -> None:
        self._dl_log.appendPlainText("✓ Ollama model pulled.")
        self._dl_progress.hide()
        self._dl_abort_btn.hide()
        if remaining:
            self._run_ollama_pulls(remaining)
        else:
            self._dl_install_btn.setEnabled(True)
            self._dl_detect_lbl.setText("Done.")
            self.packages_changed.emit()
            self._start_detection()

    def _on_download_err(self, msg: str) -> None:
        self._dl_abort_btn.hide()
        self._dl_progress.hide()
        if msg == "aborted":
            self._dl_log.appendPlainText("Download aborted.")
            self._dl_detect_lbl.setText("Aborted.")
        else:
            self._dl_log.appendHtml(f'<span style="color:#c0392b;"><b>Error:</b> {msg}</span>')
        self._dl_install_btn.setEnabled(True)

    def _on_abort(self) -> None:
        from ._addon_installer_dialog import _OllamaPullThread  # noqa: PLC0415

        self._dl_abort_btn.setEnabled(False)
        if self._pull_manager is not None:
            self._pull_manager.abort()
        elif isinstance(self._dl_active_thread, _OllamaPullThread):
            # The abort button is only shown during Ollama pulls, so the active
            # thread is always the abortable pull thread here.
            self._dl_active_thread.request_abort()

    def _dl_log_append(self, line: str) -> None:
        self._dl_log.appendPlainText(line)
        sb = self._dl_log.verticalScrollBar()
        sb.setValue(sb.maximum())
