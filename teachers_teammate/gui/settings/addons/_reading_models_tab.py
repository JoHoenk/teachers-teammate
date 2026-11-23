"""The *Reading models* tab: the built-in reader's model files and the page models'.

Both sets come from PaddlePaddle (Apache-2.0) and are downloaded into the model folder on
request; the tab offers the two boxes, says where the files end up, and nothing else.  What an
item is worth downloading is decided by the install round
(:mod:`teachers_teammate.gui._install_round`), not here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget

from ....application.composition import AppServices
from ....domain.install_item import PAGE_MODELS_ITEM, READER_ITEM, InstallItem, InstallKind
from ....texts import addons as txt
from ....texts.settings import TAB_READING_MODELS
from ._common import GREY_NOTE_STYLE, ScrolledTab, apply_installed, tick_enabled


class ReadingModelsTab(ScrolledTab):
    """The built-in reader and the page models, each with its own box."""

    def __init__(self, app_service: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._svc = app_service
        self._pending: list[InstallItem] = []

        group = QGroupBox(TAB_READING_MODELS)
        group_layout = QVBoxLayout(group)

        intro = QLabel(txt.READING_MODELS_INTRO)
        intro.setWordWrap(True)
        group_layout.addWidget(intro)

        group_layout.addWidget(self.item_box(READER_ITEM, txt.READER_ITEM_LABEL))

        page_models = self.item_box(PAGE_MODELS_ITEM, txt.PAGE_MODELS_LABEL)
        group_layout.addWidget(page_models)
        page_models_hint = QLabel(txt.PAGE_MODELS_HINT)
        page_models_hint.setWordWrap(True)
        page_models_hint.setStyleSheet(f"{GREY_NOTE_STYLE} margin-left: 20px;")
        group_layout.addWidget(page_models_hint)

        folder = QLabel(txt.saved_in(str(self._svc.reader.models_dir())))
        folder.setStyleSheet(GREY_NOTE_STYLE)
        folder.setWordWrap(True)
        folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        group_layout.addWidget(folder)

        self.body_layout.addWidget(group)
        self.body_layout.addStretch(1)

    def preselect(self, entries: Sequence[InstallItem]) -> None:
        """Tick the reader's and the page models' boxes when a deep link asks for them.

        They are ticked before the installation check has answered: these files need no server,
        and the check ticks them again once it knows which of them are there already.
        """
        items: list[InstallItem] = []
        if any(entry.kind is InstallKind.READER for entry in entries):
            items.append(READER_ITEM)
        if any(entry.kind is InstallKind.PAGE_MODELS for entry in entries):
            items.append(PAGE_MODELS_ITEM)
        self._pending.extend(items)
        tick_enabled(self.checkboxes, items)

    def tick_preselect(self) -> None:
        """Tick the requested boxes again, now that the check knows what is installed."""
        tick_enabled(self.checkboxes, self._pending)
        self._pending.clear()

    def mark_installed(self, results: Mapping[InstallItem, bool]) -> None:
        """Show which items are there and disable the boxes of the ones that are."""
        apply_installed(self.checkboxes, self.labels, results)

    def checked_items(self) -> list[InstallItem]:
        """Return the items this tab asks to install."""
        return [
            item for item, box in self.checkboxes.items() if box.isChecked() and box.isEnabled()
        ]

    def step_label(self, item: InstallItem) -> str | None:
        """Return how the status line names *item*, or ``None`` when this tab does not own it."""
        if item == READER_ITEM:
            return txt.ADDONS_ITEM_READER
        if item == PAGE_MODELS_ITEM:
            return txt.ADDONS_ITEM_PAGE_MODELS
        return None
