"""Text renderers for :class:`~teachers_teammate.domain.formatted_text.FormattedDocument`.

:class:`MarkdownRenderer` produces the canonical stored notation (CommonMark + GFM
strikethrough/tables + inline ``<sup>``/``<sub>``/``<u>``); :class:`PlainTextRenderer`
produces markup-free text for diffs and benchmark metrics.
"""

from __future__ import annotations

from collections.abc import Iterator
import re
import string
import unicodedata

from ...domain.formatted_text import (
    LIST_INDENT,
    Block,
    FormattedDocument,
    Span,
    Style,
    flat_chunks,
    join_chunks,
    list_markers,
    merge_spans,
    strip_spans,
)
from ...interfaces import TextRenderer

# Inline styles that always become HTML tags, outermost first.  They wrap the Markdown
# markers so that ``<sup>**x**</sup>`` parses as one superscript run containing strong text.
_HTML_STYLES: tuple[tuple[Style, str], ...] = (
    (Style.UNDERLINE, "u"),
    (Style.SUPERSCRIPT, "sup"),
    (Style.SUBSCRIPT, "sub"),
)

_ENTITY_RE = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
_STAR_RUN_RE = re.compile(r"\*+")
_UNDERSCORE_RUN_RE = re.compile(r"_+")
_TILDE_RUN_RE = re.compile(r"~{2,}")
# A link reference definition (``[label]: url``) at the start of a line would swallow the line.
_DEFINITION_RE = re.compile(r"^[ \t]*(?P<mark>\[)[^\]]*\]:", re.MULTILINE)

# Line-start constructs that would turn plain text into block markup.  The group named
# ``mark`` is the single character that gets a backslash.
_LINE_START_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[ \t]*(?P<mark>#)#{0,5}(?:\s|$)"),
    re.compile(r"^[ \t]*(?P<mark>>)"),
    re.compile(r"^[ \t]*(?P<mark>[-+*])(?:\s|$)"),
    re.compile(r"^[ \t]*\d{1,9}(?P<mark>[.)])(?:\s|$)"),
    re.compile(r"^[ \t]*(?P<mark>[-*_])(?:[ \t]*[-*_]){2,}[ \t]*$"),
    re.compile(r"^[ \t]*(?P<mark>[-=])[-=]*[ \t]*$"),
)


def _is_punct(char: str) -> bool:
    """Unicode punctuation as CommonMark's flanking rules define it (categories P and S)."""
    return char in string.punctuation or unicodedata.category(char)[0] in "PS"


def _edge_char(text: str, index: int) -> str:
    """Return ``text[index]``, or a blank when *index* is outside *text*."""
    return text[index] if 0 <= index < len(text) else " "


def _underscore_run_is_literal(text: str, start: int, end: int) -> bool:
    """``True`` if CommonMark can neither open nor close emphasis with this ``_`` run.

    Intraword underscores (``max_mustermann@web.de``) are literal, so they need no escape.
    """
    before = _edge_char(text, start - 1)
    after = _edge_char(text, end)
    left = not after.isspace() and (not _is_punct(after) or before.isspace() or _is_punct(before))
    right = not before.isspace() and (not _is_punct(before) or after.isspace() or _is_punct(after))
    can_open = left and (not right or _is_punct(before))
    can_close = right and (not left or _is_punct(after))
    return not (can_open or can_close)


def _fragile_gaps(spans: tuple[Span, ...]) -> frozenset[int]:
    """Return the text offsets whose neighbours change once the spans are wrapped in markers.

    Markers sit at style boundaries and just outside a styled span's edge whitespace, so a
    ``*``/``_`` run touching such an offset is no longer flanked by what the plain text says.
    """
    gaps: set[int] = set()
    offset = 0
    for span in spans:
        end = offset + len(span.text)
        if span.style != Style.NONE:
            gaps.update((offset, end))
            stripped = span.text.strip()
            if stripped:
                lead = len(span.text) - len(span.text.lstrip())
                gaps.update((offset + lead, end - (len(span.text) - len(span.text.rstrip()))))
        offset = end
    return frozenset(gaps)


def _touches(fragile: frozenset[int], start: int, end: int) -> bool:
    """``True`` if a fragile offset lies at or inside the run ``text[start:end]``."""
    return any(start <= gap <= end for gap in fragile)


def _needs_escape(text: str, index: int, *, in_table: bool) -> bool:
    """``True`` if the single character ``text[index]`` is markup on its own."""
    char = text[index]
    if char in "\\`":
        return True
    if char == "<":
        return index + 1 < len(text) and (text[index + 1].isalpha() or text[index + 1] in "/!?")
    if char == "&":
        return _ENTITY_RE.match(text, index) is not None
    if char == "|":
        return in_table
    if char == "]":
        # ``[text](url)`` / ``![alt](url)`` would become a link or image.
        return text.startswith("(", index + 1)
    return False


def _line_start_marks(text: str) -> Iterator[int]:
    """Yield the offsets of characters that would turn a line into block markup."""
    line_start = 0
    while line_start <= len(text):
        line_end = text.find("\n", line_start)
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        for pattern in _LINE_START_RES:
            match = pattern.match(line)
            if match:
                yield line_start + match.start("mark")
                break
        line_start = line_end + 1
    for match in _DEFINITION_RE.finditer(text):
        yield match.start("mark")


def _delimiter_run_offsets(text: str, fragile: frozenset[int]) -> Iterator[int]:
    """Yield the offsets of ``*``/``_``/``~~`` runs that could open or close emphasis."""
    for match in _STAR_RUN_RE.finditer(text):
        start, end = match.span()
        # A delimiter run flanked by whitespace on both sides cannot open or close emphasis.
        flanked = _edge_char(text, start - 1).isspace() and _edge_char(text, end).isspace()
        if _touches(fragile, start, end) or not flanked:
            yield from range(start, end)
    for match in _UNDERSCORE_RUN_RE.finditer(text):
        start, end = match.span()
        # Blanks written as ``_____`` keep their length: flanked by whitespace they are literal.
        if _touches(fragile, start, end) or not _underscore_run_is_literal(text, start, end):
            yield from range(start, end)
    for match in _TILDE_RUN_RE.finditer(text):
        yield from range(*match.span())


def _escape_chars(text: str, *, in_table: bool, fragile: frozenset[int] = frozenset()) -> list[str]:
    """Return the Markdown-safe replacement for every character of *text*.

    Escapes only what CommonMark would otherwise interpret, so ordinary prose (blanks like
    ``_____``, snake_case words, e-mail addresses) stays free of backslashes.  *fragile*
    lists offsets where wrapping markers will change the neighbours of a delimiter run.
    """
    marked = set(_delimiter_run_offsets(text, fragile))
    marked.update(
        index for index in range(len(text)) if _needs_escape(text, index, in_table=in_table)
    )
    marked.update(_line_start_marks(text))
    return ["\\" + char if index in marked else char for index, char in enumerate(text)]


# ── inline styles ────────────────────────────────────────────────────────────

_Run = tuple[str, Style]
"""Already-escaped text and the style it was written in."""

# Styles rendered by grouping consecutive runs (outermost first); bold/italic come last.
_GROUP_LEVELS: tuple[Style, ...] = (
    Style.UNDERLINE,
    Style.SUPERSCRIPT,
    Style.SUBSCRIPT,
    Style.STRIKE,
)
_HTML_TAG: dict[Style, str] = dict(_HTML_STYLES)
_EMPHASIS = Style.BOLD | Style.ITALIC


def _delimiter_is_safe(char: str, inner: str, before: str, after: str) -> bool:
    """``True`` if a Markdown ``*``/``~`` delimiter run around *inner* parses as emphasis.

    Implements CommonMark's flanking rules for the characters actually next to the run, and
    refuses a run that would merge with a neighbouring run of the same character.
    """
    if char in (inner[0], inner[-1], before, after):
        return False
    opens = not _is_punct(inner[0]) or not before or before.isspace() or _is_punct(before)
    closes = not _is_punct(inner[-1]) or not after or after.isspace() or _is_punct(after)
    return opens and closes


def _trim_runs(runs: list[_Run]) -> tuple[str, list[_Run], str]:
    """Split edge whitespace off *runs*: ``(leading, core runs, trailing)``."""
    core = list(runs)
    lead = ""
    while core:
        text, style = core[0]
        stripped = text.lstrip()
        lead += text[: len(text) - len(stripped)]
        if stripped:
            core[0] = (stripped, style)
            break
        core.pop(0)
    trail = ""
    while core:
        text, style = core[-1]
        stripped = text.rstrip()
        trail = text[len(stripped) :] + trail
        if stripped:
            core[-1] = (stripped, style)
            break
        core.pop()
    return lead, core, trail


def _following_char(run: _Run, remaining: tuple[Style, ...]) -> str:
    """Return the character *run* will start with once it is rendered (worst case).

    Edge whitespace is written outside any marker; otherwise a styled run starts with the
    marker of its outermost style still to be rendered (``~``/``*``, assuming Markdown).
    """
    text, style = run
    if text[:1].isspace():
        return text[:1]
    for flag in remaining:
        if flag in style:
            return "~" if flag == Style.STRIKE else "<"
    return "*" if style & _EMPHASIS else text[:1]


def _wrap_emphasis(core: str, style: Style, before: str, after: str) -> str:
    """Wrap *core* in bold/italic markers, using HTML tags where Markdown cannot parse."""
    if Style.BOLD in style and Style.ITALIC in style:
        mark, open_tag, close_tag = "***", "<b><i>", "</i></b>"
    elif Style.BOLD in style:
        mark, open_tag, close_tag = "**", "<b>", "</b>"
    else:
        mark, open_tag, close_tag = "*", "<i>", "</i>"
    if _delimiter_is_safe("*", core, before, after):
        return mark + core + mark
    return open_tag + core + close_tag


def _render_emphasis(runs: list[_Run], before: str, after: str) -> str:
    """Render runs whose only styles are bold/italic (no nesting between them)."""
    out: list[str] = []
    index = 0
    while index < len(runs):
        style = runs[index][1]
        end = index
        while end < len(runs) and runs[end][1] == style:
            end += 1
        text = "".join(run_text for run_text, _ in runs[index:end])
        following = _following_char(runs[end], ()) if end < len(runs) else after
        if not style:
            out.append(text)
        else:
            lead, core_runs, trail = _trim_runs([(text, style)])
            if not core_runs:
                out.append(text)
            else:
                context_before = lead[-1:] or "".join(out)[-1:] or before
                context_after = trail[:1] or following
                out.append(
                    lead
                    + _wrap_emphasis(core_runs[0][0], style, context_before, context_after)
                    + trail
                )
        index = end
    return "".join(out)


def _render_runs(runs: list[_Run], level: int, before: str, after: str) -> str:
    """Render *runs*, grouping neighbours that share the style flag of nesting *level*."""
    if level == len(_GROUP_LEVELS):
        return _render_emphasis(runs, before, after)
    flag = _GROUP_LEVELS[level]
    remaining = _GROUP_LEVELS[level:]
    out: list[str] = []
    index = 0
    while index < len(runs):
        has_flag = flag in runs[index][1]
        end = index
        while end < len(runs) and (flag in runs[end][1]) == has_flag:
            end += 1
        chunk = runs[index:end]
        context_before = "".join(out)[-1:] or before
        context_after = after
        if end < len(runs):
            context_after = _following_char(runs[end], remaining)
        if not has_flag:
            out.append(_render_runs(chunk, level + 1, context_before, context_after))
        else:
            stripped = [(text, style & ~flag) for text, style in chunk]
            lead, core_runs, trail = _trim_runs(stripped)
            if not core_runs:
                out.append("".join(text for text, _ in chunk))
            else:
                inner = _render_runs(core_runs, level + 1, "", "")
                out.append(
                    lead
                    + _wrap_group(
                        flag,
                        inner,
                        lead[-1:] or context_before,
                        trail[:1] or context_after,
                    )
                    + trail
                )
        index = end
    return "".join(out)


def _wrap_group(flag: Style, inner: str, before: str, after: str) -> str:
    """Wrap *inner* in the markers for *flag* (HTML tag, or ``~~`` when it parses)."""
    if flag == Style.STRIKE:
        if _delimiter_is_safe("~", inner, before, after):
            return f"~~{inner}~~"
        return f"<s>{inner}</s>"
    tag = _HTML_TAG[flag]
    return f"<{tag}>{inner}</{tag}>"


def render_spans_markdown(spans: tuple[Span, ...], *, in_table: bool = False) -> str:
    """Return *spans* as Markdown inline text (line breaks stay ``"\\n"``)."""
    spans = tuple(merge_spans([span for span in spans if span.text]))
    if not spans:
        return ""
    full = "".join(span.text for span in spans)
    replacements = _escape_chars(full, in_table=in_table, fragile=_fragile_gaps(spans))
    runs: list[_Run] = []
    offset = 0
    for span in spans:
        end = offset + len(span.text)
        runs.append(("".join(replacements[offset:end]), span.style))
        offset = end
    return _render_runs(runs, 0, "", "")


def _opens_numbered_list(block: Block, marker: str, previous: Block) -> bool:
    """``True`` if *block* starts a new list numbered other than 1 right below *previous*.

    CommonMark lets only a list starting at 1 interrupt a paragraph, so such a list needs a
    blank line before it or its first line is read as a continuation of the item above.
    """
    return (
        block.ordered
        and marker != "1. "
        and not (previous.ordered and previous.level == block.level)
    )


class MarkdownRenderer(TextRenderer):
    """Render a document as canonical Markdown."""

    def render(self, doc: FormattedDocument) -> str:
        chunks: list[tuple[str, bool]] = []  # (markdown, tight)
        previous: Block | None = None
        for block, marker in list_markers(doc.blocks):
            rendered = self._render_block(block, marker)
            if not rendered:
                continue
            tight = (
                block.kind == "list_item"
                and previous is not None
                and previous.kind == "list_item"
                and not _opens_numbered_list(block, marker, previous)
            )
            chunks.append((rendered, tight))
            previous = block
        return join_chunks(chunks)

    def _render_block(self, block: Block, marker: str) -> str:
        if block.kind == "table":
            return self._render_table(block)
        if block.kind == "figure":
            alt = block.text.replace("[", "").replace("]", "").replace("\n", " ").strip()
            return f"![{alt}]()"
        spans = strip_spans(block.spans)
        if not spans:
            return ""
        text = render_spans_markdown(spans)
        if block.kind == "heading":
            level = min(max(block.level or 1, 1), 6)
            return f"{'#' * level} {text.replace(chr(10), ' ')}"
        if block.kind == "list_item":
            indent = LIST_INDENT * block.level
            continuation = "\n" + indent + " " * len(marker)
            return indent + marker + text.replace("\n", continuation)
        return text

    @staticmethod
    def _render_table(block: Block) -> str:
        rows = [row for row in block.rows if row]
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        lines: list[str] = []
        for row_index, row in enumerate(rows):
            cells = [
                render_spans_markdown(strip_spans(cell), in_table=True).replace("\n", "<br>")
                for cell in row
            ]
            cells += [""] * (width - len(cells))
            lines.append("| " + " | ".join(cells) + " |")
            if row_index == 0:
                lines.append("|" + " --- |" * width)
        return "\n".join(lines)


class PlainTextRenderer(TextRenderer):
    """Render a document as markup-free text (struck-out text is kept)."""

    def render(self, doc: FormattedDocument) -> str:
        return join_chunks(
            ("".join(span.text for span in spans), is_item) for spans, is_item in flat_chunks(doc)
        )
