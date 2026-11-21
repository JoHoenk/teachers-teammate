"""The *AI models for this computer* tab: the model tree, its ticks and the pull marks.

The tree groups the curated catalog (:mod:`teachers_teammate.domain.model_catalog`) into reading
and writing models, plus "Other models" for anything the teacher adds by name.  Two facts decide
what a row says and what is ticked in advance: what the computer can do (the read-only line at
the top, changed on *This computer*) and what is downloaded already (the page's installation
check, reported through :meth:`OllamaModelsTab.mark_installed`).

Ticking never downloads anything: a pull needs the page's install button.  The pulls themselves
belong to the main window's download manager, which outlives this tab, so the tab only follows
it (:meth:`OllamaModelsTab.mark_pulls`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ....application.composition import AppServices
from ....domain.model_catalog import (
    HardwareProfile,
    ModelCategory,
    ModelRating,
    ModelUses,
    OllamaModelSpec,
    Suitability,
    best_model,
    find_spec,
    normalize_tag,
    suitability_rank,
)
from ....texts import dialogs as dlg
from ... import _dialogs
from ... import _user_text as txt
from ..._model_wording import (
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
from .._computer_box import ProfileLine
from ._common import is_model_tag

# Tree group rows: group key → (label, catalog category or None for user models).
_OLLAMA_GROUPS: dict[str, tuple[str, ModelCategory | None]] = {
    "ocr": (GROUP_READING, "ocr"),
    "general": (GROUP_WRITING, "general"),
    "other": (GROUP_OTHER, None),
}

_GREY = QColor("#888888")


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


def _model_tooltip(spec: OllamaModelSpec) -> str:
    """Return the tooltip of a model row: what it is for, and the terms it comes under."""
    if spec.licence is None:
        return spec.note
    return f"{spec.note}\n\nLicence: {spec.licence.name} — {spec.licence.url}"


class OllamaModelsTab(QWidget):
    """The catalog's models and the teacher's own, rated for the active computer."""

    #: The "change…" link of the read-only computer line was clicked.
    profile_change_requested = Signal()

    def __init__(
        self,
        *,
        app_service: AppServices,
        hardware_setting: str,
        ollama_url: str,
        pull_manager: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._svc = app_service
        self._ollama_url = ollama_url
        # Owned by the main window and outliving this tab: the tab only marks its pulls.
        self._pull_manager = pull_manager
        self._init_state()
        self._build_body(hardware_setting)

    def _init_state(self) -> None:
        """Set up the tab's own state: the ticks, the rows and the group rows."""
        #: Ticked by a deep link (a fix button) rather than by the user.
        self._preselect: list[str] = []
        #: Pre-ticking waits for BOTH the installed check and the hardware detection.
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

    def _build_body(self, hardware_setting: str) -> None:
        """Build the computer line, the tree and the fields for the teacher's own models."""
        layout = QVBoxLayout(self)
        group = QGroupBox("AI models for this computer (Ollama)")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(6)

        # The read-only "My computer" line; it is changed on the This computer page.
        self._profile_line = ProfileLine(
            app_service=self._svc, setting=hardware_setting, ollama_url=self._ollama_url
        )
        self._profile_line.change_requested.connect(self.profile_change_requested.emit)
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
        group_layout.addWidget(self._ollama_model_list, stretch=1)

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
        layout.addWidget(group, stretch=1)

    # ── what the page asks of this tab ───────────────────────────────────────────

    def all_tags(self) -> list[str]:
        """Return the tag of every row, in the order the tree shows them."""
        return [tag for tag, _item in self._iter_model_items()]

    def checked_models(self) -> list[str]:
        """Return the tags the teacher ticked for download."""
        return [
            tag
            for tag, item in self._iter_model_items()
            if item.checkState(0) == Qt.CheckState.Checked
            and bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
        ]

    def preselect(self, entries: Sequence[str]) -> None:
        """Remember the model tags a deep link (a fix button) asks to tick.

        They are ticked once the installation check has answered: the check re-rates every row
        and adds the models the server offers but the tree does not list yet.
        """
        self._preselect.extend(entry for entry in entries if is_model_tag(entry))

    def tick_preselect(self) -> None:
        """Tick the requested tags, now that the check knows what is installed."""
        for raw in self._preselect:
            tag = normalize_tag(raw)
            if tag not in self._model_items:
                self._add_model_row(tag, "other")
            self._tick_by_user_intent(tag)
        if self._preselect:
            self._update_model_suitability()
        self._preselect = []

    def mark_installed(self, results: Mapping[str, object]) -> None:
        """Mark the rows that are downloaded and rate the rows for the active computer."""
        self._installed_by_category = self._apply_model_detection_results(results)
        self._update_model_suitability()
        self._installed_ready = True
        self._maybe_apply_initial_ticks()

    def mark_check_failed(self) -> None:
        """The check got no answer: tick the best models as if nothing were downloaded.

        Ticking never downloads anything (that needs the page's install button), so a wrong
        guess here costs nothing.
        """
        self._installed_ready = True
        self._maybe_apply_initial_ticks()

    def on_pull_progress(self, _status: str, completed: int, total: int) -> None:
        """Show how far the pull the main window is running has come, in the Size column."""
        if total > 0:
            self.mark_pulls(int(100 * completed / total))

    def mark_pulls(self, percent: int | None = None) -> None:
        """Show in the Size column which models the main window is downloading or has queued.

        The downloads go on after the page is closed, so a page opened later has to show them
        again: the model that is coming now says how far it is, the ones behind it say "queued".
        """
        manager = self._pull_manager
        if manager is None or not manager.is_active:
            return
        current = manager.current_model
        queued = set(manager.queued)
        for tag, item in self._iter_model_items():
            if tag == current:
                item.setText(
                    3, f"downloading… {percent}%" if percent is not None else "downloading…"
                )
            elif tag in queued:
                item.setText(3, "queued")

    def confirm_too_large(self, ollama_models: list[str]) -> bool:
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
            rating = self._svc.recommendations.rate_ollama_models(
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
        return _dialogs.ask(self.window(), dlg.model_does_not_fit(messages))

    def set_hardware_setting(self, setting: str) -> None:
        """Follow the computer profile chosen on *This computer*."""
        self._hardware_selector.set_setting(setting)

    def set_ollama_url(self, url: str) -> None:
        """Follow the local AI server address chosen on *This computer*."""
        self._ollama_url = url
        self._hardware_selector.set_ollama_url(url)

    # ── the tree ─────────────────────────────────────────────────────────────────

    def _build_ollama_model_tree(self) -> QTreeWidget:
        """Build the grouped model tree and populate it from the catalog."""
        tree = QTreeWidget()
        tree.setMinimumHeight(160)
        tree.setColumnCount(5)
        tree.setHeaderLabels(["Model", "Suitability", "Memory", "Download size", "License"])
        header_item = tree.headerItem()
        if header_item is not None:
            header_item.setToolTip(
                3,
                "How much has to be downloaded for this model. A model that is already on "
                "this computer shows ✓ and the space it takes. While a model downloads the "
                "column shows how far it has come.",
            )
        hdr = tree.header()
        # The model name is what the user needs most: it takes the free width and can
        # never shrink below a readable minimum, the other columns fit their content.
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setMinimumSectionSize(70)
        tree.setColumnWidth(0, 300)
        for col in (1, 2, 3, 4):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for key, (label, _category) in _OLLAMA_GROUPS.items():
            group_item = QTreeWidgetItem([label])
            group_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable, not checkable
            tree.addTopLevelItem(group_item)
            group_item.setExpanded(True)
            self._group_items[key] = group_item
        # "Other models" stays hidden while empty.
        self._group_items["other"].setHidden(True)
        for spec in self._svc.recommendations.ollama_catalog():
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
            item.setToolTip(0, _model_tooltip(spec))
            if spec.licence is not None and spec.licence.note:
                # Only a licence that restricts use gets a word in the row; the terms of every
                # model are in the name's tooltip.
                item.setText(4, spec.licence.name)
                item.setToolTip(4, f"{spec.licence.note}\n{spec.licence.url}")
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

    # ── the computer the rows are rated for ──────────────────────────────────────

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
                    for r in self._svc.recommendations.rate_ollama_models(
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
                abilities = self._svc.recommendations.classify_ollama_models(
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

    # ── following the installation check and the pulls ───────────────────────────

    def _apply_model_detection_results(
        self, results: Mapping[str, object]
    ) -> dict[ModelCategory, bool]:
        """Mark model rows downloaded/disabled; return per-category download flags."""
        installed_by_category: dict[ModelCategory, bool] = {"ocr": False, "general": False}
        for tag, item in self._iter_model_items():
            installed = bool(results.get(f"ollama:{tag}", False))
            size_bytes = cast(int, results.get(f"size:{tag}", 0))
            if installed:
                # Downloaded rows are marked in the Size column: "✓ 4.7 GB".  Unticked and
                # disabled, like the package boxes: a tick always means "will be installed".
                item.setText(3, f"✓ {_human_size(size_bytes)}" if size_bytes else "✓")
                self._set_checked(item, False)
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
        self.mark_pulls()
        return installed_by_category
