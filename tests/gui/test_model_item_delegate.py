"""GUI tests for the shared model-item delegate and its grouped combo-box lists."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionViewItem

from teachers_teammate.gui._model_item_delegate import (
    ITEM_LABEL_COLOR_ROLE,
    ITEM_LABEL_ROLE,
    ModelItem,
    ModelItemDelegate,
    fill_grouped,
    install,
    widen,
)

_RED = QColor("#c0392b")


def _combo(qtbot) -> QComboBox:
    """Return an editable combo like the ones the model guidance decorates."""
    combo = QComboBox()
    combo.setEditable(True)
    qtbot.addWidget(combo)
    return combo


def _texts(combo: QComboBox) -> list[str]:
    """Return the item texts, separators included (they are empty strings)."""
    return [combo.itemText(i) for i in range(combo.count())]


# ── fill_grouped ──────────────────────────────────────────────────────────────


def test_fill_grouped_sets_the_raw_model_id_as_the_item_text(qtbot) -> None:
    """
    Given  a combo and two groups of models with labels
    When   fill_grouped fills it
    Then   each item's text is the raw model ID, so the settings keep working
    """
    combo = _combo(qtbot)
    fill_grouped(
        combo,
        [
            [ModelItem("gemma3:12b", label="★ Best"), ModelItem("qwen3:8b", label="Good")],
            [ModelItem("mistral:latest", label="Can't read scans", label_color=_RED)],
        ],
    )
    assert "gemma3:12b" in _texts(combo)
    assert "qwen3:8b" in _texts(combo)
    assert "mistral:latest" in _texts(combo)
    assert [t for t in _texts(combo) if t] == ["gemma3:12b", "qwen3:8b", "mistral:latest"]


def test_fill_grouped_puts_one_separator_between_two_filled_groups(qtbot) -> None:
    """
    Given  a combo filled with two non-empty groups
    When   the list is read back
    Then   exactly one empty separator row sits between them
    """
    combo = _combo(qtbot)
    fill_grouped(combo, [[ModelItem("gemma3:12b")], [ModelItem("mistral:latest")]])
    assert _texts(combo) == ["gemma3:12b", "", "mistral:latest"]


def test_fill_grouped_skips_empty_groups_without_a_separator(qtbot) -> None:
    """
    Given  a combo asked to fill a middle group that is empty
    When   the list is read back
    Then   no separator is drawn for it (no double line, no leading line)
    """
    combo = _combo(qtbot)
    fill_grouped(combo, [[ModelItem("gemma3:12b")], [], [ModelItem("mistral:latest")]])
    assert _texts(combo) == ["gemma3:12b", "", "mistral:latest"]

    fill_grouped(combo, [[], [ModelItem("qwen3:8b")], [], [ModelItem("mistral:latest")]])
    assert _texts(combo) == ["qwen3:8b", "", "mistral:latest"]

    fill_grouped(combo, [[ModelItem("qwen3:8b")], []])
    assert _texts(combo) == ["qwen3:8b"]


def test_fill_grouped_stores_label_colour_and_tooltip_in_item_roles(qtbot) -> None:
    """
    Given  a model item with a label, a label colour and a tooltip
    When   fill_grouped adds it
    Then   the three roles are set and the text stays the bare model ID
    """
    combo = _combo(qtbot)
    fill_grouped(combo, [[ModelItem("mistral:latest", "Can't read scans", _RED, "Why not")]])
    index = combo.findText("mistral:latest")
    assert index >= 0
    assert combo.itemText(index) == "mistral:latest"
    assert combo.itemData(index, ITEM_LABEL_ROLE) == "Can't read scans"
    assert combo.itemData(index, ITEM_LABEL_COLOR_ROLE) == _RED
    assert combo.itemData(index, Qt.ItemDataRole.ToolTipRole) == "Why not"


def test_separator_rows_carry_no_label_and_no_model_id(qtbot) -> None:
    """
    Given  a combo with a separator between two groups
    When   the separator row is inspected
    Then   it has no text and no label, so nothing looks like a model
    """
    combo = _combo(qtbot)
    fill_grouped(combo, [[ModelItem("gemma3:12b")], [ModelItem("mistral:latest")]])
    assert combo.itemText(1) == ""
    assert combo.itemData(1, ITEM_LABEL_ROLE) in (None, "")


def test_a_visible_model_is_found_by_its_raw_id(qtbot) -> None:
    """
    Given  a filled combo whose items carry labels
    When   a model is looked up and selected by its raw ID
    Then   findText and setCurrentText work as before, and the value is the raw ID
    """
    combo = _combo(qtbot)
    fill_grouped(
        combo,
        [[ModelItem("gemma3:12b", label="★ Best")], [ModelItem("mistral:latest", label="No")]],
    )
    index = combo.findText("mistral:latest")
    assert index == 2  # after "gemma3:12b" and the separator row
    combo.setCurrentIndex(index)
    assert combo.currentText() == "mistral:latest"


def test_fill_grouped_replaces_a_previous_list(qtbot) -> None:
    """
    Given  a combo already filled with models
    When   fill_grouped is called again with different ones
    Then   the old models and separators are gone
    """
    combo = _combo(qtbot)
    fill_grouped(combo, [[ModelItem("old-a")], [ModelItem("old-b")]])
    fill_grouped(combo, [[ModelItem("new-a")]])
    assert _texts(combo) == ["new-a"]


# ── install and widen ─────────────────────────────────────────────────────────


def test_install_gives_the_combo_the_label_delegate_once(qtbot) -> None:
    """
    Given  a plain editable combo
    When   install is called twice
    Then   the combo uses the label delegate, and the same instance both times
    """
    combo = _combo(qtbot)
    first = install(combo)
    second = install(combo)
    assert isinstance(first, ModelItemDelegate)
    assert first is second
    assert combo.itemDelegate() is first


def test_widen_makes_the_popup_room_for_the_longest_name_and_label(qtbot) -> None:
    """
    Given  a filled combo with a long model name and a label
    When   the popup is inspected
    Then   its minimum width leaves room for both rather than clipping the label
    """
    combo = _combo(qtbot)
    fill_grouped(combo, [[ModelItem("qwen3-vl:235b-instruct-q8_0", label="★ Best · 24 GB")]])
    narrow = combo.fontMetrics().horizontalAdvance("qwen3-vl:235b-instruct-q8_0")
    assert combo.view().minimumWidth() > narrow


def test_widen_clears_no_entries(qtbot) -> None:
    """
    Given  a filled combo
    When   widen is called on its own
    Then   the list is unchanged (it only sizes the popup)
    """
    combo = _combo(qtbot)
    fill_grouped(combo, [[ModelItem("gemma3:12b", label="★ Best")]])
    before = _texts(combo)
    widen(combo)
    assert _texts(combo) == before


# ── painting ──────────────────────────────────────────────────────────────────


def _paint_row(combo: QComboBox, row: int, *, selected: bool = False) -> QImage:
    """Paint row *row* of *combo* through its delegate into an image and return it."""
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 300, 24)
    option.font = combo.font()
    option.widget = combo
    option.state = QStyle.StateFlag.State_Enabled
    if selected:
        option.state |= QStyle.StateFlag.State_Selected
    option.text = combo.itemText(row)
    image = QImage(300, 24, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        combo.itemDelegate().paint(painter, option, combo.model().index(row, 0))
    finally:
        painter.end()
    return image


@pytest.mark.parametrize("selected", [False, True])
def test_painting_a_labelled_row_works_and_keeps_the_model_name(qtbot, selected: bool) -> None:
    """
    Given  a combo whose rows carry labels, with and without the row selected
    When   a labelled row is painted
    Then   painting works and the label does not replace the model name in the item text
    """
    combo = _combo(qtbot)
    install(combo)
    fill_grouped(
        combo,
        [
            [ModelItem("gemma3:12b", label="★ Best · 9 GB"), ModelItem("qwen3:8b", "Good")],
            [ModelItem("mistral:latest", "Can't read scans", _RED)],
        ],
    )
    row = combo.findText("gemma3:12b")
    image = _paint_row(combo, row, selected=selected)
    assert not image.isNull()
    assert image.width() == 300
    assert combo.itemText(row) == "gemma3:12b"


def test_rows_without_a_label_are_painted_by_the_base_delegate(qtbot) -> None:
    """
    Given  a combo filled without labels (a plain provider model list)
    When   such a row is painted
    Then   the delegate leaves the standard painting in place and still draws the row
    """
    combo = _combo(qtbot)
    install(combo)
    combo.addItems(["gpt-4o", "gpt-3.5"])
    assert not _paint_row(combo, 0).isNull()
    assert combo.itemData(0, ITEM_LABEL_ROLE) is None


def test_a_long_name_is_elided_instead_of_running_under_the_label(qtbot) -> None:
    """
    Given  a row whose model name is far too long for the space left of its label
    When   the row is painted
    Then   painting succeeds: the name is shortened rather than drawn over the label
    """
    combo = _combo(qtbot)
    install(combo)
    combo.addItem("hf.co/some-very-long-repository-name/model:instruction-q8_0")
    combo.setItemData(0, "★ Best · 24 GB", ITEM_LABEL_ROLE)
    assert not _paint_row(combo, 0).isNull()


def test_the_opened_popup_renders_its_labelled_rows(qtbot) -> None:
    """
    Given  a populated combo whose rows carry labels and a separator
    When   the popup is opened and rendered
    Then   the view paints without error and is wide enough for a name plus its label
    """
    combo = _combo(qtbot)
    install(combo)
    fill_grouped(
        combo,
        [[ModelItem("gemma3:12b", "★ Best · 9 GB")], [ModelItem("mistral:latest", "Reading only")]],
    )
    combo.resize(320, 30)
    combo.show()
    combo.showPopup()
    qtbot.wait(50)
    try:
        view = combo.view()
        image = view.grab().toImage()
        assert not image.isNull()
        assert view.width() >= combo.view().minimumWidth()
    finally:
        combo.hidePopup()


def _separator_row(combo: QComboBox) -> int:
    """Return the row of the first separator of *combo*."""
    return next(
        row
        for row in range(combo.count())
        if combo.itemData(row, Qt.ItemDataRole.AccessibleDescriptionRole) == "separator"
    )


def test_a_separator_is_a_thin_row_not_an_empty_entry(qtbot) -> None:
    """
    Given  a combo with the label delegate and a separator between two groups
    When   the delegate is asked how high the separator row and an entry row are
    Then   the separator is clearly lower than an entry (a divider, not a blank row)
    """
    combo = _combo(qtbot)
    install(combo)
    fill_grouped(combo, [[ModelItem("gemma3:12b", "★ Best")], [ModelItem("qwen3:8b", "Good")]])
    option = QStyleOptionViewItem()
    model = combo.model()

    separator = combo.itemDelegate().sizeHint(option, model.index(_separator_row(combo), 0))
    entry = combo.itemDelegate().sizeHint(option, model.index(0, 0))

    assert separator.height() < entry.height()


def test_a_separator_is_painted_as_a_line(qtbot) -> None:
    """
    Given  a combo with the label delegate and a separator between two groups
    When   the separator row is painted
    Then   a line is drawn across the middle of the row and the rest stays empty
    """
    combo = _combo(qtbot)
    install(combo)
    fill_grouped(combo, [[ModelItem("gemma3:12b", "★ Best")], [ModelItem("qwen3:8b", "Good")]])

    image = _paint_row(combo, _separator_row(combo))

    middle = image.height() // 2
    line = [image.pixelColor(150, y).alpha() for y in range(middle - 2, middle + 2)]
    assert any(alpha > 0 for alpha in line)
    assert image.pixelColor(150, 1).alpha() == 0
