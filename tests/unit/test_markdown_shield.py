"""Unit tests for the Markdown shield (``domain/proofreading/markdown_shield.py``)."""

from __future__ import annotations

from teachers_teammate.domain.formatted_text import Block, FormattedDocument, Span, Style
from teachers_teammate.domain.proofreading.markdown_shield import restore, shield
from teachers_teammate.infrastructure.ocr_formats import MarkdownRenderer


def _render(document: FormattedDocument) -> str:
    """Return *document* as the canonical Markdown the app stores."""
    return MarkdownRenderer().render(document)


def _paragraph(*spans: Span) -> Block:
    """Return a paragraph block of *spans*."""
    return Block("paragraph", spans)


def test_the_request_text_carries_no_marks() -> None:
    """
    Given  a document with a heading, a list item and styled words
    When   its request text is taken
    Then   the text is plain paragraphs, without marks or list markers
    """
    doc = FormattedDocument(
        (
            Block("heading", (Span("Aufgabe 1"),), level=2),
            Block("list_item", (Span("Ein "), Span("Punkt", Style.BOLD)), level=1, ordered=True),
        )
    )

    assert shield(doc).text == "Aufgabe 1\n\nEin Punkt"


def test_crossed_out_words_are_removed_from_the_request_and_put_back() -> None:
    """
    Given  a paragraph with a crossed-out word
    When   the request text is taken and the same text comes back
    Then   the request leaves the word out and the restored document has it again
    """
    doc = FormattedDocument(
        (_paragraph(Span("Er war "), Span("sehr", Style.STRIKE), Span(" laut.")),)
    )

    shield_map = shield(doc)

    assert shield_map.text == "Er war  laut."
    assert [anchor.text for anchor in shield_map.anchors] == ["sehr"]
    assert _render(restore(doc, shield_map, shield_map.text).document) == "Er war ~~sehr~~ laut."


def test_an_unchanged_answer_restores_the_source_document() -> None:
    """
    Given  a document of a heading, a paragraph and a list item
    When   the request text comes back unchanged
    Then   the restored document is the source document
    """
    doc = FormattedDocument(
        (
            Block("heading", (Span("Aufgabe 1"),), level=2),
            _paragraph(Span("Ich habe ein "), Span("grosse", Style.BOLD), Span(" Hund gesehen.")),
            Block("list_item", (Span("Ein Punkt"),), level=1, ordered=True, number=3),
        )
    )

    shield_map = shield(doc)

    assert restore(doc, shield_map, shield_map.text).document == doc


def test_a_replacement_keeps_the_style_of_its_span() -> None:
    """
    Given  a bold word that the answer corrects
    When   the answer is restored
    Then   the corrected word is bold again
    """
    doc = FormattedDocument((_paragraph(Span("Ich habe ein "), Span("grosse", Style.BOLD)),))
    shield_map = shield(doc)

    restored = restore(doc, shield_map, "Ich habe ein großen")

    assert _render(restored.document) == "Ich habe ein **großen**"
    assert restored.unstyled == 0


def test_a_replacement_across_style_boundaries_loses_its_style_and_is_counted() -> None:
    """
    Given  a bold word that the answer deletes together with the space before it
    When   the answer is restored
    Then   the region has no style and is counted
    """
    doc = FormattedDocument(
        (_paragraph(Span("Ich habe ein "), Span("grosse", Style.BOLD), Span(" Hund.")),)
    )
    shield_map = shield(doc)

    restored = restore(doc, shield_map, "Ich habe ein Hund.")

    assert _render(restored.document) == "Ich habe ein Hund."
    assert restored.unstyled == 1


def test_an_insertion_takes_the_style_of_both_neighbours() -> None:
    """
    Given  a bold phrase that the answer extends with an inserted word
    When   the answer is restored
    Then   the inserted word is bold too
    """
    doc = FormattedDocument((_paragraph(Span("Der "), Span("grosse Hund", Style.BOLD)),))
    shield_map = shield(doc)

    restored = restore(doc, shield_map, "Der grosse braune Hund")

    assert _render(restored.document) == "Der **grosse braune Hund**"


def test_an_insertion_between_two_styles_gets_none() -> None:
    """
    Given  a plain word between a bold and a plain span that the answer replaces
    When   the answer is restored
    Then   the new words carry no style
    """
    doc = FormattedDocument(
        (_paragraph(Span("Der "), Span("grosse", Style.BOLD), Span(" Hund bellt.")),)
    )
    shield_map = shield(doc)

    restored = restore(doc, shield_map, "Der große Hund bellt.")

    assert _render(restored.document) == "Der **große** Hund bellt."


def test_an_insertion_keeps_the_words_apart() -> None:
    """
    Given  a paragraph and an answer that adds a word between two words
    When   the answer is restored
    Then   the new word stands between them with its spaces
    """
    doc = FormattedDocument((_paragraph(Span("Ich gehe heim.")),))
    shield_map = shield(doc)

    restored = restore(doc, shield_map, "Ich gehe schnell heim.")

    assert _render(restored.document) == "Ich gehe schnell heim."


def test_block_boundaries_come_from_the_source() -> None:
    """
    Given  two paragraphs that the answer joins into one
    When   the answer is restored
    Then   the document keeps two paragraphs
    """
    doc = FormattedDocument((_paragraph(Span("Ich ging heim.")), _paragraph(Span("Dann ass ich."))))
    shield_map = shield(doc)

    restored = restore(doc, shield_map, "Ich ging heim. Dann ass ich.")

    assert [block.kind for block in restored.document.blocks] == ["paragraph", "paragraph"]
    assert _render(restored.document) == "Ich ging heim.\n\nDann ass ich."


def test_a_table_keeps_its_cells() -> None:
    """
    Given  a table whose second cell the answer corrects
    When   the answer is restored
    Then   the table keeps its rows and cells with the corrected text
    """
    doc = FormattedDocument(
        (
            Block(
                "table",
                rows=(
                    ((Span("Wort"),), (Span("Zahl"),)),
                    ((Span("Hund"),), (Span("3"),)),
                ),
            ),
        )
    )
    shield_map = shield(doc)

    restored = restore(doc, shield_map, "Wort | Zahl\nHund | drei")

    assert _render(restored.document) == "| Wort | Zahl |\n| --- | --- |\n| Hund | drei |"


def test_a_figure_is_passed_through() -> None:
    """
    Given  a document with a figure and a paragraph
    When   the request text is taken and restored
    Then   the figure is not part of the request and comes back unchanged
    """
    doc = FormattedDocument((Block("figure", (Span("Skizze"),)), _paragraph(Span("Text."))))
    shield_map = shield(doc)

    restored = restore(doc, shield_map, "Text.")

    assert shield_map.text == "Text."
    assert [block.kind for block in restored.document.blocks] == ["figure", "paragraph"]


def test_a_list_item_keeps_its_marker_and_level() -> None:
    """
    Given  a numbered list item on the second level
    When   the answer corrects its text
    Then   the item keeps its number and its level
    """
    doc = FormattedDocument(
        (Block("list_item", (Span("Ein Punkt"),), level=1, ordered=True, number=3),)
    )
    shield_map = shield(doc)

    restored = restore(doc, shield_map, "Ein Punkt!")

    assert restored.document.blocks[0].level == 1
    assert restored.document.blocks[0].number == 3
    assert _render(restored.document) == "    3. Ein Punkt!"


def test_a_plain_text_document_round_trips() -> None:
    """
    Given  a document of plain paragraphs
    When   the request text is taken and restored unchanged
    Then   the document is the same
    """
    doc = FormattedDocument((_paragraph(Span("Zeile eins")), _paragraph(Span("Zeile zwei"))))

    shield_map = shield(doc)

    assert restore(doc, shield_map, shield_map.text).document == doc


def test_words_written_on_a_block_gap_are_kept_not_lost() -> None:
    """
    Given  two paragraphs and an answer that joins them with a word or a comma
    When   the document is restored
    Then   the words stay at the end of the first paragraph, the block break is the source's,
           and the region is counted
    """
    doc = FormattedDocument((_paragraph(Span("Erster Absatz")), _paragraph(Span("zweiter Absatz"))))
    shield_map = shield(doc)

    joined = restore(doc, shield_map, "Erster Absatz und zweiter Absatz")
    comma = restore(
        FormattedDocument((_paragraph(Span("Der Hund")), _paragraph(Span("bellt")))),
        shield(FormattedDocument((_paragraph(Span("Der Hund")), _paragraph(Span("bellt"))))),
        "Der Hund, bellt",
    )

    assert _render(joined.document) == "Erster Absatz und\n\nzweiter Absatz"
    assert joined.unstyled == 1
    assert _render(comma.document) == "Der Hund,\n\nbellt"


def test_a_crossed_out_word_inside_a_hunk_comes_before_its_replacement() -> None:
    """
    Given  a crossed-out word with the word that replaced it written next to it
    When   the model rewrites that next word
    Then   the crossed-out word keeps its place before the new word, set off by spaces,
           and nothing is reported as lost
    """
    doc = FormattedDocument(
        (_paragraph(Span("Ich "), Span("gehe", Style.STRIKE), Span(" laufe nach Hause.")),)
    )
    shield_map = shield(doc)

    restored = restore(doc, shield_map, "Ich renne nach Hause.")

    assert _render(restored.document) == "Ich ~~gehe~~ renne nach Hause."
    assert restored.unstyled == 0
