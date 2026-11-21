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
def test_the_badge_of_a_model_change_takes_the_colour_of_its_category() -> None:
    """
    Given  a grammar change and a word choice change
    When   their badges are compared with the category colours
    Then   each is drawn in its own category's colour, so the list says which change it is
    """
    grammar = _style.badge_colours(_item(ItemKind.CORRECTION, category="grammar"))
    word_choice = _style.badge_colours(_item(ItemKind.CHANGED, category="word_choice"))

    assert grammar == _style.CATEGORY_COLOURS["grammar"]
    assert word_choice == _style.CATEGORY_COLOURS["word_choice"]


@pytest.mark.gui
def test_no_two_categories_share_a_badge_colour() -> None:
    """
    Given  every change category
    When   their badge colours are compared
    Then   no two share a background, so a badge names one category
    """
    backgrounds = [
        _style.badge_colours(_item(ItemKind.CORRECTION, category=category))[0]
        for category in CHANGE_CATEGORIES
    ]

    assert len(set(backgrounds)) == len(backgrounds)


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
def test_no_badge_of_a_kind_looks_like_the_badge_of_a_category() -> None:
    """
    Given  the badges of the kinds that are no change of the model, and of every category
    When   their backgrounds are compared
    Then   none is shared, so *your change* is never mistaken for *grammar*
    """
    categories = {colours[0] for colours in _style.CATEGORY_COLOURS.values()}
    kinds = {colours[0] for colours in _style.KIND_BADGE_COLOURS.values()}

    assert not categories & kinds


@pytest.mark.gui
def test_a_model_change_without_a_category_has_the_badge_of_other() -> None:
    """
    Given  a correction, a changed correction and an unexplained change, none with a category
    When   their badges are made
    Then   each is the grey of *other*, and none fails for the missing category
    """
    for kind in (ItemKind.CORRECTION, ItemKind.CHANGED, ItemKind.UNEXPLAINED):
        assert _style.badge_colours(_item(kind)) == _style.CATEGORY_COLOURS["other"]


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
