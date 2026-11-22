"""The few HTML fragments a widget puts around a sentence.

Colour, bold and a path in a monospaced font are layout: the sentence itself comes from
``teachers_teammate/texts``.  A widget that shows a marked-up line calls one of these instead of
building the markup at its own call site, so the colour lives in one place.

A *label* argument is plain text and is escaped here; a *body* argument may hold markup of its
own and is not.  ``headline`` and ``with_path`` take a body next to their label or path,
everything else takes plain text only.
"""

from __future__ import annotations

import html

GREEN = "#27ae60"
RED = "#c0392b"
GREY = "#888"
AMBER = "#e67e22"


def green(text: str) -> str:
    """Return the label *text* in the colour of success."""
    return _coloured(html.escape(text), GREEN)


def red(text: str) -> str:
    """Return the label *text* in the colour of a failure."""
    return _coloured(html.escape(text), RED)


def grey(text: str) -> str:
    """Return the label *text* in the muted colour of a note."""
    return _coloured(html.escape(text), GREY)


def amber(text: str) -> str:
    """Return the label *text* in the colour of a warning."""
    return _coloured(html.escape(text), AMBER)


def bold(text: str) -> str:
    """Return the label *text* in bold."""
    return f"<b>{html.escape(text)}</b>"


def bold_with_note(head: str, note: str) -> str:
    """Return the label *head* in bold, followed by the label *note* in the muted colour."""
    return f"<b>{html.escape(head)}</b> {_coloured(f'({html.escape(note)})', GREY)}"


def heading(text: str) -> str:
    """Return the label *text* in bold with a colon, as the first line of a message."""
    return f"<b>{html.escape(text)}:</b>"


def headline(head: str, rest: str) -> str:
    """Return the label *head* in bold with a full stop, followed by the body *rest*."""
    return f"<b>{html.escape(head)}.</b> {rest}"


def error_line(label: str, detail: str) -> str:
    """Return the label *label* in bold, then the plain message *detail*, all in red."""
    return _coloured(f"<b>{html.escape(label)}</b> {html.escape(detail)}", RED)


def link(href: str, label: str) -> str:
    """Return the label *label* as a link the widget's ``linkActivated`` signal reports as *href*."""
    return f'<a href="{html.escape(href)}">{html.escape(label)}</a>'


def with_path(intro: str, path: str) -> str:
    """Return the body *intro* followed by *path* in a monospaced font, below it."""
    return f"{intro}<br><tt>{html.escape(path)}</tt>"


def _coloured(body: str, colour: str) -> str:
    """Return the body *body* in *colour*."""
    return f'<span style="color:{colour};">{body}</span>'
