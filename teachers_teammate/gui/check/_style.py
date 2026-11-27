"""Colours, character formats and marks of the Check tab.

One place for everything the three views and the side panel draw, so a mark means the same in
the text, in the list and in the card.  The app always runs on the dark theme, so every colour
here is a dark tint with light text on it: a pale background would need dark text, and the
theme's own light text would be unreadable on it.

The rule the tab follows: colour says what happened to the text, a word says the category.

* ``CORRECTION`` and ``UNEXPLAINED`` are green, ``CHANGED`` and ``OWN`` blue, ``UNCLEAR`` and
  ``LEFT_OUT`` amber, ``TAKEN_OUT`` grey, each with an accent (:data:`MARK_ACCENTS`) drawn as the
  mark's underline, as the bar at the left of its row and as its swatch in the legend.
* Paler means one thing: a spelling or punctuation change.
* A badge or a chip is neutral grey and carries the name: the category, or what kind of thing an
  item is where it has no category.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette, QTextCharFormat

from ...application.check_items import CheckItem, ItemKind, counts_for_looked_at

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


#: Background and text colour of a mark in the text: what kind of thing the item is.
MARK_COLOURS: dict[ItemKind, tuple[str, str]] = {
    ItemKind.CORRECTION: ("#1d4a2b", "#c6f7d0"),
    ItemKind.UNEXPLAINED: ("#1d4a2b", "#c6f7d0"),
    ItemKind.CHANGED: ("#22406b", "#cfe1ff"),
    ItemKind.OWN: ("#22406b", "#cfe1ff"),
    ItemKind.TAKEN_OUT: ("#3a4048", "#dfe4e8"),
    ItemKind.UNCLEAR: ("#4d3a12", "#ffdf9e"),
    ItemKind.LEFT_OUT: ("#4d3a12", "#ffdf9e"),
}

#: The accent of a kind: its underline in the text, the bar at the left of its list row and its
#: swatch in the legend.  Four colours for seven kinds, since four things happened to the text.
MARK_ACCENTS: dict[ItemKind, str] = {
    ItemKind.CORRECTION: "#4caf50",
    ItemKind.UNEXPLAINED: "#4caf50",
    ItemKind.CHANGED: "#4a90d9",
    ItemKind.OWN: "#4a90d9",
    ItemKind.UNCLEAR: "#e0a030",
    ItemKind.LEFT_OUT: "#e0a030",
    ItemKind.TAKEN_OUT: "#8c949b",
}

CHIP_BACKGROUND = "#34393e"
CHIP_TEXT = "#d3d8dc"
"""A badge in the list and a chip in the card: neutral grey, whatever the category says."""

#: The bullet in front of a row the teacher has not looked at yet.
TODO_DOT = "\u2022"

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
    """How one item is drawn: its background, its text colour, its accent and its underline."""

    background: str
    text: str
    accent: str
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
            fmt.setUnderlineColor(QColor(self.accent))
        return fmt


def todo_mark(item: CheckItem, *, looked_at: bool) -> str:
    """The bullet in front of a row: one the teacher has not looked at yet and has to.

    A row that does not count for looked-at — a change of the teacher's own — has no bullet, and
    a row already looked at has none either; the row's colour says it was looked at.
    """
    return TODO_DOT if counts_for_looked_at(item) and not looked_at else ""


def icon(kind: ItemKind, *, looked_at: bool) -> str:
    """The character the list shows for *kind*: its own mark, or whether it was looked at."""
    own = _KIND_ICONS.get(kind)
    if own is not None:
        return own
    return ICON_LOOKED_AT if looked_at else ICON_NOT_LOOKED_AT


def row_text(palette: QPalette, *, looked_at: bool, selected: bool) -> QColor:
    """Return the colour a row's bullet and words are drawn in.

    A selected row takes the theme's selection text, a looked-at one the theme's grey for text
    that is there but not the point, and every other row the ordinary text colour.  Nothing is
    faded: a colour drawn at an alpha was unreadable on the list's dark background.
    """
    if selected:
        return palette.color(QPalette.ColorRole.HighlightedText)
    if looked_at:
        return palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    return palette.color(QPalette.ColorRole.Text)


def look(item: CheckItem) -> MarkLook:
    """Return how *item* is drawn in the text and in the card's context line.

    A kept correction is green, the teacher's own change blue, an unclear or not proofread
    passage amber and a taken-out correction grey.  A spelling or punctuation change is drawn
    weaker, because it is a small thing to look at.  Every mark is underlined in its kind's
    accent, so it reads without its colour.
    """
    background, text = MARK_COLOURS[item.kind]
    if item.is_minor:
        background = _weaker(background)
    return MarkLook(background, text, MARK_ACCENTS[item.kind], _UNDERLINE.get(item.kind, _SOLID))
