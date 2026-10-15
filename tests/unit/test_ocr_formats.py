"""Unit tests for teachers_teammate.infrastructure.ocr_formats and the formatted-text model."""

from __future__ import annotations

import re

import pytest

from teachers_teammate.application.formatting import TextFormatter
from teachers_teammate.domain.formatted_text import (
    Block,
    FormattedDocument,
    Span,
    Style,
    list_markers,
    merge_spans,
    normalize_text_format,
    strip_spans,
)
from teachers_teammate.infrastructure.ocr_formats import (
    ChandraHtmlParser,
    DropPageFurniture,
    MarkdownParser,
    MarkdownRenderer,
    OcrOutputPostprocessor,
    PlainTextParser,
    PlainTextRenderer,
    looks_like_markup,
    parse_stored_text,
    plain_text_of,
    upgrade_legacy_text,
)

CHANDRA_SAMPLE = (
    '<div data-bbox="133 102 403 118" data-label="Text"><p>- Include the following ideas:</p></div>'
    '<div data-bbox="113 139 691 156" data-label="Text"><p><b>branches - to build a campfire'
    " - barbecue - smokejumper</b></p></div>"
    '<div data-bbox="744 187 910 204" data-label="Text"><p>7 a.m. Mon. 15.2026</p></div>'
    '<div data-bbox="108 214 243 237" data-label="Text"><p>Dear diary,</p></div>'
    '<div data-bbox="103 233 983 497" data-label="Text"><p>today I arrived at the airport in the'
    " USA. An unaccompanied minor help me with my <sup>the passport</sup> <del>backpack</del>"
    " and I was happy. Curchie there are very <sup>much</sup> mountains.</p></div>"
    '<div data-bbox="318 909 600 929" data-label="Text"><p>In total: _____ / 62 BE</p></div>'
    '<div data-bbox="645 902 838 930" data-label="Text"><p><img alt="flower icon"'
    ' data-bbox="650 905 690 930"/> Hn / Me / Wa</p></div>'
    '<div data-bbox="853 946 871 959" data-label="Page-Footer"><p>4</p></div>'
)


def _paragraph(*spans: Span) -> Block:
    return Block("paragraph", tuple(spans))


# ── Style / model helpers ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [("markdown", "markdown"), ("plain", "plain"), ("", "plain"), (None, "plain"), ("x", "plain")],
)
def test_normalize_text_format_falls_back_to_plain(value: str | None, expected: str) -> None:
    """
    Given  a stored format flag that may be missing or unknown
    When   normalize_text_format is called
    Then   only "markdown" is kept and everything else is "plain"
    """
    assert normalize_text_format(value) == expected


# ── Chandra HTML parser ───────────────────────────────────────────────────


def test_chandra_parser_detects_data_attributes() -> None:
    """
    Given  Chandra layout HTML and an ordinary HTML snippet without data-bbox/data-label
    When   can_parse is asked
    Then   only the Chandra output is accepted
    """
    parser = ChandraHtmlParser()

    assert parser.can_parse(CHANDRA_SAMPLE)
    assert not parser.can_parse("<p>Just <b>some</b> html</p>")
    assert not parser.can_parse("plain text")


def test_chandra_parser_maps_labels_and_styles() -> None:
    """
    Given  the Chandra sample with a struck-out word, an insertion and a page footer
    When   it is parsed
    Then   styles and block kinds land in the document model
    """
    doc = ChandraHtmlParser().parse(CHANDRA_SAMPLE)

    kinds = [block.kind for block in doc.blocks]
    assert kinds[-1] == "page_footer"
    assert "figure" in kinds
    assert doc.blocks[0].text == "- Include the following ideas:"

    diary = next(block for block in doc.blocks if block.text.startswith("today"))
    by_style = {span.style: span.text for span in diary.spans if span.style != Style.NONE}
    assert by_style[Style.SUPERSCRIPT] == "much"  # last superscript wins in the dict
    assert any(s.style == Style.STRIKE and s.text == "backpack" for s in diary.spans)
    assert any(s.style == Style.SUPERSCRIPT and s.text == "the passport" for s in diary.spans)

    bold = doc.blocks[1]
    assert all(span.style == Style.BOLD for span in bold.spans)


def test_chandra_parser_turns_inline_image_into_figure_block() -> None:
    """
    Given  a paragraph containing an <img alt="flower icon"> followed by text
    When   it is parsed
    Then   a figure block with the alt text precedes the paragraph with the remaining text
    """
    doc = ChandraHtmlParser().parse(
        '<div data-bbox="1 2 3 4" data-label="Text"><p><img alt="flower icon"/> Hn / Me</p></div>'
    )

    assert [b.kind for b in doc.blocks] == ["figure", "paragraph"]
    assert doc.blocks[0].text == "flower icon"
    assert doc.blocks[1].text == "Hn / Me"


def test_chandra_parser_reads_tables_lists_and_headings() -> None:
    """
    Given  Table, List-Group and Section-Header blocks
    When   they are parsed
    Then   they become table rows, list items and a heading
    """
    html = (
        '<div data-bbox="0 0 1 1" data-label="Section-Header"><h2>Title</h2></div>'
        '<div data-bbox="0 0 1 1" data-label="List-Group"><ul><li>one</li>'
        "<li>two<ul><li>nested</li></ul></li></ul></div>"
        '<div data-bbox="0 0 1 1" data-label="Table"><table><tr><th>A</th><th>B</th></tr>'
        "<tr><td>1</td><td>2</td></tr></table></div>"
    )

    doc = ChandraHtmlParser().parse(html)

    assert doc.blocks[0].kind == "heading"
    assert doc.blocks[0].level == 2
    items = [b for b in doc.blocks if b.kind == "list_item"]
    assert [(b.text, b.level) for b in items] == [("one", 0), ("two", 0), ("nested", 1)]
    table = next(b for b in doc.blocks if b.kind == "table")
    assert [[cell[0].text for cell in row] for row in table.rows] == [["A", "B"], ["1", "2"]]


def test_chandra_parser_collapses_html_whitespace() -> None:
    """
    Given  a paragraph with newlines and runs of blanks inside the markup
    When   it is parsed
    Then   whitespace is collapsed like a browser would and <br> becomes a line break
    """
    doc = ChandraHtmlParser().parse(
        '<div data-label="Text"><p>one\n   two<br/>three  <b> four</b></p></div>'
    )

    assert doc.blocks[0].text == "one two\nthree four"


# ── Markdown parser ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "This is **bold** text",
        "a ~~struck~~ word",
        "# Heading\n\nbody",
        "| A | B |\n| --- | --- |\n| 1 | 2 |",
        "wrote <sup>this</sup> above",
        "![alt](x.png)",
    ],
)
def test_markdown_parser_detects_strong_markers(text: str) -> None:
    """
    Given  text containing a Markdown construct that plain OCR output never produces
    When   can_parse is asked
    Then   the Markdown parser accepts it
    """
    assert MarkdownParser().can_parse(text)


@pytest.mark.parametrize(
    "text",
    ["- one\n- two", "2 * 3 = 6", "Hello world", "a_b_c", "price < 5 > 2", "Name: _____"],
)
def test_markdown_parser_ignores_ordinary_text(text: str) -> None:
    """
    Given  plain text that merely contains list dashes, asterisks or underscores
    When   can_parse is asked
    Then   the Markdown parser declines so plain OCR text stays verbatim
    """
    assert not MarkdownParser().can_parse(text)


def test_markdown_parser_reads_inline_styles() -> None:
    """
    Given  Markdown with bold, italic, struck text and inline <sup>/<sub>/<u> tags
    When   it is parsed
    Then   every run carries the matching style flag
    """
    doc = MarkdownParser().parse("**b** *i* ~~s~~ <sup>p</sup> <sub>q</sub> <u>u</u> plain")

    styles = {span.text.strip(): span.style for span in doc.blocks[0].spans if span.text.strip()}
    assert styles["b"] == Style.BOLD
    assert styles["i"] == Style.ITALIC
    assert styles["s"] == Style.STRIKE
    assert styles["p"] == Style.SUPERSCRIPT
    assert styles["q"] == Style.SUBSCRIPT
    assert styles["u"] == Style.UNDERLINE
    assert styles["plain"] == Style.NONE


def test_markdown_parser_reads_blocks() -> None:
    """
    Given  Markdown with a heading, paragraph, nested lists, a table and an image
    When   it is parsed
    Then   each block kind, list depth/ordering and table cell is recovered
    """
    text = (
        "## Head\n\nPara one\nsecond line\n\n- a\n    1. nested\n- b\n\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n![flower]()"
    )

    doc = MarkdownParser().parse(text)

    assert doc.blocks[0] == Block("heading", (Span("Head"),), level=2)
    assert doc.blocks[1].text == "Para one\nsecond line"
    items = [b for b in doc.blocks if b.kind == "list_item"]
    assert [(b.text, b.level, b.ordered) for b in items] == [
        ("a", 0, False),
        ("nested", 1, True),
        ("b", 0, False),
    ]
    table = next(b for b in doc.blocks if b.kind == "table")
    assert [[c[0].text for c in row] for row in table.rows] == [["A", "B"], ["1", "2"]]
    assert doc.blocks[-1].kind == "figure"
    assert doc.blocks[-1].text == "flower"


# ── Renderers ─────────────────────────────────────────────────────────────


def test_markdown_renderer_wraps_styles_and_keeps_edge_whitespace_outside() -> None:
    """
    Given  styled spans where a bold run ends with a blank
    When   rendered as Markdown
    Then   the marker hugs the word (valid Markdown) and the blank stays outside
    """
    doc = FormattedDocument(
        (
            _paragraph(
                Span("a "),
                Span("bold ", Style.BOLD),
                Span("gone", Style.STRIKE),
                Span("up", Style.SUPERSCRIPT),
            ),
        )
    )

    assert MarkdownRenderer().render(doc) == "a **bold** ~~gone~~<sup>up</sup>"


def test_markdown_renderer_leaves_blank_underscores_unescaped() -> None:
    """
    Given  a fill-in-the-blank line like "In total: _____ / 62 BE"
    When   rendered as Markdown
    Then   no backslashes are added (whitespace-flanked underscores are inert)
    """
    doc = FormattedDocument((_paragraph(Span("In total: _____ / 62 BE")),))

    assert MarkdownRenderer().render(doc) == "In total: _____ / 62 BE"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- Include ideas", "\\- Include ideas"),
        ("1. first", "1\\. first"),
        ("# not a heading", "\\# not a heading"),
        ("snake_case_name", "snake_case_name"),
        ("max_mustermann@web.de", "max_mustermann@web.de"),
        ("_emphasis_ here", "\\_emphasis\\_ here"),
        ("see [1]: Quelle", "see [1]: Quelle"),
        ("[1]: Quelle", "\\[1]: Quelle"),
        ("[Link](http://x.y)", "[Link\\](http://x.y)"),
        ("![c](d)", "![c\\](d)"),
        ("2*3", "2\\*3"),
        ("a ~~b~~", "a \\~\\~b\\~\\~"),
        ("use <sup> tag", "use \\<sup> tag"),
        ("C:\\temp", "C:\\\\temp"),
    ],
)
def test_markdown_renderer_escapes_constructs_that_would_change_meaning(
    text: str, expected: str
) -> None:
    """
    Given  plain text containing characters Markdown would interpret
    When   rendered as Markdown
    Then   exactly those characters are backslash-escaped
    """
    assert MarkdownRenderer().render(FormattedDocument((_paragraph(Span(text)),))) == expected


@pytest.mark.parametrize(
    "text",
    [
        "- Include ideas",
        "1. first\n2) second",
        "snake_case_name and **not bold** and ~~no~~",
        "In total: _____ / 62 BE",
        "# hash\n> quote\n---",
        "<b>tags</b> &amp; entities",
        "back\\slash `tick`",
    ],
)
def test_markdown_round_trip_preserves_literal_text(text: str) -> None:
    """
    Given  literal text rendered to Markdown by the app
    When   the Markdown is parsed again
    Then   the original text comes back unchanged
    """
    markdown = MarkdownRenderer().render(FormattedDocument((_paragraph(Span(text)),)))

    reparsed = MarkdownParser().parse(markdown)

    assert "\n\n".join(b.text for b in reparsed.blocks) == text


def test_markdown_round_trip_preserves_styles_and_structure() -> None:
    """
    Given  a document with every style, a heading, lists, a table and a figure
    When   it is rendered to Markdown and parsed back
    Then   the parsed document equals the original
    """
    doc = FormattedDocument(
        (
            Block("heading", (Span("Title"),), level=1),
            _paragraph(
                Span("plain "),
                Span("bold", Style.BOLD),
                Span(" "),
                Span("both", Style.BOLD | Style.ITALIC),
                Span(" "),
                Span("struck", Style.STRIKE),
                Span(" "),
                Span("up", Style.SUPERSCRIPT),
                Span(" "),
                Span("down", Style.SUBSCRIPT),
                Span(" "),
                Span("under", Style.UNDERLINE),
            ),
            Block("list_item", (Span("one"),)),
            Block("list_item", (Span("sub"),), level=1, ordered=True),
            Block("figure", (Span("flower icon"),)),
            Block(
                "table",
                rows=(
                    ((Span("A"),), (Span("B", Style.BOLD),)),
                    ((Span("1"),), (Span("2"),)),
                ),
            ),
        )
    )

    markdown = MarkdownRenderer().render(doc)

    assert MarkdownParser().parse(markdown) == doc


def test_markdown_renderer_numbers_ordered_lists_and_restarts_after_other_blocks() -> None:
    """
    Given  two ordered runs separated by a paragraph
    When   rendered
    Then   each run is numbered from 1
    """
    item = lambda text: Block("list_item", (Span(text),), ordered=True)  # noqa: E731
    doc = FormattedDocument((item("a"), item("b"), _paragraph(Span("x")), item("c")))

    assert MarkdownRenderer().render(doc) == "1. a\n2. b\n\nx\n\n1. c"


def test_plain_renderer_strips_markup_but_keeps_struck_text() -> None:
    """
    Given  a document with a struck word, an insertion, a list and a table
    When   rendered as plain text
    Then   there are no markers and the crossed-out word is still present
    """
    doc = FormattedDocument(
        (
            _paragraph(
                Span("my "),
                Span("backpack", Style.STRIKE),
                Span(" bag", Style.BOLD),
                Span("the passport", Style.SUPERSCRIPT),
            ),
            Block("list_item", (Span("one"),)),
            Block("table", rows=(((Span("A"),), (Span("B"),)),)),
            Block("figure", (Span("flower icon"),)),
        )
    )

    assert PlainTextRenderer().render(doc) == (
        "my backpack bagthe passport\n\n- one\n\nA | B\n\n[flower icon]"
    )


def test_plain_text_of_returns_plain_text_untouched() -> None:
    """
    Given  stored text flagged plain that contains Markdown-looking characters
    When   plain_text_of is called
    Then   it is returned verbatim; only markdown-flagged text is re-rendered
    """
    assert plain_text_of("**not bold**", "plain") == "**not bold**"
    assert plain_text_of("**bold** ~~gone~~", "markdown") == "bold gone"


def test_parse_stored_text_honours_the_format_flag() -> None:
    """
    Given  the same text stored as plain and as markdown
    When   parse_stored_text is called
    Then   only the markdown flag interprets the markers
    """
    plain = parse_stored_text("**x**", "plain")
    rich = parse_stored_text("**x**", "markdown")

    assert plain.blocks[0].spans == (Span("**x**"),)
    assert rich.blocks[0].spans == (Span("x", Style.BOLD),)


# ── Plain parser ──────────────────────────────────────────────────────────


def test_plain_parser_splits_paragraphs_on_blank_lines_and_keeps_line_breaks() -> None:
    """
    Given  text with two paragraphs, one of them multi-line
    When   it is parsed by the plain parser
    Then   there are two paragraph blocks and single newlines are preserved
    """
    doc = PlainTextParser().parse("line one\nline two\n\n\nsecond paragraph\n")

    assert [b.text for b in doc.blocks] == ["line one\nline two", "second paragraph"]


# ── Postprocessor ─────────────────────────────────────────────────────────


def test_postprocessor_keeps_plain_text_verbatim() -> None:
    """
    Given  Tesseract-style plain output
    When   process_pages is called
    Then   text is unchanged and flagged plain
    """
    result = OcrOutputPostprocessor().process_pages(["- first\n- second\n\nthird *star*"])

    assert result.text == "- first\n- second\n\nthird *star*"
    assert result.text_format == "plain"


def test_postprocessor_converts_chandra_html_to_markdown_and_drops_page_footer() -> None:
    """
    Given  the Chandra sample
    When   process_pages is called
    Then   the result is Markdown with styles and without the page number
    """
    result = OcrOutputPostprocessor().process_pages([CHANDRA_SAMPLE])

    assert result.text_format == "markdown"
    assert "~~backpack~~" in result.text
    assert "<sup>the passport</sup>" in result.text
    assert "**branches - to build a campfire - barbecue - smokejumper**" in result.text
    assert "\\- Include the following ideas:" in result.text
    assert "In total: _____ / 62 BE" in result.text
    assert "![flower icon]()" in result.text
    assert not result.text.rstrip().endswith("4")
    assert "data-bbox" not in result.text


def test_postprocessor_labels_pages_like_the_plain_pipeline() -> None:
    """
    Given  two plain pages
    When   process_pages is called with and without page labels
    Then   labelled output matches the existing "Page N:" layout; unlabelled uses blank lines
    """
    processor = OcrOutputPostprocessor()

    labelled = processor.process_pages(["a", "b"])
    unlabelled = processor.process_pages(["a", "b"], label_pages=False)

    assert labelled.text == "Page 1:\na\nPage 2:\nb"
    assert unlabelled.text == "a\n\nb"


def test_postprocessor_renders_mixed_pages_as_markdown() -> None:
    """
    Given  one Markdown page and one plain page
    When   process_pages is called
    Then   the whole document is Markdown and the plain page keeps its literal text
    """
    result = OcrOutputPostprocessor().process_pages(["**bold** page", "- plain page"])

    assert result.text_format == "markdown"
    assert result.text == "Page 1:\n\n**bold** page\n\nPage 2:\n\n\\- plain page"


def test_postprocessor_falls_back_to_plain_when_a_parser_raises() -> None:
    """
    Given  a parser that accepts the text but raises while parsing
    When   process_pages is called
    Then   the next parser is used and OCR is not failed
    """

    class _Exploding(ChandraHtmlParser):
        def parse(self, text: str) -> FormattedDocument:
            raise ValueError("boom")

    result = OcrOutputPostprocessor(parsers=[_Exploding(), PlainTextParser()]).process_pages(
        ['<div data-label="Text">x</div>']
    )

    assert result.text_format == "plain"
    assert result.text == '<div data-label="Text">x</div>'


def test_drop_page_furniture_removes_page_numbers_only() -> None:
    """
    Given  a document with a running title, a student header, a body paragraph and numbered
           footers in several notations
    When   DropPageFurniture is applied
    Then   the page numbers are gone and all other header/footer text is kept
    """
    doc = FormattedDocument(
        (
            Block("page_header", (Span("Name: Max Muster, 7b"),)),
            Block("page_header", (Span("Running title"),)),
            _paragraph(Span("body")),
            Block("page_footer", (Span("4"),)),
            Block("page_footer", (Span("- 5 -"),)),
            Block("page_footer", (Span("Seite 6 von 10"),)),
            Block("page_footer", (Span("Klassenarbeit 2"),)),
        )
    )

    kept = DropPageFurniture().apply(doc).blocks

    assert [block.text for block in kept] == [
        "Name: Max Muster, 7b",
        "Running title",
        "body",
        "Klassenarbeit 2",
    ]


def test_drop_page_furniture_keeps_a_page_that_has_only_a_page_number() -> None:
    """
    Given  a page whose only block is a footer holding the page number
    When   DropPageFurniture is applied
    Then   the page is not emptied (an empty page would abort the whole file)
    """
    doc = FormattedDocument((Block("page_footer", (Span("3"),)),))

    assert DropPageFurniture().apply(doc) == doc


def test_postprocessor_keeps_student_header_and_a_page_with_only_a_number() -> None:
    """
    Given  Chandra pages: one with a Page-Header holding the student's name, one with only a
           page number
    When   process_pages is called
    Then   the name is part of the text and the number-only page does not empty the result
    """
    header = '<div data-label="Page-Header"><p>Name: Max Muster, 7b</p></div>'
    footer = '<div data-label="Page-Footer"><p>3</p></div>'

    named = OcrOutputPostprocessor().process_pages([header + footer])
    number_only = OcrOutputPostprocessor().process_pages([footer])

    assert named.text == "Name: Max Muster, 7b"
    assert number_only.text == "3"


# ── Regression: round trips the review found broken ───────────────────────


def test_markdown_parser_keeps_ordered_list_numbers() -> None:
    """
    Given  Markdown lists starting at 3 and 5, and a list resumed after a paragraph
    When   parsed and rendered again
    Then   the numbers are preserved (not renumbered from 1)
    """
    for markdown in (
        "3. Antwort **drei**\n4. Antwort vier",
        "5. Fünf\n6. Sechs",
        "1. a\n\nZwischentext\n\n2. b",
        "- x\n\n    3. n\n    4. m",
    ):
        doc = MarkdownParser().parse(markdown)

        assert MarkdownRenderer().render(doc) == markdown


def test_markdown_parser_keeps_numbers_after_a_continuation_paragraph() -> None:
    """
    Given  an ordered item with a second paragraph, followed by item 2
    When   parsed and rendered
    Then   item 2 keeps its number
    """
    doc = MarkdownParser().parse("1. a\n\n   more\n\n2. b")

    assert [m for _b, m in list_markers(doc.blocks) if m] == ["1. ", "2. "]


@pytest.mark.parametrize(
    "spans",
    [
        pytest.param(
            (Span("Ich glaube"), Span(",", Style.STRIKE), Span(" dass")), id="strike-punct"
        ),
        pytest.param((Span("a"), Span("(b)", Style.BOLD), Span(" c")), id="bold-punct-in-word"),
        pytest.param(
            (Span("abc", Style.STRIKE), Span("def", Style.STRIKE | Style.BOLD)), id="strike-pair"
        ),
        pytest.param((Span("a", Style.BOLD), Span("b", Style.ITALIC)), id="adjacent-emphasis"),
        pytest.param((Span("x "), Span("*", Style.BOLD), Span(" y")), id="literal-star-styled"),
        pytest.param((Span("a_"), Span("b", Style.BOLD), Span("c_d")), id="underscore-neighbours"),
        pytest.param((Span("„"), Span("Haus", Style.BOLD), Span("“")), id="german-quotes"),
    ],
)
def test_markdown_round_trip_keeps_styles_next_to_punctuation(spans: tuple[Span, ...]) -> None:
    """
    Given  styled spans whose Markdown delimiters sit next to punctuation or each other
    When   rendered to Markdown and parsed back
    Then   the spans (text and style) come back unchanged
    """
    doc = FormattedDocument((_paragraph(*spans),))

    reparsed = MarkdownParser().parse(MarkdownRenderer().render(doc))

    assert reparsed == doc


def test_markdown_round_trip_of_random_styled_text() -> None:
    """
    Given  many seeded random span sequences over a hostile alphabet (markers, brackets,
           quotes, entities, whitespace) with random styles
    When   each is rendered to Markdown and parsed back
    Then   text and styles survive every time
    """
    import random  # noqa: PLC0415

    alphabet = [*"ab Z  *_~<>[]()!#-.1\\`&|,", "„", "€", "**", "__", "~~", "1. ", "](", "&amp;"]
    styles = [Style.NONE] * 4 + [
        Style.BOLD,
        Style.ITALIC,
        Style.STRIKE,
        Style.UNDERLINE,
        Style.SUPERSCRIPT,
        Style.BOLD | Style.ITALIC,
        Style.STRIKE | Style.BOLD,
    ]

    def normalised(spans: tuple[Span, ...]) -> tuple[Span, ...]:
        # Whitespace carries no style and the parser drops blanks around line breaks.
        out: list[Span] = []
        for span in merge_spans(list(spans)):
            text = re.sub(r" *\n *", "\n", span.text)
            core = text.strip()
            if not core:
                out.append(Span(text))
                continue
            lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()) :]
            out.extend(s for s in (Span(lead), Span(core, span.style), Span(trail)) if s.text)
        merged = merge_spans([Span(re.sub(r" *\n *", "\n", s.text), s.style) for s in out])
        return strip_spans(merged)

    failures: list[str] = []
    for seed in range(2000):
        rng = random.Random(seed)
        spans = tuple(
            Span(
                "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 4))), rng.choice(styles)
            )
            for _ in range(rng.randint(1, 5))
        )
        joined = "".join(span.text for span in spans)
        if "\n\n" in joined or "\n \n" in joined or not joined.strip():
            continue  # a blank line is a paragraph break by definition
        expected = normalised(spans)
        if not expected:
            continue
        reparsed = MarkdownParser().parse(
            MarkdownRenderer().render(FormattedDocument((_paragraph(*spans),)))
        )
        got = normalised(reparsed.blocks[0].spans) if len(reparsed.blocks) == 1 else None
        if got != expected:
            failures.append(f"seed {seed}: {spans!r} -> {got!r}")

    assert not failures[:3], failures[:3]


def test_markdown_parser_turns_embedded_html_table_into_a_table_block() -> None:
    """
    Given  Markdown from a vision model that embeds an HTML table
    When   parsed, rendered and parsed again
    Then   the table is a table block (not escaped tag text), with its styles
    """
    markdown = "# Aufgabe 1\n\n<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td><b>2</b></td></tr></table>"

    doc = MarkdownParser().parse(markdown)
    rendered = MarkdownRenderer().render(doc)

    assert [block.kind for block in doc.blocks] == ["heading", "table"]
    assert doc.blocks[1].rows[1][1] == (Span("2", Style.BOLD),)
    assert "\\<" not in rendered
    assert MarkdownParser().parse(rendered) == doc


def test_chandra_parser_separates_nested_blocks_in_cells_and_items() -> None:
    """
    Given  table cells and list items that hold several <p> elements
    When   parsed
    Then   the paragraphs are separated by a line break instead of running together
    """
    html = (
        '<div data-label="Table"><table><tr><td><p>Name</p><p>Max</p></td></tr></table></div>'
        '<div data-label="List-Group"><ul><li><p>Erstens</p><p>Zweitens</p></li></ul></div>'
    )

    doc = ChandraHtmlParser().parse(html)

    assert doc.blocks[0].rows[0][0] == (Span("Name\nMax"),)
    assert doc.blocks[1].text == "Erstens\nZweitens"


def test_chandra_parser_keeps_ordered_list_start() -> None:
    """
    Given  an <ol start="3">
    When   parsed and rendered to Markdown
    Then   the items are numbered 3 and 4
    """
    doc = ChandraHtmlParser().parse(
        '<div data-label="List-Group"><ol start="3"><li>a</li><li>b</li></ol></div>'
    )

    assert MarkdownRenderer().render(doc) == "3. a\n4. b"


def test_stored_markdown_leaves_email_addresses_matchable_by_the_anonymizer() -> None:
    """
    Given  OCR text with an e-mail address containing underscores, stored as Markdown
    When   the anonymizer's EMAIL pattern runs over the stored text
    Then   it matches the whole address (no backslash splits the local part, so no part of
           the name reaches the correction LLM)
    """
    from teachers_teammate.infrastructure.anonymizer import DEFAULT_PATTERNS  # noqa: PLC0415

    email_pattern = dict(DEFAULT_PATTERNS)["EMAIL"]
    result = OcrOutputPostprocessor().process_pages(
        ['<div data-label="Text"><p>Schreib an <b>Max</b>: max_mustermann@web.de.</p></div>']
    )

    match = re.search(email_pattern, result.text)

    assert result.text_format == "markdown"
    assert match is not None
    assert match.group(0) == "max_mustermann@web.de"
    assert "\\" not in result.text


# ── Review fixes: page-number filter, fenced answers, list continuations, legacy text ──


@pytest.mark.parametrize(
    "number",
    ["3", "iv", "XIV", "xxxix", "Seite 12", "p. iii", "- 7 -", "3/10", "Page 3 of 10"],
)
def test_drop_page_furniture_drops_page_numbers_in_all_notations(number: str) -> None:
    """
    Given  a footer holding a page number (arabic or roman, any usual wording) and a body
    When   DropPageFurniture is applied
    Then   the footer is removed
    """
    doc = FormattedDocument(
        (_paragraph(Span("body")), Block("page_footer", (Span(number),))),
    )

    kept = DropPageFurniture().apply(doc).blocks

    assert [block.text for block in kept] == ["body"]


@pytest.mark.parametrize("word", ["Lili", "Mimi", "Didi", "Mix", "Civil", "Li", "Xi", "Mi", "Dill"])
def test_drop_page_furniture_keeps_words_made_of_roman_letters(word: str) -> None:
    """
    Given  a page header holding a name or word that happens to use only roman-numeral letters
    When   DropPageFurniture is applied
    Then   the header is kept: it is content, not a page number
    """
    doc = FormattedDocument(
        (Block("page_header", (Span(word),)), _paragraph(Span("body"))),
    )

    kept = DropPageFurniture().apply(doc).blocks

    assert [block.text for block in kept] == [word, "body"]


@pytest.mark.parametrize("info", ["markdown", "md", "Markdown", ""])
def test_postprocessor_looks_through_a_fence_around_the_whole_markdown_answer(info: str) -> None:
    """
    Given  a model answer that wraps its Markdown in one code fence
    When   process_pages is called
    Then   the formatting is parsed (not stored as one escaped literal paragraph)
    """
    page = f"```{info}\n# Titel\n\nDas ist **fett** und ~~weg~~.\n```"

    result = OcrOutputPostprocessor().process_pages([page])

    assert result.text_format == "markdown"
    assert result.text == "# Titel\n\nDas ist **fett** und ~~weg~~."


def test_postprocessor_looks_through_a_fence_around_layout_html() -> None:
    """
    Given  a Chandra answer wrapped in a ```html fence
    When   process_pages is called
    Then   the layout HTML is parsed into Markdown
    """
    page = '```html\n<div data-bbox="0 0 1 1" data-label="Text"><p>Hallo <b>Welt</b></p></div>\n```'

    result = OcrOutputPostprocessor().process_pages([page])

    assert (result.text, result.text_format) == ("Hallo **Welt**", "markdown")


def test_postprocessor_does_not_unwrap_a_fence_in_another_language() -> None:
    """
    Given  a page that is one fenced block in another language, with a "# comment" line
    When   the page is parsed
    Then   the fence is not looked through: the code stays one literal paragraph, and its
           comment does not become a heading
    """
    page = "```python\n# Kommentar\nx = 1\n```"

    doc, _name = OcrOutputPostprocessor().parse_page(page)

    assert [(block.kind, block.text) for block in doc.blocks] == [
        ("paragraph", "# Kommentar\nx = 1")
    ]


def test_postprocessor_keeps_a_fenced_page_without_markup_verbatim() -> None:
    """
    Given  a bare fence around ordinary text
    When   process_pages is called
    Then   the page stays plain and verbatim
    """
    page = "```\nHallo Welt\n```"

    result = OcrOutputPostprocessor().process_pages([page])

    assert (result.text, result.text_format) == (page, "plain")


def test_markdown_parser_keeps_a_second_paragraph_inside_its_list_item() -> None:
    """
    Given  a numbered list whose first item has a second paragraph
    When   parsed and rendered again
    Then   the paragraph stays in the item (as a line break) and the list is not split
    """
    doc = MarkdownParser().parse("1. Antwort\n\n   Begründung dazu\n2. Zweite")

    assert [(block.kind, block.text) for block in doc.blocks] == [
        ("list_item", "Antwort\nBegründung dazu"),
        ("list_item", "Zweite"),
    ]
    rendered = MarkdownRenderer().render(doc)
    assert rendered == "1. Antwort\n   Begründung dazu\n2. Zweite"
    assert MarkdownRenderer().render(MarkdownParser().parse(rendered)) == rendered


def test_markdown_parser_does_not_reorder_a_paragraph_after_a_nested_list() -> None:
    """
    Given  a list item with a nested list followed by a further paragraph of the item
    When   parsed
    Then   the paragraph is not merged up into the item, so document order is kept
    """
    doc = MarkdownParser().parse("- Eins\n\n  - innen\n\n  danach")

    assert [block.text for block in doc.blocks] == ["Eins", "innen", "danach"]


def test_looks_like_markup_detects_markdown_and_layout_html_only() -> None:
    """
    Given  plain prose, Markdown and Chandra HTML
    When   looks_like_markup is called
    Then   only the last two are recognised
    """
    assert looks_like_markup("**fett**")
    assert looks_like_markup('<div data-bbox="0 0 1 1" data-label="Text"><p>x</p></div>')
    assert not looks_like_markup("Ein ganz normaler Satz - mit Strich.\n* und Stern")


def test_upgrade_legacy_text_converts_stored_markup_to_markdown() -> None:
    """
    Given  raw model markup stored before notations were tracked
    When   upgrade_legacy_text is called
    Then   it is canonical Markdown with the "markdown" format
    """
    result = upgrade_legacy_text(
        '<div data-bbox="0 0 1 1" data-label="Text"><p>Hallo <del>Welt</del></p></div>'
    )

    assert (result.text, result.text_format) == ("Hallo ~~Welt~~", "markdown")


def test_text_formatter_parses_the_same_stored_text_once() -> None:
    """
    Given  a stored Markdown text asked for repeatedly (to show it, to diff it, to count words)
    When   TextFormatter.document() and plain_text() are called several times
    Then   the results are equal and the very same objects are reused (parsed once)
    """
    text = "Ein **fetter** Satz mit ~~Fehler~~ - einmalig " + "x" * 17

    first_doc = TextFormatter.document(text, "markdown")
    first_plain = TextFormatter.plain_text(text, "markdown")

    assert TextFormatter.document(text, "markdown") is first_doc
    assert TextFormatter.plain_text(text, "markdown") is first_plain
    assert first_plain == "Ein fetter Satz mit Fehler - einmalig " + "x" * 17
    assert TextFormatter.plain_text(text, "plain") is text


def test_join_labelled_pages_matches_postprocessor_output() -> None:
    """
    Given  per-page texts
    When   join_labelled_pages() and OcrOutputPostprocessor.process_pages() label them
    Then   both produce the same "Page N:" shape, and a single page stays unlabelled
    """
    from teachers_teammate.domain.formatted_text import join_labelled_pages  # noqa: PLC0415

    pages = ["first", "second"]

    assert join_labelled_pages(pages) == "Page 1:\nfirst\nPage 2:\nsecond"
    assert join_labelled_pages(pages) == OcrOutputPostprocessor().process_pages(pages).text
    assert join_labelled_pages(["only"]) == "only"
    assert join_labelled_pages(["a", " "], markdown=True) == "Page 1:\n\na"
