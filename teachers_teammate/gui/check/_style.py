"""Colours, character formats and icons of the Check tab's marks.

One place for everything the three views and the side panel draw, so a mark means the same in
the text, in the list and in the card.  Every background sets its text colour too: the app runs
on a dark theme, whose light text is unreadable on these light backgrounds, and the colours are
chosen to read on both.  A change category has one colour, used by the card's chip and the
list's badge alike.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QTextCharFormat

from ...application.check_items import ItemKind

STUDENT_STRIKE = "#999999"
"""The student's own crossed-out words: grey, so they read as the page's history."""

MODEL_STRIKE = "#c0392b"
"""The words the model took out: red, as in the Word files and the benchmark."""

KEPT_BACKGROUND = "#d7ffd7"
KEPT_TEXT = "#14532d"
MINOR_BACKGROUND = "#eef8ee"
MINOR_TEXT = "#14532d"
UNCLEAR_BACKGROUND = "#ffe3b3"
UNCLEAR_TEXT = "#5a3200"
OWN_BACKGROUND = "#dbeafe"
OWN_TEXT = "#1e3a8a"
TAKEN_OUT_BACKGROUND = "#f3f4f6"
TAKEN_OUT_TEXT = "#4b5563"
CHOSEN_BACKGROUND = "#ffe066"
CHOSEN_TEXT = "#222222"

#: Background and text colour of a change category's badge, in the card and in the list.
CATEGORY_COLOURS: dict[str, tuple[str, str]] = {
    "grammar": ("#dbeafe", "#1e3a8a"),
    "spelling": ("#eef8ee", "#14532d"),
    "punctuation": ("#f3e8ff", "#4c1d95"),
    "word_choice": ("#ffe3b3", "#5a3200"),
    "word_order": ("#cffafe", "#155e75"),
    "other": ("#f3f4f6", "#4b5563"),
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

_COLOURS = {
    ItemKind.CORRECTION: (KEPT_BACKGROUND, KEPT_TEXT),
    ItemKind.UNEXPLAINED: (KEPT_BACKGROUND, KEPT_TEXT),
    ItemKind.OWN: (OWN_BACKGROUND, OWN_TEXT),
    ItemKind.CHANGED: (OWN_BACKGROUND, OWN_TEXT),
    ItemKind.TAKEN_OUT: (TAKEN_OUT_BACKGROUND, TAKEN_OUT_TEXT),
    ItemKind.UNCLEAR: (UNCLEAR_BACKGROUND, UNCLEAR_TEXT),
    ItemKind.LEFT_OUT: (UNCLEAR_BACKGROUND, UNCLEAR_TEXT),
}

#: Underline of a mark whose words are not the model's own change.
_UNDERLINE = {
    ItemKind.UNCLEAR: QTextCharFormat.UnderlineStyle.DotLine,
    ItemKind.LEFT_OUT: QTextCharFormat.UnderlineStyle.DotLine,
    ItemKind.TAKEN_OUT: QTextCharFormat.UnderlineStyle.DashUnderline,
}


def icon(kind: ItemKind, *, looked_at: bool) -> str:
    """The character the list shows for *kind*: its own mark, or whether it was looked at."""
    own = _KIND_ICONS.get(kind)
    if own is not None:
        return own
    return ICON_LOOKED_AT if looked_at else ICON_NOT_LOOKED_AT


def category_colours(category: str) -> tuple[str, str]:
    """The background and text colour of *category*'s badge; grey for one without a colour."""
    return CATEGORY_COLOURS.get(category, CATEGORY_COLOURS["other"])


def kind_colours(kind: ItemKind) -> tuple[str, str]:
    """The background and text colour a mark of *kind* has in the text, for the card's context."""
    return _COLOURS[kind]


def mark_format(kind: ItemKind, *, minor: bool = False, chosen: bool = False) -> QTextCharFormat:
    """The character format of a mark of *kind* in an editable view.

    *minor* lightens a spelling or punctuation change, *chosen* is the item the teacher picked:
    a stronger background, drawn over the kind's own.
    """
    background, text = _COLOURS[kind]
    if minor:
        background = MINOR_BACKGROUND
    if chosen:
        background, text = CHOSEN_BACKGROUND, CHOSEN_TEXT
    fmt = QTextCharFormat()
    fmt.setBackground(QColor(background))
    fmt.setForeground(QColor(text))
    underline = _UNDERLINE.get(kind)
    if underline is not None:
        fmt.setUnderlineStyle(underline)
        fmt.setUnderlineColor(QColor(text))
    return fmt
