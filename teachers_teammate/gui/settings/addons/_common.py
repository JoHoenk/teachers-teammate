"""What the tabs of the Add-ons page share: the frame, the mark of an installed item, a tick.

The three short tabs (reading models, online services, name detection) differ only in the rows
they list: they scroll the same way, and an item of theirs is marked as installed in the same
words.  That is what lives here; everything else stays in the tab that owns it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QScrollArea, QVBoxLayout, QWidget

from ....application.addons import bundled_status_key
from ....texts import addons as txt
from ..._install_round import (
    PACKAGE_PREFIX,
    PAGE_MODELS_ITEM,
    PAGE_MODELS_PREFIX,
    READER_ITEM,
    READER_PREFIX,
)
from ..._model_guidance import PROVIDER_PREFIX, SPACY_PREFIX

#: A small grey note under a control (the main content of a tab is never this small).
GREY_NOTE_STYLE = "color: #888; font-size: 11px;"

#: The prefixes of a deep link's entries that name a package or a model file, not a model tag.
_ENTRY_PREFIXES = (
    PROVIDER_PREFIX,
    PACKAGE_PREFIX,
    SPACY_PREFIX,
    READER_PREFIX,
    PAGE_MODELS_PREFIX,
)


def is_model_tag(entry: str) -> bool:
    """Return whether *entry* names an Ollama model, rather than a package or a model file."""
    return bool(entry) and not entry.startswith(_ENTRY_PREFIXES)


class ScrolledTab(QScrollArea):
    """The frame the short tabs share: one content widget, laid out in :attr:`body_layout`.

    The local-models tab is not one of these: its tree scrolls and the page does not, so the
    install button below the tabs stays visible on a laptop screen.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.setWidget(content)
        self.body_layout = QVBoxLayout(content)
        #: The items of this tab, keyed like the installation check reports them.
        self.checkboxes: dict[str, QCheckBox] = {}
        #: Each box's own label, by item: the mark of an installed item is added to it.
        self.labels: dict[str, str] = {}

    def item_box(self, key: str, label: str) -> QCheckBox:
        """Return the box of item *key*, labelled *label*, and list it under that label."""
        box = QCheckBox(label)
        self.checkboxes[key] = box
        self.labels[key] = label
        return box


def installed_mark(key: str, results: Mapping[str, object]) -> str:
    """Return what an installed row says: *included* when the app carries the files itself."""
    if results.get(bundled_status_key(key)):
        return txt.INCLUDED
    if key in (READER_ITEM, PAGE_MODELS_ITEM):
        return txt.READER_DOWNLOADED
    return txt.INSTALLED_MARK


def apply_installed(
    boxes: Mapping[str, QCheckBox],
    labels: Mapping[str, str],
    results: Mapping[str, object],
) -> None:
    """Mark the boxes of what is installed and untick every box.

    *labels* holds each box's own label, so the mark is added to it rather than read back out of
    the widget.  An installed box is disabled: the mark says it is there, while a tick always
    means "install it", and nothing is installed without the teacher's click.
    """
    for key, box in boxes.items():
        base = labels[key]
        box.setChecked(False)
        if results.get(key, False):
            box.setText(f"{base}  {installed_mark(key, results)}")
            box.setEnabled(False)
        else:
            box.setText(base)
            box.setEnabled(True)


def tick_enabled(boxes: Mapping[str, QCheckBox], keys: Iterable[str]) -> None:
    """Tick the boxes named by *keys* that can still be ticked (installed ones cannot)."""
    for key in keys:
        box = boxes.get(key)
        if box is not None and box.isEnabled():
            box.setChecked(True)
