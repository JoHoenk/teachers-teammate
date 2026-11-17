"""GUI tests for the marks' colours and icons: one place, every kind covered."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from teachers_teammate.application.check_items import ItemKind
from teachers_teammate.gui.check import _style


@pytest.mark.gui
@pytest.mark.parametrize("kind", list(ItemKind), ids=lambda kind: kind.value)
def test_every_kind_has_an_icon_and_a_mark(kind: ItemKind) -> None:
    """
    Given  a kind of item the list can show
    When   its icon and its mark are asked for
    Then   both exist, and the mark is readable on its own background
    """
    assert _style.icon(kind)

    fmt = _style.mark_format(kind)

    assert fmt.background().color().isValid()
    assert fmt.foreground().color().isValid()
    assert fmt.background().color() != fmt.foreground().color()


@pytest.mark.gui
def test_the_chosen_mark_wins_over_the_kind() -> None:
    """
    Given  a correction and the same correction chosen in the list
    When   both marks are made
    Then   the chosen one has the stronger background
    """
    plain = _style.mark_format(ItemKind.CORRECTION)
    chosen = _style.mark_format(ItemKind.CORRECTION, chosen=True)

    assert chosen.background().color() == QColor(_style.CHOSEN_BACKGROUND)
    assert plain.background().color() != chosen.background().color()


@pytest.mark.gui
def test_a_minor_mark_is_lighter_than_the_correction_it_belongs_to() -> None:
    """
    Given  a spelling change and the same change folded into the minor group
    When   both marks are made
    Then   the folded one is drawn lighter
    """
    plain = _style.mark_format(ItemKind.CORRECTION)
    minor = _style.mark_format(ItemKind.CORRECTION, minor=True)

    assert minor.background().color() == QColor(_style.MINOR_BACKGROUND)
    assert minor.background().color() != plain.background().color()


@pytest.mark.gui
def test_the_student_and_the_model_are_struck_in_different_colours() -> None:
    """
    Given  the two kinds of crossed-out words
    When   their colours are compared
    Then   the student's are grey and the model's red
    """
    assert _style.STUDENT_STRIKE == "#999999"
    assert _style.MODEL_STRIKE == "#c0392b"
