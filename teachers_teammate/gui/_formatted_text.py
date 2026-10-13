"""Qt rendering of OCR text for the preview panel.

:func:`render_html` turns a :class:`~teachers_teammate.domain.formatted_text.FormattedDocument`
into the HTML subset Qt's rich-text engine understands, highlighting what a teacher cares
about: crossed-out words (red, struck through) and insertions written above the line
(raised, blue).  :func:`document_from_qt` is the inverse used for in-place editing.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFocusEvent,
    QFont,
    QKeyEvent,
    QKeySequence,
    QShortcut,
    QTextBlock,
    QTextBlockFormat,
    QTextCharFormat,
    QTextDocument,
    QTextFrame,
    QTextListFormat,
    QTextTable,
)
from PySide6.QtWidgets import QTextEdit

from ..domain.formatted_text import (
    Block,
    FormattedDocument,
    Span,
    Style,
    compact_list_numbers,
    list_markers,
    merge_spans,
)

_LINE_SEPARATOR = chr(0x2028)
_NO_BREAK_SPACE = chr(0x00A0)

STRIKE_COLOUR = "#c0392b"
INSERT_COLOUR = "#1f6feb"
MUTED_COLOUR = "#888888"


def _span_html(span: Span) -> str:
    text = html.escape(span.text).replace("\n", "<br>")
    style = span.style
    if Style.STRIKE in style:
        text = f'<span style="color:{STRIKE_COLOUR};"><s>{text}</s></span>'
    if Style.SUPERSCRIPT in style:
        text = f'<span style="color:{INSERT_COLOUR};"><sup>{text}</sup></span>'
    if Style.SUBSCRIPT in style:
        text = f"<sub>{text}</sub>"
    if Style.UNDERLINE in style:
        text = f"<u>{text}</u>"
    if Style.ITALIC in style:
        text = f"<i>{text}</i>"
    if Style.BOLD in style:
        text = f"<b>{text}</b>"
    return text


def _spans_html(spans: tuple[Span, ...]) -> str:
    return "".join(_span_html(span) for span in spans)


class _ListWriter:
    """Emits nested ``<ul>``/``<ol>`` lists as list items arrive and close them on demand."""

    def __init__(self) -> None:
        self._stack: list[list[object]] = []  # [tag, li_open]

    def item(self, block: Block, content: str) -> str:
        out: list[str] = []
        depth = block.level + 1
        tag = "ol" if block.ordered else "ul"
        while len(self._stack) > depth:
            out.append(self._close_one())
        if self._stack and self._stack[-1][0] != tag and len(self._stack) == depth:
            out.append(self._close_one())
        while len(self._stack) < depth:
            # Only the innermost list starts at this item's number.
            start = block.number if len(self._stack) + 1 == depth else None
            attr = f' start="{start}"' if tag == "ol" and start not in (None, 1) else ""
            out.append(f"<{tag}{attr}>")
            self._stack.append([tag, False])
        top = self._stack[-1]
        if top[1]:
            out.append("</li>")
        out.append(f"<li>{content}")
        top[1] = True
        return "".join(out)

    def close(self) -> str:
        return "".join(self._close_one() for _ in range(len(self._stack)))

    def _close_one(self) -> str:
        tag, li_open = self._stack.pop()
        return ("</li>" if li_open else "") + f"</{tag}>"


def render_html(doc: FormattedDocument) -> str:
    """Return Qt-compatible HTML for *doc* (no newlines in the markup: spacing is literal)."""
    parts: list[str] = ['<html><body style="white-space: pre-wrap;">']
    lists = _ListWriter()
    for block, _marker in list_markers(doc.blocks):
        if block.kind == "list_item":
            parts.append(lists.item(block, _spans_html(block.spans)))
            continue
        parts.append(lists.close())
        if block.kind == "table":
            parts.append(_table_html(block))
        elif block.kind == "figure":
            alt = html.escape(block.text.strip() or "image")
            parts.append(f'<p><i style="color:{MUTED_COLOUR};">[{alt}]</i></p>')
        elif block.kind == "heading":
            level = min(max(block.level or 1, 1), 6)
            parts.append(f"<h{level}>{_spans_html(block.spans)}</h{level}>")
        elif block.kind in ("page_header", "page_footer"):
            parts.append(f'<p style="color:{MUTED_COLOUR};">{_spans_html(block.spans)}</p>')
        elif block.kind == "caption":
            parts.append(f"<p><i>{_spans_html(block.spans)}</i></p>")
        elif block.text.strip():
            parts.append(f"<p>{_spans_html(block.spans)}</p>")
    parts.append(lists.close())
    parts.append("</body></html>")
    return "".join(parts)


def _table_html(block: Block) -> str:
    rows: list[str] = []
    for row_index, row in enumerate(block.rows):
        tag = "th" if row_index == 0 else "td"
        cells = "".join(f"<{tag}>{_spans_html(cell)}</{tag}>" for cell in row)
        rows.append(f"<tr>{cells}</tr>")
    return f'<table border="1" cellspacing="0" cellpadding="4">{"".join(rows)}</table>'


# ── Qt document -> model (in-place editing) ───────────────────────────────


def _style_of(fmt: QTextCharFormat) -> Style:
    style = Style.NONE
    if fmt.fontWeight() >= QFont.Weight.Bold.value:
        style |= Style.BOLD
    if fmt.fontItalic():
        style |= Style.ITALIC
    if fmt.fontUnderline():
        style |= Style.UNDERLINE
    if fmt.fontStrikeOut():
        style |= Style.STRIKE
    alignment = fmt.verticalAlignment()
    if alignment == QTextCharFormat.VerticalAlignment.AlignSuperScript:
        style |= Style.SUPERSCRIPT
    elif alignment == QTextCharFormat.VerticalAlignment.AlignSubScript:
        style |= Style.SUBSCRIPT
    return style


def _block_spans(block: QTextBlock) -> tuple[Span, ...]:
    spans: list[Span] = []
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid():
            # Qt stores soft line breaks as U+2028 and non-breaking blanks as U+00A0.
            text = fragment.text().replace(_LINE_SEPARATOR, "\n").replace(_NO_BREAK_SPACE, " ")
            spans.append(Span(text, _style_of(fragment.charFormat())))
        iterator += 1
    return merge_spans(spans)


def _is_figure_placeholder(block: QTextBlock, spans: tuple[Span, ...]) -> bool:
    """``True`` for the muted italic ``[alt]`` paragraph :func:`render_html` writes for figures."""
    text = "".join(span.text for span in spans).strip()
    if not (text.startswith("[") and text.endswith("]")) or not spans:
        return False
    if not all(Style.ITALIC in span.style for span in spans):
        return False
    iterator = block.begin()
    if iterator.atEnd():
        return False
    colour = iterator.fragment().charFormat().foreground().color()
    return colour.isValid() and colour.name().lower() == MUTED_COLOUR


def document_from_qt(document: QTextDocument) -> FormattedDocument:
    """Read the paragraphs, headings, lists and styled runs of *document* into the model."""
    blocks: list[Block] = []

    def walk(frame: QTextFrame) -> None:
        iterator = frame.begin()
        while not iterator.atEnd():
            child_frame = iterator.currentFrame()
            child_block = iterator.currentBlock()
            if isinstance(child_frame, QTextTable):
                blocks.append(_table_block(child_frame))
            elif child_frame is not None:
                walk(child_frame)
            elif child_block.isValid():
                _append_block(child_block)
            iterator += 1

    def _append_block(block: QTextBlock) -> None:
        spans = _block_spans(block)
        text_list = block.textList()
        heading = block.blockFormat().headingLevel()
        if text_list is not None:
            list_format = text_list.format()
            ordered = list_format.style() in (
                QTextListFormat.Style.ListDecimal,
                QTextListFormat.Style.ListLowerAlpha,
                QTextListFormat.Style.ListUpperAlpha,
                QTextListFormat.Style.ListLowerRoman,
                QTextListFormat.Style.ListUpperRoman,
            )
            number = list_format.start() + text_list.itemNumber(block) if ordered else None
            blocks.append(
                Block(
                    "list_item",
                    spans,
                    level=max(list_format.indent() - 1, 0),
                    ordered=ordered,
                    number=number,
                )
            )
        elif heading:
            # Headings are bold by definition; keep BOLD only when the user added it elsewhere.
            plain = tuple(Span(span.text, span.style & ~Style.BOLD) for span in spans)
            blocks.append(Block("heading", merge_spans(list(plain)), level=heading))
        elif _is_figure_placeholder(block, spans):
            alt = "".join(span.text for span in spans).strip()[1:-1]
            blocks.append(Block("figure", (Span(alt),)))
        elif "".join(span.text for span in spans).strip():
            blocks.append(Block("paragraph", spans))

    def _table_block(table: QTextTable) -> Block:
        rows = []
        for row in range(table.rows()):
            cells = []
            for column in range(table.columns()):
                cell = table.cellAt(row, column)
                cell_spans: list[Span] = []
                iterator = cell.begin()
                while not iterator.atEnd():
                    cell_block = iterator.currentBlock()
                    if cell_block.isValid():
                        if cell_spans:
                            cell_spans.append(Span("\n"))
                        cell_spans.extend(_block_spans(cell_block))
                    iterator += 1
                if row == 0:  # header cells are bold by definition
                    cell_spans = [Span(sp.text, sp.style & ~Style.BOLD) for sp in cell_spans]
                cells.append(merge_spans(cell_spans))
            rows.append(tuple(cells))
        return Block("table", rows=tuple(rows))

    walk(document.rootFrame())
    return FormattedDocument(compact_list_numbers(tuple(blocks)))


def _toggle(view: QTextEdit, attribute: str) -> None:
    """Toggle one character style on the selection (or at the cursor for new text)."""
    current = view.currentCharFormat()
    fmt = QTextCharFormat()
    # ``clearForeground()`` only drops the property from *fmt*, so merging it would leave the
    # old colour in place; an explicit empty brush resets the text to the default colour.
    default_colour = QBrush()
    if attribute == "bold":
        on = current.fontWeight() < QFont.Weight.Bold.value
        fmt.setFontWeight(QFont.Weight.Bold.value if on else QFont.Weight.Normal.value)
    elif attribute == "italic":
        fmt.setFontItalic(not current.fontItalic())
    elif attribute == "underline":
        fmt.setFontUnderline(not current.fontUnderline())
    elif attribute == "strike":
        on = not current.fontStrikeOut()
        fmt.setFontStrikeOut(on)
        fmt.setForeground(QColor(STRIKE_COLOUR) if on else default_colour)
    elif attribute == "insert":
        raised = current.verticalAlignment() == QTextCharFormat.VerticalAlignment.AlignSuperScript
        fmt.setVerticalAlignment(
            QTextCharFormat.VerticalAlignment.AlignNormal
            if raised
            else QTextCharFormat.VerticalAlignment.AlignSuperScript
        )
        fmt.setForeground(default_colour if raised else QColor(INSERT_COLOUR))
    view.mergeCurrentCharFormat(fmt)


_SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+B", "bold"),
    ("Ctrl+I", "italic"),
    ("Ctrl+U", "underline"),
    ("Ctrl+Shift+X", "strike"),
    ("Ctrl+Shift+=", "insert"),
)


def install_formatting_shortcuts(view: QTextEdit) -> None:
    """Bind Ctrl+B/I/U, Ctrl+Shift+X (crossed out) and Ctrl+Shift+= (inserted) on *view*."""
    for sequence, attribute in _SHORTCUTS:
        shortcut = QShortcut(QKeySequence(sequence), view)
        shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        shortcut.activated.connect(lambda attr=attribute: _toggle(view, attr))


class FormattedTextEdit(QTextEdit):
    """Rich-text editor for Markdown text.

    ``QTextCursor.insertBlock`` copies the character format, so the line typed after a
    figure placeholder would inherit its muted italics and be stored as ``*...*``.  Enter at
    the end of a placeholder therefore starts an unstyled paragraph.  (Qt itself already
    resets the heading level and weight for the line after a heading.)
    """

    focus_lost = Signal()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        modifiers = e.modifiers()
        starts_new_block = e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            modifiers
            & (
                Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
            )
        )
        plain_next = starts_new_block and self._cursor_ends_figure_placeholder()
        super().keyPressEvent(e)
        if plain_next:
            cursor = self.textCursor()
            cursor.joinPreviousEditBlock()  # one undo step together with the new line
            cursor.setBlockFormat(QTextBlockFormat())
            cursor.setBlockCharFormat(QTextCharFormat())
            cursor.endEditBlock()
            self.setCurrentCharFormat(QTextCharFormat())

    def focusOutEvent(self, e: QFocusEvent) -> None:
        super().focusOutEvent(e)
        self.focus_lost.emit()

    def _cursor_ends_figure_placeholder(self) -> bool:
        """``True`` if the cursor sits at the end of a figure placeholder paragraph."""
        cursor = self.textCursor()
        if cursor.hasSelection() or not cursor.atBlockEnd():
            return False
        block = cursor.block()
        return _is_figure_placeholder(block, _block_spans(block))
