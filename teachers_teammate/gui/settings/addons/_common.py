"""What the tabs of the Add-ons page share: the frame, the mark of an installed item, a tick.

The three short tabs (reading models, online services, name detection) differ only in the rows
they list: they scroll the same way, and an item of theirs is marked as installed in the same
words.  That is what lives here; everything else stays in the tab that owns it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QScrollArea, QVBoxLayout, QWidget

from ....domain.install_item import InstallItem, InstallKind
from ....texts import addons as txt

#: A small grey note under a control (the main content of a tab is never this small).
GREY_NOTE_STYLE = "color: #888; font-size: 11px;"


def is_model_tag(item: InstallItem) -> bool:
    """Return whether *item* names an Ollama model, rather than a package or a model file."""
    return item.kind is InstallKind.OLLAMA


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
        self.checkboxes: dict[InstallItem, QCheckBox] = {}
        #: Each box's own label, by item: the mark of an installed item is added to it.
        self.labels: dict[InstallItem, str] = {}

    def item_box(self, item: InstallItem, label: str) -> QCheckBox:
        """Return the box of *item*, labelled *label*, and list it under that label."""
        box = QCheckBox(label)
        self.checkboxes[item] = box
        self.labels[item] = label
        return box


def installed_mark(item: InstallItem, results: Mapping[InstallItem, bool]) -> str:
    """Return what an installed row says: *included* when the app carries the files itself."""
    if results.get(item.bundled):
        return txt.INCLUDED
    if item.kind in (InstallKind.READER, InstallKind.PAGE_MODELS):
        return txt.READER_DOWNLOADED
    return txt.INSTALLED_MARK


def apply_installed(
    boxes: Mapping[InstallItem, QCheckBox],
    labels: Mapping[InstallItem, str],
    results: Mapping[InstallItem, bool],
) -> None:
    """Mark the boxes of what is installed and untick every box.

    *labels* holds each box's own label, so the mark is added to it rather than read back out of
    the widget.  An installed box is disabled: the mark says it is there, while a tick always
    means "install it", and nothing is installed without the teacher's click.
    """
    for item, box in boxes.items():
        base = labels[item]
        box.setChecked(False)
        if results.get(item, False):
            box.setText(f"{base}  {installed_mark(item, results)}")
            box.setEnabled(False)
        else:
            box.setText(base)
            box.setEnabled(True)


def tick_enabled(boxes: Mapping[InstallItem, QCheckBox], items: Iterable[InstallItem]) -> None:
    """Tick the boxes named by *items* that can still be ticked (installed ones cannot)."""
    for item in items:
        box = boxes.get(item)
        if box is not None and box.isEnabled():
            box.setChecked(True)
