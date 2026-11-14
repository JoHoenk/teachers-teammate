"""Settings → Add-ons: install AI models for this computer, the reader, services and language models.

Each downloadable category lives on its own tab; a shared log and a single
"Install / Download Selected" button act across all tabs.  The "AI models for this computer"
tab is first: it groups the curated catalog into reading and writing models and ticks the
best models for the user's computer (see :mod:`teachers_teammate.domain.model_catalog`).

What happens here happens at once: Cancel does not undo an install.  Ollama pulls belong to
the main window's download manager and keep running when the window closes; a pip install or
a language-model download keeps the window open until it finished (it must make the new
package importable before anything else uses it).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
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

from ...application.service import ProcessingApplicationService
from ...config import DEFAULTS
from ...domain.local_models import READER_SIZE_LABEL
from ...domain.model_catalog import (
    HardwareProfile,
    ModelCategory,
    ModelRating,
    ModelUses,
    Suitability,
    best_model,
    find_spec,
    normalize_tag,
    suitability_rank,
)
from .. import _user_text as txt
from .._background import LatestTask
from .._constants import _SPACY_MODEL_CHOICES as _SPACY_MODEL_DOWNLOAD_CHOICES
from .._install_round import (
    PACKAGE_PREFIX,
    PAGE_MODELS_ITEM,
    PAGE_MODELS_PREFIX,
    READER_ITEM,
    READER_PREFIX,
    InstallRound,
)
from .._model_guidance import PROVIDER_PREFIX, SPACY_PREFIX
from .._model_wording import (
    ADD_MODEL_NOTE,
    BEST_ALREADY_DOWNLOADED,
    DOWNLOADS_INTRO,
    GROUP_OTHER,
    GROUP_READING,
    GROUP_WRITING,
    MODEL_ALREADY_LISTED,
    abilities_text,
    compact_suitability_text,
    memory_text,
    slow_text,
    suitability_text,
    too_large_confirmation,
    too_large_tooltip,
)
from ._page import (
    FOCUS_GPU,
    FOCUS_LOCAL_MODELS,
    FOCUS_NAME_DETECTION,
    FOCUS_ONLINE_SERVICES,
    FOCUS_READING_MODELS,
    SettingsContext,
    SettingsPage,
    SettingsPageBase,
)
from ._profile_line import ProfileLine

TITLE = "Add-ons"
DESCRIPTION = (
    "Download AI models for this computer and install optional parts. Installs start right "
    "away and take effect immediately."
)
TAB_LOCAL_MODELS = "AI models for this computer"
TAB_READING_MODELS = txt.TAB_READING_MODELS
TAB_ONLINE_SERVICES = "Online AI services"
TAB_NAME_DETECTION = "Name detection"
TAB_GPU = "Performance monitoring"
CLOSE_BLOCKED = (
    "An installation is still running. Please wait until it has finished — the new package "
    "must be set up before the app can use it."
)

# Tree group rows: group key → (label, catalog category or None for user models).
_OLLAMA_GROUPS: dict[str, tuple[str, ModelCategory | None]] = {
    "ocr": (GROUP_READING, "ocr"),
    "general": (GROUP_WRITING, "general"),
    "other": (GROUP_OTHER, None),
}

_GREY = QColor("#888888")

_GPU_DL_PACKAGES: dict[str, str] = {
    "gpu:nvidia": "nvidia-ml-py",
    "gpu:amd": "pyamdgpuinfo",
}

# Importable module names for detection (pip package names differ for nvidia)
_GPU_MODULE_NAMES: dict[str, str] = {
    "gpu:nvidia": "pynvml",
    "gpu:amd": "pyamdgpuinfo",
}

_PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI  (langchain-openai)",
    "anthropic": "Anthropic  (langchain-anthropic)",
    "google": "Google Gemini  (langchain-google-genai)",
    "mistral": "Mistral and Mistral (EU)  (langchain-mistralai)",
    "cohere": "Cohere  (langchain-cohere)",
    "ollama": "Ollama LangChain connector  (langchain-ollama)",
}


def _human_size(n: int) -> str:
    """Return *n* bytes as a human-readable decimal size (e.g. ``'4.7 GB'``).

    Decimal units, like the Ollama library page and the catalog sizes shown for
    models that are not downloaded yet — the Size column then reads consistently.
    """
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        size /= 1000
    return str(n)  # unreachable but keeps type-checker happy


def _detect_installed(
    svc: ProcessingApplicationService, ollama_url: str, models: list[str]
) -> dict[str, object]:
    """Probe every downloadable item and return what is installed (background task)."""
    spacy_models = [model_id for _, model_id in _SPACY_MODEL_DOWNLOAD_CHOICES]
    status = svc.get_installation_status(
        provider_modules={
            name: module for name, (module, _pip) in svc.list_provider_packages().items()
        },
        spacy_models=spacy_models,
        gpu_packages=_GPU_MODULE_NAMES,
        ollama_models=list(models),
        ollama_url=ollama_url,
    )
    result: dict[str, object] = dict(status)
    result["spacy"] = svc.is_module_importable("spacy")
    # Add model size information so the UI can show a Size column.
    for model, size_bytes in svc.list_ollama_model_sizes(ollama_url).items():
        result[f"size:{model}"] = size_bytes
    # Classify models here (the result is cached by the service) so the GUI thread
    # never blocks on show() requests; the dialog reads the cache afterwards.
    svc.classify_ollama_models(list(models), ollama_url)
    return result


class AddonsPage(SettingsPageBase):
    """Install AI models for this computer, handwriting engines, online services and more.

    Items are grouped onto one tab per category; a shared log and a single install button
    operate across every tab.  Model detection and pulls target the configured Ollama server
    (it follows *This computer*).
    """

    page = SettingsPage.ADDONS
    title = TITLE
    description = DESCRIPTION
    applies_immediately = True

    #: Emitted after a successful install round so callers can refresh model lists.
    packages_changed = Signal()

    #: Tab indexes within the tab widget (the local-models tab is first — what new users need).
    TAB_LOCAL_MODELS = 0
    TAB_ONLINE_SERVICES = 1
    TAB_NAME_DETECTION = 2
    TAB_READING_MODELS = 3
    TAB_GPU = 4

    _FOCUS_TABS: ClassVar[dict[str, int]] = {
        FOCUS_LOCAL_MODELS: TAB_LOCAL_MODELS,
        FOCUS_READING_MODELS: TAB_READING_MODELS,
        FOCUS_ONLINE_SERVICES: TAB_ONLINE_SERVICES,
        FOCUS_NAME_DETECTION: TAB_NAME_DETECTION,
        FOCUS_GPU: TAB_GPU,
    }

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        settings = context.settings
        self._ollama_url = str(settings.get("ollama_url") or "").strip() or DEFAULTS["ollama_url"]
        self._svc = context.app_service or ProcessingApplicationService()
        self._hardware_profile = str(settings.get("hardware_profile", DEFAULTS["hardware_profile"]))
        self._preselect: list[str] = []
        self._provider_packages = self._svc.list_provider_packages()
        # "provider:<name>" asks for an LLM provider package, not an Ollama model.
        self._preselect_providers: list[str] = []
        self._pull_connected = False  # subscribed to the pull manager's signals
        self._detect_task = LatestTask(self)
        self._detect_running = False
        self._detect_again = False  # a pull finished while a detection was running
        self._detection_started = False
        self._dl_checkboxes: dict[str, QCheckBox] = {}
        # The install round does the work; this page only shows it.
        self._round = InstallRound(self._svc, context.pull_manager, self)
        self._round.log_line.connect(self._dl_log_append)
        self._round.item_started.connect(self._on_round_item_started)
        self._round.pull_progress.connect(self._on_round_pull_progress)
        self._round.failed.connect(self._on_round_failed)
        self._round.finished.connect(self._on_round_finished)
        # Pre-ticking waits for BOTH the installed check and the hardware detection.
        self._installed_ready = False
        self._profile_ready = False
        self._initial_ticks_done = False
        self._installed_by_category: dict[ModelCategory, bool] = {"ocr": False, "general": False}
        self._auto_ticked: set[str] = set()  # ticked by the app, not by the user
        self._user_touched: set[str] = set()
        self._programmatic_change = False
        # Last known tick per row: QTreeWidget emits itemChanged for flag, colour
        # and text changes too, so a real tick change is detected by comparison.
        self._check_state: dict[str, Qt.CheckState] = {}
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._model_items: dict[str, QTreeWidgetItem] = {}  # tag → child row

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(8)

        self._tabs = QTabWidget()
        self._tabs.setMinimumHeight(460)
        main.addWidget(self._tabs, stretch=1)

        self._tabs.addTab(self._build_ollama_models_tab(), TAB_LOCAL_MODELS)
        self._tabs.addTab(self._build_providers_tab(), TAB_ONLINE_SERVICES)
        self._tabs.addTab(self._build_spacy_tab(), TAB_NAME_DETECTION)
        self._tabs.addTab(self._build_reading_models_tab(), TAB_READING_MODELS)
        self._tabs.addTab(self._build_gpu_tab(), TAB_GPU)

        self._build_status_and_actions(main)

        # Enter in a text field must only do what that field does.  A dialog would also
        # "press" its default button (the first push button, e.g. Install Selected).  The
        # window turns its own OK into a non-default button while this page is shown.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _build_status_and_actions(self, main: QVBoxLayout) -> None:
        """Build the detection label, progress bar, log and the action row."""
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

    # ── tab builders ────────────────────────────────────────────────────────────

    @staticmethod
    def _scrolled(content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def _build_reading_models_tab(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)
        group = QGroupBox(TAB_READING_MODELS)
        group_layout = QVBoxLayout(group)

        intro = QLabel(
            "The built-in reader reads pages on this computer, without a server. "
            f"{txt.OCR_ENGINE_HINTS['ppocr']} Its model files, and the page models of the "
            "automatic image preparation, come from PaddlePaddle (Apache-2.0) and are "
            "downloaded once, only when you ask for them."
        )
        intro.setWordWrap(True)
        group_layout.addWidget(intro)

        cb = QCheckBox(f"Built-in reader (PP-OCR) — {READER_SIZE_LABEL}")
        self._dl_checkboxes[READER_ITEM] = cb
        group_layout.addWidget(cb)

        page_models = QCheckBox(txt.PAGE_MODELS_LABEL)
        self._dl_checkboxes[PAGE_MODELS_ITEM] = page_models
        group_layout.addWidget(page_models)
        page_models_hint = QLabel(txt.PAGE_MODELS_HINT)
        page_models_hint.setWordWrap(True)
        page_models_hint.setStyleSheet("color: #888; font-size: 11px; margin-left: 20px;")
        group_layout.addWidget(page_models_hint)

        folder = QLabel(f"Saved in: {self._svc.models_dir()}")
        folder.setStyleSheet("color: #888; font-size: 11px;")
        folder.setWordWrap(True)
        folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        group_layout.addWidget(folder)

        layout.addWidget(group)
        layout.addStretch(1)
        return self._scrolled(content)

    def _build_providers_tab(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)
        group = QGroupBox("Online AI services")
        group_layout = QVBoxLayout(group)
        for prov, (_, pip_pkg) in self._provider_packages.items():
            lbl = _PROVIDER_LABELS.get(prov) or f"{self._provider_name(prov)}  ({pip_pkg})"
            cb = QCheckBox(lbl)
            self._dl_checkboxes[f"{PROVIDER_PREFIX}{prov}"] = cb
            group_layout.addWidget(cb)
        layout.addWidget(group)
        layout.addStretch(1)
        return self._scrolled(content)

    def _package_key(self, provider: str) -> str:
        """Return the checkbox key of the package *provider* needs.

        Providers that share a package (Mistral and Mistral EU) have one checkbox, listed
        under the first of them, so a request for the other one is mapped onto it.
        """
        if provider not in self._provider_packages:
            package = self._svc.get_provider_info(provider).get("package")
            pip = package.get("pip") if isinstance(package, dict) else None
            for name, (_module, listed_pip) in self._provider_packages.items():
                if pip is not None and listed_pip == pip:
                    provider = name
                    break
        return f"{PROVIDER_PREFIX}{provider}"

    def _provider_name(self, provider: str) -> str:
        """Return the display name of *provider* (its own name when it has none)."""
        return str(self._svc.get_provider_info(provider).get("display_name", provider))

    def _build_ollama_models_tab(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)
        group = QGroupBox("AI models for this computer (Ollama)")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(6)

        # The read-only "My computer" line; it is changed on the This computer page.
        self._profile_line = ProfileLine(
            app_service=self._svc, setting=self._hardware_profile, ollama_url=self._ollama_url
        )
        self._profile_line.change_requested.connect(lambda: self._navigate(SettingsPage.AI_SETUP))
        self._hardware_selector = self._profile_line.selector
        self._hardware_selector.resolved_changed.connect(self._on_profile_resolved)
        group_layout.addWidget(self._profile_line)

        intro = QLabel(DOWNLOADS_INTRO)
        intro.setWordWrap(True)
        group_layout.addWidget(intro)

        best_row = QWidget()
        br = QHBoxLayout(best_row)
        br.setContentsMargins(0, 0, 0, 0)
        self._select_best_btn = QPushButton("Select the best models for my computer")
        self._select_best_btn.clicked.connect(self._on_select_best_models)
        br.addWidget(self._select_best_btn)
        br.addStretch(1)
        self._ollama_hint_lbl = QLabel("")
        self._ollama_hint_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self._ollama_hint_lbl.setWordWrap(True)
        br.addWidget(self._ollama_hint_lbl, stretch=2)
        group_layout.addWidget(best_row)

        self._ollama_model_list = self._build_ollama_model_tree()
        group_layout.addWidget(self._ollama_model_list)

        custom_row = QWidget()
        cmr = QHBoxLayout(custom_row)
        cmr.setContentsMargins(0, 0, 0, 0)
        add_label = QLabel("Add a model by name:")
        self._ollama_custom_input = QLineEdit()
        self._ollama_custom_input.setPlaceholderText("e.g. llama3.2:latest")
        add_model_btn = QPushButton("Add")
        add_model_btn.setFixedWidth(90)
        add_model_btn.clicked.connect(self._on_add_custom_ollama_model)
        self._ollama_custom_input.returnPressed.connect(self._on_add_custom_ollama_model)
        cmr.addWidget(add_label)
        cmr.addWidget(self._ollama_custom_input, stretch=1)
        cmr.addWidget(add_model_btn)
        group_layout.addWidget(custom_row)
        add_note = QLabel(ADD_MODEL_NOTE)
        add_note.setStyleSheet("color: #888; font-size: 11px;")
        add_note.setWordWrap(True)
        group_layout.addWidget(add_note)
        layout.addWidget(group)
        layout.addStretch(1)
        return self._scrolled(content)

    def _build_ollama_model_tree(self) -> QTreeWidget:
        """Build the grouped model tree and populate it from the catalog."""
        tree = QTreeWidget()
        tree.setMinimumHeight(320)
        tree.setColumnCount(4)
        tree.setHeaderLabels(["Model", "Suitability", "Memory", "Size"])
        hdr = tree.header()
        # The model name is what the user needs most: it takes the free width and can
        # never shrink below a readable minimum, the other columns fit their content.
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setMinimumSectionSize(70)
        tree.setColumnWidth(0, 300)
        for col in (1, 2, 3):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for key, (label, _category) in _OLLAMA_GROUPS.items():
            group_item = QTreeWidgetItem([label])
            group_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable, not checkable
            tree.addTopLevelItem(group_item)
            group_item.setExpanded(True)
            self._group_items[key] = group_item
        # "Other models" stays hidden while empty.
        self._group_items["other"].setHidden(True)
        for spec in self._svc.ollama_catalog():
            self._add_model_row(spec.tag, "ocr" if spec.category == "ocr" else "general")
        tree.itemChanged.connect(self._on_model_item_changed)
        return tree

    def _add_model_row(self, tag: str, group_key: str) -> QTreeWidgetItem:
        """Add a checkable model row to the *group_key* group; returns the item."""
        tag = normalize_tag(tag)
        spec = find_spec(tag)
        group_item = self._group_items[group_key]
        self._check_state[tag] = Qt.CheckState.Unchecked  # before the item emits signals
        item = QTreeWidgetItem(group_item, [tag])
        item.setData(0, Qt.ItemDataRole.UserRole, tag)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        if spec is not None:
            item.setText(2, memory_text(spec.memory_gb))
            item.setText(3, f"{spec.download_gb:g} GB")
            item.setToolTip(0, spec.note)
        else:
            item.setText(2, "—")
            item.setText(3, "—")
            self._group_items["other"].setHidden(False)
        self._model_items[tag] = item
        return item

    def _iter_model_items(self) -> list[tuple[str, QTreeWidgetItem]]:
        """Return ``(tag, item)`` for every model row, walking the group children."""
        rows: list[tuple[str, QTreeWidgetItem]] = []
        for i in range(self._ollama_model_list.topLevelItemCount()):
            group_item = self._ollama_model_list.topLevelItem(i)
            if group_item is None:
                continue
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                if child is None:
                    continue
                tag = child.data(0, Qt.ItemDataRole.UserRole) or child.text(0)
                rows.append((str(tag), child))
        return rows

    def _group_category(self, item: QTreeWidgetItem) -> ModelCategory | None:
        """Return the catalog category of the group *item* belongs to."""
        for key, (_label, category) in _OLLAMA_GROUPS.items():
            if item.parent() is self._group_items.get(key):
                return category
        return None

    def _active_profile(self) -> HardwareProfile:
        """Return the currently active hardware profile ("8gb" until detection lands)."""
        detection = self._hardware_selector.resolved()
        if detection is not None:
            return detection.profile
        setting = self._hardware_selector.setting()
        return cast(HardwareProfile, setting if setting != "auto" else "8gb")

    def _best_model(self, category: ModelCategory, profile: HardwareProfile) -> str:
        """Return the BEST model of *category* on the active computer."""
        return best_model(
            category,
            profile,
            system_memory_gb=self._active_system_memory_gb(),
            card_gb=self._active_card_gb(),
        )

    def _active_system_memory_gb(self) -> float | None:
        """Return the computer's memory in GB when known (it rates models too big for the card)."""
        detection = self._hardware_selector.resolved()
        return detection.system_memory_gb if detection is not None else None

    def _active_card_gb(self) -> float | None:
        """Return the graphics memory models can use when it is known, else ``None``."""
        detection = self._hardware_selector.resolved()
        return detection.card_gb if detection is not None else None

    # ── ticking ────────────────────────────────────────────────────────────

    def _set_checked(self, item: QTreeWidgetItem, checked: bool) -> None:
        """Change a row's tick on behalf of the app (not recorded as a user choice)."""
        self._programmatic_change = True
        try:
            item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        finally:
            self._programmatic_change = False

    def _on_model_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Remember rows the user ticked or unticked so automatic ticks leave them alone."""
        if column != 0:
            return
        tag = str(item.data(0, Qt.ItemDataRole.UserRole) or item.text(0))
        state = item.checkState(0)
        if self._check_state.get(tag) == state:
            return  # flags, colours or text changed — not the tick
        self._check_state[tag] = state
        if self._programmatic_change:
            return
        self._user_touched.add(tag)
        self._auto_ticked.discard(tag)

    def _tick_by_user_intent(self, tag: str) -> None:
        """Tick *tag* because the user asked for it (button or preselect), if possible."""
        item = self._model_items.get(tag)
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        self._set_checked(item, True)
        self._user_touched.add(tag)
        self._auto_ticked.discard(tag)

    def _apply_auto_ticks(self) -> None:
        """Tick the BEST model of each category that has nothing downloaded yet (AC14).

        Earlier automatic ticks the user did not touch are replaced, so a change
        of "My computer" moves the suggestion.  Ticked rows are never downloaded
        without the user clicking *Install / Download Selected*.
        """
        for tag in list(self._auto_ticked):
            item = self._model_items.get(tag)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsEnabled:
                self._set_checked(item, False)
        self._auto_ticked.clear()
        profile = self._active_profile()
        for category, downloaded in self._installed_by_category.items():
            if downloaded:
                continue
            tag = self._best_model(category, profile)
            item = self._model_items.get(tag)
            if item is None or tag in self._user_touched:
                continue
            if not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
                continue
            self._set_checked(item, True)
            self._auto_ticked.add(tag)

    def _maybe_apply_initial_ticks(self) -> None:
        """Run the first automatic ticking once both detections have finished."""
        if self._initial_ticks_done or not (self._installed_ready and self._profile_ready):
            return
        self._initial_ticks_done = True
        self._apply_auto_ticks()

    def _on_profile_resolved(self, _detection: object) -> None:
        """The computer type became known or changed: re-rate rows, adjust auto ticks."""
        self._profile_ready = True
        self._update_model_suitability()
        if self._initial_ticks_done:
            self._apply_auto_ticks()
        else:
            self._maybe_apply_initial_ticks()

    def _update_model_suitability(self) -> None:
        """Re-compute suitability labels, sorting and colours for the active profile."""
        profile = self._active_profile()
        memory_gb = self._active_system_memory_gb()
        card_gb = self._active_card_gb()
        for key, (_label, category) in _OLLAMA_GROUPS.items():
            group_item = self._group_items[key]
            children: list[QTreeWidgetItem] = []
            while group_item.childCount():
                if (child := group_item.takeChild(0)) is not None:
                    children.append(child)
            tags = [str(c.data(0, Qt.ItemDataRole.UserRole) or c.text(0)) for c in children]
            ratings: dict[str, ModelRating] = {}
            abilities: dict[str, ModelUses | None] = {}
            # Cache lookups only: the detection task classified the models.
            if category is not None and tags:
                ratings = {
                    r.tag: r
                    for r in self._svc.rate_ollama_models(
                        tags,
                        category,
                        profile,
                        self._ollama_url,
                        cached_only=True,
                        system_memory_gb=memory_gb,
                        card_gb=card_gb,
                    )
                }
            elif tags:
                abilities = self._svc.classify_ollama_models(
                    tags, self._ollama_url, cached_only=True
                )
            decorated: list[tuple[int, str, QTreeWidgetItem, Suitability, float | None, bool]] = []
            for tag, child in zip(tags, children, strict=True):
                rating = ratings.get(tag)
                value = rating.suitability if rating is not None else Suitability.UNKNOWN
                memory = rating.memory_gb if rating is not None else None
                slowly = rating.very_slow if rating is not None else False
                decorated.append((suitability_rank(value), tag, child, value, memory, slowly))
            decorated.sort(key=lambda entry: (entry[0], entry[1]))
            for _rank, tag, child, value, memory, very_slow in decorated:
                group_item.addChild(child)
                rating = ratings.get(tag)
                expected = rating.speed if rating is not None else None
                if category is not None:
                    label = compact_suitability_text(value, category, very_slow=very_slow)
                    if expected is not None:
                        label = f"{label} · {txt.speed_expected_label(expected)}"
                    child.setText(1, label)
                else:
                    # Models outside the catalog: say what they can do, not how good.
                    child.setText(1, abilities_text(abilities.get(tag)))
                if value is Suitability.TOO_LARGE:
                    child.setForeground(0, _GREY)
                    child.setForeground(1, _GREY)
                    child.setToolTip(
                        1,
                        too_large_tooltip(
                            memory, profile, system_memory_gb=memory_gb, card_gb=card_gb
                        ),
                    )
                elif value is Suitability.SLOW:
                    child.setForeground(0, QBrush())
                    child.setForeground(1, QBrush())
                    child.setToolTip(
                        1, slow_text(memory, profile, card_gb=card_gb, very_slow=very_slow)
                    )
                else:
                    # An empty QBrush (no brush) restores the theme's text colour.  A
                    # QColor() would be an *invalid* solid colour, painted black: unreadable
                    # on the dark theme (text and tick box of every non-"too large" row).
                    child.setForeground(0, QBrush())
                    child.setForeground(1, QBrush())
                    # The column shows a compact label; the tooltip has the full wording.
                    note = (
                        suitability_text(value, category, very_slow=very_slow)
                        if category is not None
                        else ""
                    )
                    if expected is not None:
                        note = f"{note}\n{txt.speed_expected_note(expected)}"
                    child.setToolTip(1, note)

    def _on_select_best_models(self) -> None:
        """Tick the BEST reading and writing models that are not downloaded yet."""
        profile = self._active_profile()
        found = 0
        for tag in (self._best_model("ocr", profile), self._best_model("general", profile)):
            item = self._model_items.get(tag)
            if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
                continue  # not listed, or already downloaded
            self._tick_by_user_intent(tag)
            found += 1
        self._ollama_hint_lbl.setText("" if found else BEST_ALREADY_DOWNLOADED)

    def _build_spacy_tab(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)

        install_group = QGroupBox("Name detection")
        install_layout = QVBoxLayout(install_group)
        intro = QLabel(
            "spaCy is the library that finds names and other personal information. "
            "Install it first, then download a name detection model below."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaa; font-size: 9pt;")
        install_layout.addWidget(intro)
        cb_spacy = QCheckBox("spaCy  (spacy)")
        self._dl_checkboxes["spacy"] = cb_spacy
        install_layout.addWidget(cb_spacy)
        layout.addWidget(install_group)

        model_group = QGroupBox("Name detection models")
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
        group = QGroupBox("Performance monitoring")
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

    # ── page protocol ────────────────────────────────────────────────────────────

    def on_shown(self) -> None:
        """Detect what is installed (first time), and follow a pull that is already running."""
        if not self._detection_started:
            self._detection_started = True
            self._start_detection()
        # If a pull is already running in the manager, follow it to show current progress
        model = self._round.follow_running_pull()
        if model:
            self._dl_log.appendPlainText(f"(pull of {model!r} already in progress…)")
            self._show_pull_progress()

    def focus(self, focus: str, preselect: Sequence[str] = ()) -> None:
        """Show the tab named *focus*; tick *preselect*.

        Entries are model tags, ``provider:<name>`` (an online service's package) and
        ``spacy:<model>`` (a name-detection model; spaCy itself is ticked with it while it is
        not installed).
        """
        spacy = [tag for tag in preselect if tag.startswith(SPACY_PREFIX)]
        reader = [tag for tag in preselect if tag.startswith(READER_PREFIX)]
        page_models = [tag for tag in preselect if tag.startswith(PAGE_MODELS_PREFIX)]
        tags = [
            tag
            for tag in preselect
            if tag
            and not tag.startswith(
                (PROVIDER_PREFIX, SPACY_PREFIX, READER_PREFIX, PAGE_MODELS_PREFIX)
            )
        ]
        providers = [
            self._package_key(tag.removeprefix(PROVIDER_PREFIX))
            for tag in preselect
            if tag.startswith(PROVIDER_PREFIX)
        ]
        tab = self._FOCUS_TABS.get(focus)
        if providers:
            tab = self.TAB_ONLINE_SERVICES
        elif reader or page_models:
            tab = self.TAB_READING_MODELS
        elif tags and tab is None:
            tab = self.TAB_LOCAL_MODELS
        if tab is not None:
            self._tabs.setCurrentIndex(tab)
        self._preselect.extend(tags)
        self._preselect_providers.extend(providers)
        if spacy:
            # A box that is already installed is disabled and stays as it is.
            self._preselect_providers.extend(["spacy", *spacy])
        if reader:
            self._preselect_providers.append(READER_ITEM)
        if page_models:
            self._preselect_providers.append(PAGE_MODELS_ITEM)
        if self._installed_ready and (tags or providers or spacy or reader or page_models):
            # Detection is done: tick now (otherwise it ticks once detection finished).
            self._tick_preselect_tags()

    def set_hardware_setting(self, setting: str) -> None:
        self._hardware_selector.set_setting(setting)

    def set_ollama_url(self, url: str) -> None:
        new_url = url.strip() or DEFAULTS["ollama_url"]
        if new_url == self._ollama_url:
            return
        self._ollama_url = new_url
        self._hardware_selector.set_ollama_url(new_url)
        if self._detection_started:
            self._start_detection()

    def close_blocker(self) -> str:
        """A pip install or language-model download must finish before the window closes."""
        if self._round.busy():
            return CLOSE_BLOCKED
        return ""

    def shutdown(self) -> None:
        """Stop following the pull manager; a running pull keeps going in the main window."""
        self._round.detach()

    def _start_detection(self) -> None:
        if self._detect_running:
            # Something changed while a check was running (e.g. a pull finished):
            # the running check may predate it, so check once more afterwards.
            self._detect_again = True
            return
        self._detect_running = True
        self._dl_detect_lbl.setText("Detecting installed packages…")
        svc = self._svc
        url = self._ollama_url
        models = [tag for tag, _item in self._iter_model_items()]
        self._detect_task.submit(
            lambda: _detect_installed(svc, url, models), self._on_detect_finished
        )

    def _on_detect_finished(self, result: object, error: BaseException | None) -> None:
        self._detect_running = False
        if error is not None or not isinstance(result, dict):
            self._dl_detect_lbl.setText(f"Detection failed: {error or 'unexpected result'}")
            # Nothing is known to be installed, but the suggested ticks and the
            # requested preselection must not depend on this check (ticking never
            # downloads anything — that needs a click on Install).
            self._installed_ready = True
            self._maybe_apply_initial_ticks()
            self._tick_preselect_tags()
        else:
            self._on_detection_done(result)
        if self._detect_again:
            self._detect_again = False
            self._start_detection()

    def _on_detection_done(self, results: dict) -> None:
        self._dl_detect_lbl.setText("Detection complete.")

        for key, cb in self._dl_checkboxes.items():
            installed = results.get(key, False)
            base = cb.text().split("  ✓")[0]
            if installed:
                cb.setText(
                    f"{base}  "
                    f"{txt.READER_DOWNLOADED if key in (READER_ITEM, PAGE_MODELS_ITEM) else '✓ Installed'}"
                )
                cb.setChecked(True)
                cb.setEnabled(False)
            else:
                cb.setText(base)
                cb.setChecked(False)
                cb.setEnabled(True)

        self._installed_by_category = self._apply_model_detection_results(results)
        self._update_model_suitability()
        self._installed_ready = True
        self._maybe_apply_initial_ticks()
        self._tick_preselect_tags()

    def _apply_model_detection_results(self, results: dict) -> dict[ModelCategory, bool]:
        """Mark model rows downloaded/disabled; return per-category download flags."""
        installed_by_category: dict[ModelCategory, bool] = {"ocr": False, "general": False}
        for tag, item in self._iter_model_items():
            installed = bool(results.get(f"ollama:{tag}", False))
            size_bytes = int(results.get(f"size:{tag}", 0))
            if installed:
                # Downloaded rows are marked in the Size column: "✓ 4.7 GB".
                item.setText(3, f"✓ {_human_size(size_bytes)}" if size_bytes else "✓")
                self._set_checked(item, True)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                self._auto_ticked.discard(tag)
                category = self._group_category(item)
                if find_spec(tag) is not None and category is not None:
                    installed_by_category[category] = True
            else:
                # Not downloaded (any more): the Size column shows the catalog download size.
                spec = find_spec(tag)
                item.setText(3, f"{spec.download_gb:g} GB" if spec is not None else "—")
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
        return installed_by_category

    def _tick_preselect_providers(self) -> None:
        """Tick the requested provider packages that are not installed yet."""
        for key in self._preselect_providers:
            cb = self._dl_checkboxes.get(key)
            if cb is not None and cb.isEnabled():
                cb.setChecked(True)
        self._preselect_providers = []

    def _tick_preselect_tags(self) -> None:
        """Tick requested preselect tags (adding unknown ones to \"Other models\")."""
        self._tick_preselect_providers()
        for raw in self._preselect:
            tag = normalize_tag(raw)
            if tag not in self._model_items:
                self._add_model_row(tag, "other")
            self._tick_by_user_intent(tag)
        if self._preselect:
            self._update_model_suitability()
        self._preselect = []

    def _on_add_custom_ollama_model(self) -> None:
        model = normalize_tag(self._ollama_custom_input.text())
        if not model:
            return
        existing = self._model_items.get(model)
        if existing is not None:
            # Duplicates (also "name" vs "name:latest") get no second row: show the
            # existing one, tick it when it can still be downloaded, and say so.
            self._tick_by_user_intent(model)
            self._ollama_model_list.scrollToItem(existing)
            self._ollama_model_list.setCurrentItem(existing)
            self._ollama_hint_lbl.setText(MODEL_ALREADY_LISTED)
            self._ollama_custom_input.clear()
            return
        self._add_model_row(model, "other")
        self._tick_by_user_intent(model)
        self._ollama_custom_input.clear()
        self._update_model_suitability()

    # ── install pipeline ─────────────────────────────────────────────────────────

    def _collect_pip_packages(self) -> list[str]:
        """Return pip package names for all checked, enabled pip-installable checkboxes."""
        packages: list[str] = []
        for key, pkgs in [
            ("spacy", ["spacy"]),
        ]:
            cb = self._dl_checkboxes.get(key)
            if cb and cb.isChecked() and cb.isEnabled():
                packages.extend(pkgs)
        for provider, (_, pip_pkg) in self._provider_packages.items():
            cb = self._dl_checkboxes.get(f"{PROVIDER_PREFIX}{provider}")
            if cb and cb.isChecked() and cb.isEnabled():
                packages.append(pip_pkg)
        for key, pkg in _GPU_DL_PACKAGES.items():
            cb = self._dl_checkboxes.get(key)
            if cb and cb.isChecked() and cb.isEnabled():
                packages.append(pkg)
        return packages

    def _on_download_install(self) -> None:
        if self._round.running():
            return

        pip_packages = self._collect_pip_packages()
        spacy_models: list[str] = []
        ollama_models: list[str] = []

        for _, model_id in _SPACY_MODEL_DOWNLOAD_CHOICES:
            cb = self._dl_checkboxes.get(f"spacy:{model_id}")
            if cb and cb.isChecked() and cb.isEnabled():
                spacy_models.append(model_id)

        for tag, item in self._iter_model_items():
            if item.checkState(0) == Qt.CheckState.Checked and bool(
                item.flags() & Qt.ItemFlag.ItemIsEnabled
            ):
                ollama_models.append(tag)

        reader_cb = self._dl_checkboxes.get(READER_ITEM)
        reader = bool(reader_cb and reader_cb.isChecked() and reader_cb.isEnabled())

        page_models_cb = self._dl_checkboxes.get(PAGE_MODELS_ITEM)
        page_models = bool(
            page_models_cb and page_models_cb.isChecked() and page_models_cb.isEnabled()
        )

        if (
            not pip_packages
            and not spacy_models
            and not ollama_models
            and not reader
            and not page_models
        ):
            return

        if ollama_models and not self._confirm_too_large(ollama_models):
            return

        self._dl_log.clear()
        self._dl_install_btn.setEnabled(False)
        self._round.start(
            packages=pip_packages,
            spacy_models=spacy_models,
            reader=reader,
            page_models=page_models,
            ollama_models=ollama_models,
            ollama_url=self._ollama_url,
        )

    def _confirm_too_large(self, ollama_models: list[str]) -> bool:
        """Ask for confirmation before pulling models that exceed the profile budget.

        Returns True when the user chose "Download anyway" (or nothing is too large).
        """
        profile = self._active_profile()
        messages: list[str] = []
        for tag in ollama_models:
            item = self._model_items.get(tag)
            category = self._group_category(item) if item is not None else None
            if category is None:
                continue
            rating = self._svc.rate_ollama_models(
                [tag],
                category,
                profile,
                self._ollama_url,
                cached_only=True,
                system_memory_gb=self._active_system_memory_gb(),
                card_gb=self._active_card_gb(),
            )[0]
            if rating.suitability is Suitability.TOO_LARGE:
                messages.append(
                    too_large_confirmation(
                        tag,
                        rating.memory_gb,
                        profile,
                        system_memory_gb=self._active_system_memory_gb(),
                        card_gb=self._active_card_gb(),
                    )
                )
        if not messages:
            return True
        box = QMessageBox(self.window())
        box.setWindowTitle("Model does not fit")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("\n\n".join(messages) + "\n\nDownload anyway?")
        download_btn = box.addButton("Download anyway", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        return box.clickedButton() == download_btn

    # ── following the install round ──────────────────────────────────────────────

    def _on_round_item_started(self, item: str) -> None:
        """Show the progress bar and Abort for an Ollama pull; pip and spaCy have no bar."""
        if item.startswith((PACKAGE_PREFIX, SPACY_PREFIX)):
            return
        self._show_pull_progress()

    def _show_pull_progress(self) -> None:
        self._dl_progress.setValue(0)
        self._dl_progress.show()
        self._dl_abort_btn.setEnabled(True)
        self._dl_abort_btn.show()

    def _on_round_pull_progress(self, status: str, completed: int, total: int) -> None:
        if total > 0:
            mb_done = completed / 1_048_576
            mb_total = total / 1_048_576
            self._dl_detect_lbl.setText(f"{status}: {mb_done:.1f} / {mb_total:.1f} MB")
            self._dl_progress.setValue(int(100 * completed / total))
        else:
            self._dl_detect_lbl.setText(status)

    def _on_round_failed(self, _item: str, message: str) -> None:
        if message == "aborted":
            self._dl_log.appendPlainText("Download aborted.")
            self._dl_detect_lbl.setText("Aborted.")
        else:
            self._dl_log.appendHtml(f'<span style="color:#c0392b;"><b>Error:</b> {message}</span>')

    def _on_round_finished(self, installed_any: bool) -> None:
        self._dl_progress.hide()
        self._dl_abort_btn.hide()
        self._dl_install_btn.setEnabled(True)
        if not installed_any:
            return
        # Something arrived: the installed list and the classifications are stale.
        self._dl_detect_lbl.setText("Done.")
        self.packages_changed.emit()
        self._start_detection()

    def _on_abort(self) -> None:
        self._dl_abort_btn.setEnabled(False)
        self._round.abort()

    def _dl_log_append(self, line: str) -> None:
        self._dl_log.appendPlainText(line)
        sb = self._dl_log.verticalScrollBar()
        sb.setValue(sb.maximum())
