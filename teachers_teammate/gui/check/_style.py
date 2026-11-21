"""Colours, character formats and icons of the Check tab's marks.

One place for everything the three views and the side panel draw, so a mark means the same in
the text, in the list and in the card.  The app always runs on the dark theme, so every colour
here is a dark tint with light text on it: a pale background would need dark text, and the
theme's own light text would be unreadable on it.

:func:`look` is the only colour lookup for the text: a kept correction is green, the teacher's
own change blue, an unclear or left-out passage amber and a taken-out correction grey, each
underlined so a mark does not rest on its colour alone.  Which category a change belongs to is
read from the badge in the list and the chip in the card, which keep a colour per category.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette, QTextCharFormat

from ...application.check_items import MODEL_KINDS, CheckItem, ItemKind

STUDENT_STRIKE = "#999999"
"""The student's own crossed-out words: grey, so they read as the page's history."""

MODEL_STRIKE = "#c0392b"
"""The words the model took out: red, as in the Word files and the benchmark."""

CHOSEN_BACKGROUND = "#ffe066"
CHOSEN_TEXT = "#222222"
"""The item the teacher picked in the list: the one bright background, with dark text."""

BASE_BACKGROUND = "#19232d"
"""The theme's base colour, which a weaker tint is blended towards."""

#: How far a weaker tint is blended towards the background.
_WEAKER_SHARE = 0.45


def _weaker(colour: str) -> str:
    """Return *colour* blended towards the background: the same hue, drawn weaker."""
    tint = QColor(colour)
    base = QColor(BASE_BACKGROUND)
    return QColor(
        round(tint.red() + (base.red() - tint.red()) * _WEAKER_SHARE),
        round(tint.green() + (base.green() - tint.green()) * _WEAKER_SHARE),
        round(tint.blue() + (base.blue() - tint.blue()) * _WEAKER_SHARE),
    ).name()


def _badge_of_kind(kind: ItemKind) -> tuple[str, str]:
    """Return the badge colours of *kind*: its mark's hue, weaker than any category's badge."""
    background, text = MARK_COLOURS[kind]
    return _weaker(background), text


#: Background and text colour of a change category, in the list's badge and the card's chip.
CATEGORY_COLOURS: dict[str, tuple[str, str]] = {
    "grammar": ("#22406b", "#cfe1ff"),
    "spelling": ("#1d4a2b", "#c6f7d0"),
    "punctuation": ("#3b2a63", "#e3d3ff"),
    "word_choice": ("#4d3a12", "#ffdf9e"),
    "word_order": ("#14454b", "#b3f2f9"),
    "other": ("#34393e", "#d3d8dc"),
}

#: Background and text colour of a mark in the text: what kind of thing the item is.
MARK_COLOURS: dict[ItemKind, tuple[str, str]] = {
    ItemKind.CORRECTION: ("#1d4a2b", "#c6f7d0"),
    ItemKind.UNEXPLAINED: ("#1d4a2b", "#c6f7d0"),
    ItemKind.CHANGED: ("#22406b", "#cfe1ff"),
    ItemKind.OWN: ("#22406b", "#cfe1ff"),
    ItemKind.TAKEN_OUT: ("#34393e", "#d3d8dc"),
    ItemKind.UNCLEAR: ("#4d3a12", "#ffdf9e"),
    ItemKind.LEFT_OUT: ("#4d3a12", "#ffdf9e"),
}

#: Background and text colour of the badge of an item that is no change of the model: the hue of
#: its mark in the text, drawn weaker so that no badge of a kind looks like one of a category.
#: An unclear passage and a stretch the model left out share the amber, since both ask a question.
KIND_BADGE_COLOURS: dict[ItemKind, tuple[str, str]] = {
    ItemKind.UNCLEAR: _badge_of_kind(ItemKind.UNCLEAR),
    ItemKind.LEFT_OUT: _badge_of_kind(ItemKind.UNCLEAR),
    ItemKind.OWN: _badge_of_kind(ItemKind.OWN),
    ItemKind.TAKEN_OUT: _badge_of_kind(ItemKind.TAKEN_OUT),
}

TAG_BACKGROUND = "#34393e"
TAG_TEXT = "#d3d8dc"
"""A note that is an attribute of the change rather than a sentence: *optional*, *AI not sure*."""

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

#: The underline of a mark: the kinds that are not a plain change say so with its style.
_UNDERLINE = {
    ItemKind.UNCLEAR: QTextCharFormat.UnderlineStyle.DotLine,
    ItemKind.LEFT_OUT: QTextCharFormat.UnderlineStyle.DotLine,
    ItemKind.TAKEN_OUT: QTextCharFormat.UnderlineStyle.DashUnderline,
}

#: The underline of every other mark: a plain line, so the mark reads without its colour.
_SOLID = QTextCharFormat.UnderlineStyle.SingleUnderline


@dataclass(frozen=True)
class MarkLook:
    """How one item is drawn: its background, its text colour and its underline."""

    background: str
    text: str
    underline: QTextCharFormat.UnderlineStyle | None = None

    def format(self, *, chosen: bool = False) -> QTextCharFormat:
        """Return the character format of a mark with this look; *chosen* draws it stronger."""
        fmt = QTextCharFormat()
        if chosen:
            fmt.setBackground(QColor(CHOSEN_BACKGROUND))
            fmt.setForeground(QColor(CHOSEN_TEXT))
            return fmt
        fmt.setBackground(QColor(self.background))
        fmt.setForeground(QColor(self.text))
        if self.underline is not None:
            fmt.setUnderlineStyle(self.underline)
            fmt.setUnderlineColor(QColor(self.text))
        return fmt


def icon(kind: ItemKind, *, looked_at: bool) -> str:
    """The character the list shows for *kind*: its own mark, or whether it was looked at."""
    own = _KIND_ICONS.get(kind)
    if own is not None:
        return own
    return ICON_LOOKED_AT if looked_at else ICON_NOT_LOOKED_AT


def row_text(palette: QPalette, *, looked_at: bool, selected: bool) -> QColor:
    """Return the colour a row's icon and words are drawn in.

    A selected row takes the theme's selection text, a looked-at one the theme's grey for text
    that is there but not the point, and every other row the ordinary text colour.  Nothing is
    faded: a colour drawn at an alpha was unreadable on the list's dark background.
    """
    if selected:
        return palette.color(QPalette.ColorRole.HighlightedText)
    if looked_at:
        return palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    return palette.color(QPalette.ColorRole.Text)


def category_colours(category: str) -> tuple[str, str]:
    """The background and text colour of *category*; grey for one without a colour."""
    return CATEGORY_COLOURS.get(category, CATEGORY_COLOURS["other"])


def look(item: CheckItem) -> MarkLook:
    """Return how *item* is drawn in the text and in the card's context line.

    A kept correction is green, the teacher's own change blue, an unclear or left-out passage
    amber and a taken-out correction grey.  A spelling or punctuation change is drawn weaker,
    because it is a small thing to look at.  Every mark is underlined, so it reads without its
    colour.
    """
    background, text = MARK_COLOURS[item.kind]
    if item.is_minor:
        background = _weaker(background)
    return MarkLook(background, text, _UNDERLINE.get(item.kind, _SOLID))


def badge_colours(item: CheckItem) -> tuple[str, str]:
    """Return the background and text colour of the badge the list draws for *item*.

    A model change shows its category's colour, so the badge says which kind of change it is; one
    without a category is grey, like *other*.  Every other item shows its kind's colour, so every
    row says what it is.
    """
    if item.category or item.kind in MODEL_KINDS:
        return category_colours(item.category)
    return KIND_BADGE_COLOURS[item.kind]
