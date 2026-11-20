"""Colours, character formats and icons of the Check tab's marks.

One place for everything the three views and the side panel draw, so a mark means the same in
the text, in the list and in the card.  Every background sets its text colour too: the app runs
on a dark theme, whose light text is unreadable on these light backgrounds, and the colours are
chosen to read on both.

:func:`look` is the only colour lookup: a model change takes its category's colour, every other
kind a colour of its own, and no two of them share a background.  The text marks, the list's
badge, the card's chip and the card's context line all draw the look of the item.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QTextCharFormat

from ...application.check_items import CheckItem, ItemKind

STUDENT_STRIKE = "#999999"
"""The student's own crossed-out words: grey, so they read as the page's history."""

MODEL_STRIKE = "#c0392b"
"""The words the model took out: red, as in the Word files and the benchmark."""

CHOSEN_BACKGROUND = "#ffe066"
CHOSEN_TEXT = "#222222"
"""The item the teacher picked in the list: a stronger background, drawn over the item's own."""

#: Background and text colour of a change category, in the text, the badge and the chip.
CATEGORY_COLOURS: dict[str, tuple[str, str]] = {
    "grammar": ("#dbeafe", "#1e3a8a"),
    "spelling": ("#eef8ee", "#14532d"),
    "punctuation": ("#f3e8ff", "#4c1d95"),
    "word_choice": ("#ffe3b3", "#5a3200"),
    "word_order": ("#cffafe", "#155e75"),
    "other": ("#f3f4f6", "#4b5563"),
}

#: Background and text colour of every kind that is not a model change of a category.
KIND_COLOURS: dict[ItemKind, tuple[str, str]] = {
    ItemKind.UNEXPLAINED: ("#d7ffd7", "#14532d"),
    ItemKind.OWN: ("#c7d2fe", "#312e81"),
    ItemKind.TAKEN_OUT: ("#e7e5e4", "#44403c"),
    ItemKind.UNCLEAR: ("#fde68a", "#78350f"),
    ItemKind.LEFT_OUT: ("#fbcfe8", "#831843"),
}

TAG_BACKGROUND = "#e5e7eb"
TAG_TEXT = "#374151"
"""A note that is an attribute of the change rather than a sentence: *optional*, *AI not sure*."""

#: How much of a row's colour a looked-at row keeps: dimmed, not hidden.
LOOKED_AT_ALPHA = 110

#: How much of a row's colour the category badge keeps when the row was looked at.
BADGE_ALPHA = 150

ICON_LOOKED_AT = "\u25cf"
"""An item the teacher has looked at."""

ICON_NOT_LOOKED_AT = "\u25cb"
"""An item the teacher has not looked at yet."""

ICON_OWN = "\u270b"
"""The teacher's own change: nothing to look at, so it keeps its own mark."""

ICON_TAKEN_OUT = "\u21b6"
"""A correction the teacher took out: it keeps its own mark too."""

#: The kinds that keep their own icon instead of the looked-at pair.
_KIND_ICONS = {
    ItemKind.OWN: ICON_OWN,
    ItemKind.TAKEN_OUT: ICON_TAKEN_OUT,
}

#: The kinds whose colour is their change category's: the model's own changes.
_CATEGORY_KINDS = frozenset({ItemKind.CORRECTION, ItemKind.CHANGED})

#: Underline of a mark whose words are not the model's own change.
_UNDERLINE = {
    ItemKind.UNCLEAR: QTextCharFormat.UnderlineStyle.DotLine,
    ItemKind.LEFT_OUT: QTextCharFormat.UnderlineStyle.DotLine,
    ItemKind.TAKEN_OUT: QTextCharFormat.UnderlineStyle.DashUnderline,
}


@dataclass(frozen=True)
class MarkLook:
    """How one item is drawn: its background, its text colour and its underline."""

    background: str
    text: str
    underline: QTextCharFormat.UnderlineStyle | None = None

    def format(self, *, chosen: bool = False) -> QTextCharFormat:
        """Return the character format of a mark with this look; *chosen* draws it stronger."""
        background, text = (
            (CHOSEN_BACKGROUND, CHOSEN_TEXT) if chosen else (self.background, self.text)
        )
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(background))
        fmt.setForeground(QColor(text))
        if self.underline is not None:
            fmt.setUnderlineStyle(self.underline)
            fmt.setUnderlineColor(QColor(text))
        return fmt


def icon(kind: ItemKind, *, looked_at: bool) -> str:
    """The character the list shows for *kind*: its own mark, or whether it was looked at."""
    own = _KIND_ICONS.get(kind)
    if own is not None:
        return own
    return ICON_LOOKED_AT if looked_at else ICON_NOT_LOOKED_AT


def category_colours(category: str) -> tuple[str, str]:
    """The background and text colour of *category*; grey for one without a colour."""
    return CATEGORY_COLOURS.get(category, CATEGORY_COLOURS["other"])


def look(item: CheckItem) -> MarkLook:
    """Return how *item* is drawn, in the text, in the list and in the card.

    A model change takes its category's colour, so a grammar change is the same blue wherever
    the teacher meets it; every other kind has a colour of its own.  A spelling or punctuation
    change is drawn lighter, because the list folds those into one row.
    """
    if item.kind in _CATEGORY_KINDS:
        background, text = category_colours(item.category)
    else:
        background, text = KIND_COLOURS[item.kind]
    if item.is_minor:
        background = _lighter(background)
    return MarkLook(background, text, _UNDERLINE.get(item.kind))


def _lighter(colour: str) -> str:
    """Return *colour* blended halfway to white: the same hue, drawn lighter."""
    light = QColor(colour)
    return QColor(
        (light.red() + 255) // 2, (light.green() + 255) // 2, (light.blue() + 255) // 2
    ).name()
