"""GUI tests for the marks' colours and icons: one look per item, every kind covered."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from teachers_teammate.application.check_items import CheckItem, ItemKind
from teachers_teammate.domain.proofreading.review import CHANGE_CATEGORIES
from teachers_teammate.gui.check import _style

#: The kinds whose colour is their change category's, not a colour of their own.
_CATEGORY_KINDS = (ItemKind.CORRECTION, ItemKind.CHANGED)


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


@pytest.mark.gui
@pytest.mark.parametrize("kind", list(ItemKind), ids=lambda kind: kind.value)
def test_every_kind_has_an_icon_and_a_look(kind: ItemKind) -> None:
    """
    Given  a kind of item the list can show
    When   its icon and its look are asked for
    Then   both exist, and the look is readable on its own background
    """
    assert _style.icon(kind, looked_at=False)
    assert _style.icon(kind, looked_at=True)

    look = _style.look(_item(kind, category="grammar"))

    assert QColor(look.background).isValid()
    assert QColor(look.text).isValid()
    assert look.background != look.text


@pytest.mark.gui
def test_no_two_items_share_a_background() -> None:
    """
    Given  every change category and every kind that is not a model change of a category
    When   their looks are compared
    Then   no two share a background, so a colour names one thing
    """
    looks = [
        _style.look(_item(ItemKind.CORRECTION, category=category)) for category in CHANGE_CATEGORIES
    ]
    looks += [_style.look(_item(kind)) for kind in ItemKind if kind not in _CATEGORY_KINDS]

    backgrounds = [look.background for look in looks]

    assert len(set(backgrounds)) == len(backgrounds)


@pytest.mark.gui
def test_a_model_change_takes_the_colour_of_its_category() -> None:
    """
    Given  a grammar change and a word choice change
    When   their looks are compared with the category colours
    Then   each is drawn in its own category's colour, so the text and the badge agree
    """
    grammar = _style.look(_item(ItemKind.CORRECTION, category="grammar"))
    word_choice = _style.look(_item(ItemKind.CHANGED, category="word_choice"))

    assert (grammar.background, grammar.text) == _style.CATEGORY_COLOURS["grammar"]
    assert (word_choice.background, word_choice.text) == _style.CATEGORY_COLOURS["word_choice"]


@pytest.mark.gui
def test_the_chosen_mark_wins_over_the_item() -> None:
    """
    Given  a correction and the same correction chosen in the list
    When   both marks are made
    Then   the chosen one has the stronger background
    """
    look = _style.look(_item(ItemKind.CORRECTION, category="grammar"))

    plain = look.format()
    chosen = look.format(chosen=True)

    assert chosen.background().color() == QColor(_style.CHOSEN_BACKGROUND)
    assert plain.background().color() != chosen.background().color()


@pytest.mark.gui
def test_a_minor_mark_is_lighter_than_the_change_it_belongs_to() -> None:
    """
    Given  a punctuation change and the same change folded into the minor group
    When   both looks are made
    Then   the folded one keeps the category's hue, drawn lighter
    """
    plain = _style.look(_item(ItemKind.CORRECTION, category="punctuation"))
    minor = _style.look(_item(ItemKind.CORRECTION, category="punctuation", minor=True))

    assert minor.background != plain.background
    assert minor.text == plain.text
    assert QColor(minor.background).lightness() > QColor(plain.background).lightness()


@pytest.mark.gui
def test_a_mark_that_is_not_the_model_s_own_change_is_underlined() -> None:
    """
    Given  an unclear passage, a stretch the model left out and a correction it took out
    When   their looks are made
    Then   each is underlined, so it reads as something other than a plain change
    """
    for kind in (ItemKind.UNCLEAR, ItemKind.LEFT_OUT, ItemKind.TAKEN_OUT):
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
