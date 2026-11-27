"""GUI tests for the marks' colours and icons: one look per item, every kind covered."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from teachers_teammate.application.check_items import CheckItem, ItemKind
from teachers_teammate.domain.proofreading.review import CHANGE_CATEGORIES
from teachers_teammate.gui.check import _style


def _item(kind: ItemKind, *, category: str = "", minor: bool = False) -> CheckItem:
    """Return an item of *kind*, with the category and the minor flag the look reads."""
    return CheckItem(
        key="edit:1",
        kind=kind,
        number=1,
        before="bunte",
        after="braune",
        category=category,
        explanation="Farbe.",
        optional=False,
        needed=None,
        raw_span=(4, 9),
        corrected_spans=((4, 10),),
        is_minor=minor,
    )


def _luminance(colour: QColor) -> float:
    """The relative luminance of *colour*, as WCAG defines it."""

    def channel(value: int) -> float:
        share = value / 255
        return share / 12.92 if share <= 0.03928 else ((share + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(colour.red())
        + 0.7152 * channel(colour.green())
        + 0.0722 * channel(colour.blue())
    )


def _contrast(first: QColor, second: QColor) -> float:
    """The contrast ratio between two colours, from 1 (none) to 21 (black on white)."""
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.gui
@pytest.mark.parametrize("kind", list(ItemKind), ids=lambda kind: kind.value)
def test_every_kind_has_an_icon_and_a_look(kind: ItemKind) -> None:
    """
    Given  a kind of item the list can show
    When   its icon and its look are asked for
    Then   both exist, and the look's text reads on its own background
    """
    assert _style.icon(kind, looked_at=False)
    assert _style.icon(kind, looked_at=True)

    look = _style.look(_item(kind, category="grammar"))

    assert QColor(look.background).isValid()
    assert QColor(look.text).isValid()
    assert _contrast(QColor(look.text), QColor(look.background)) >= 4.5


@pytest.mark.gui
def test_the_four_kinds_of_mark_have_four_backgrounds() -> None:
    """
    Given  a kept correction, the teacher's own change, an unclear passage and a taken-out one
    When   their looks are compared
    Then   each has a background of its own, so a colour names one kind of thing
    """
    kinds = (ItemKind.CORRECTION, ItemKind.OWN, ItemKind.UNCLEAR, ItemKind.TAKEN_OUT)

    backgrounds = [_style.look(_item(kind)).background for kind in kinds]

    assert len(set(backgrounds)) == len(backgrounds)


@pytest.mark.gui
def test_a_mark_is_a_dark_tint_that_stands_off_the_text() -> None:
    """
    Given  a kept correction on the theme the app runs with
    When   its look is made
    Then   the background is darker than the text and lighter than the page, so the mark reads
    """
    look = _style.look(_item(ItemKind.CORRECTION, category="grammar"))
    background = QColor(look.background)

    assert _luminance(background) < _luminance(QColor(look.text))
    assert _contrast(background, QColor(_style.BASE_BACKGROUND)) > 1.2


@pytest.mark.gui
def test_the_four_accents_are_four_colours_that_read_on_the_page() -> None:
    """
    Given  the accents the four kinds of mark are drawn in
    When   they are compared with each other and with the page
    Then   no two are the same, so a colour names one kind of thing, and each reads on the page
    """
    accents = {_style.MARK_ACCENTS[kind] for kind in _style.MARK_ACCENTS}

    assert len(accents) == 4
    for colour in accents:
        assert _contrast(QColor(colour), QColor(_style.BASE_BACKGROUND)) >= 3


@pytest.mark.gui
@pytest.mark.parametrize("kind", list(ItemKind), ids=lambda kind: kind.value)
def test_a_marks_underline_is_the_accent_of_its_kind(kind: ItemKind) -> None:
    """
    Given  a mark of any kind
    When   its character format is made
    Then   the underline is drawn in the accent of that kind, not in its text colour
    """
    mark = _style.look(_item(kind)).format()

    assert mark.underlineColor() == QColor(_style.MARK_ACCENTS[kind])


@pytest.mark.gui
def test_no_chip_colour_is_a_mark_colour_or_an_accent() -> None:
    """
    Given  the grey of the badges and chips
    When   it is compared with the marks in the text and with their accents
    Then   it is neither, so grey never means a kind of change
    """
    marks = {colour for pair in _style.MARK_COLOURS.values() for colour in pair}
    accents = set(_style.MARK_ACCENTS.values())
    chips = {_style.CHIP_BACKGROUND, _style.CHIP_TEXT}

    assert not chips & marks
    assert not chips & accents


@pytest.mark.gui
def test_the_chosen_mark_has_the_one_bright_background() -> None:
    """
    Given  every kind of item, and the same item chosen in the list
    When   both marks are made
    Then   the chosen one has the bright background no other kind has, with dark text on it
    """
    chosen = _style.look(_item(ItemKind.CORRECTION, category="grammar")).format(chosen=True)

    assert chosen.background().color() == QColor(_style.CHOSEN_BACKGROUND)
    assert chosen.foreground().color() == QColor(_style.CHOSEN_TEXT)
    for kind in ItemKind:
        plain = _style.look(_item(kind, category="grammar")).format()
        assert plain.background().color() != chosen.background().color()


@pytest.mark.gui
def test_a_minor_mark_is_weaker_than_the_change_it_belongs_to() -> None:
    """
    Given  a punctuation change and the same change marked as minor
    When   both looks are made
    Then   the minor one keeps the hue, drawn closer to the page
    """
    plain = _style.look(_item(ItemKind.CORRECTION, category="punctuation"))
    minor = _style.look(_item(ItemKind.CORRECTION, category="punctuation", minor=True))

    assert minor.background != plain.background
    assert minor.text == plain.text
    assert _contrast(QColor(minor.background), QColor(_style.BASE_BACKGROUND)) < _contrast(
        QColor(plain.background), QColor(_style.BASE_BACKGROUND)
    )


@pytest.mark.gui
def test_every_mark_is_underlined() -> None:
    """
    Given  every kind of item
    When   its look is made
    Then   it is underlined, so a mark reads without its colour
    """
    for kind in ItemKind:
        assert _style.look(_item(kind)).underline is not None


@pytest.mark.gui
def test_the_student_and_the_model_are_struck_in_different_colours() -> None:
    """
    Given  the two kinds of crossed-out words
    When   their colours are compared
    Then   the student's are grey and the model's red
    """
    assert _style.STUDENT_STRIKE == "#999999"
    assert _style.MODEL_STRIKE == "#c0392b"
