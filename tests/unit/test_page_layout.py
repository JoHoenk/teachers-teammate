"""What the scan marks: the places of a page's lines, the sentences, and the boxes of a mark."""

from __future__ import annotations

from difflib import SequenceMatcher

import pytest

from teachers_teammate.domain.page_layout import (
    ABBREVIATIONS,
    CONFIDENCE_BANDS,
    LAYOUT_VERSION,
    LineAt,
    PageReading,
    ReadLine,
    ScanBox,
    carried,
    layout_from,
    mark_of_line,
    marks,
    place,
    sentence_at,
    sentences,
    shade,
)
from teachers_teammate.domain.proofreading.word_diff import OffsetMap

_BOX = (((0.1, 0.1), (0.5, 0.1), (0.5, 0.2), (0.1, 0.2)),)


def _line(text: str, page: int = 0, confidence: float = 0.9, boxes: int = 1) -> ReadLine:
    """Return a line as the reader would return it, with *boxes* equal boxes."""
    corners = tuple(
        (
            (index / 10, 0.1),
            (index / 10 + 0.05, 0.1),
            (index / 10 + 0.05, 0.2),
            (index / 10, 0.2),
        )
        for index in range(boxes)
    )
    return ReadLine(text=text, confidence=confidence, boxes=corners, page=page)


def _opcodes(one: str, other: str):
    """Return the character alignment of two texts, as the pipeline makes it."""
    return tuple(SequenceMatcher(None, one, other, autojunk=False).get_opcodes())


def _at(line: ReadLine, text: str, offset: int) -> LineAt:
    """Return *line* at the place its own text has in *text* from *offset* on."""
    start = text.index(line.text, offset) if line.text else offset
    return LineAt(line, start, start + len(line.text))


def test_place_identical_reading_keeps_every_line() -> None:
    """Given a reading that is the reader's own text, When the lines are placed, Then each line
    keeps its own range and its box."""
    first, second = _line("Die Katze sitzt"), _line("auf der Matte", page=0)
    text = "Die Katze sitzt\nauf der Matte"
    lines = [_at(first, text, 0), _at(second, text, 0)]

    placed = place(lines, _opcodes(text, text), text)

    assert [(box.start, box.end) for box in placed] == [
        (0, len("Die Katze sitzt")),
        (len("Die Katze sitzt\n"), len(text)),
    ]
    assert placed[0].boxes == first.boxes
    assert placed[0].page == 0


def test_place_drops_a_line_the_reading_does_not_hold() -> None:
    """Given a reading whose second line was read as something else entirely, When the lines are
    placed, Then that line is left out and the first is kept."""
    kept = _line("Die Katze sitzt")
    lost = _line("qwrtz xkcd")
    text = "Die Katze sitzt\nqwrtz xkcd"
    reading = "Die Katze sitzt\nDie Maus läuft schnell davon"
    lines = [_at(kept, text, 0), _at(lost, text, 0)]

    placed = place(lines, _opcodes(text, reading), reading)

    assert [box.confidence for box in placed] == [kept.confidence]


def test_place_follows_the_page_labels() -> None:
    """Given a two-page reading with its page labels, When the second page's line is placed,
    Then its range lies after the label that page got."""
    line = _line("Die Katze sitzt", page=1)
    text = "Page 1:\nDer Hund bellt\nPage 2:\nDie Katze sitzt"
    placed = place([_at(line, text, 0)], _opcodes(text, text), text)

    assert placed[0].page == 1
    assert text[placed[0].start : placed[0].end] == "Die Katze sitzt"


def test_place_keeps_what_share_of_the_line_each_box_holds() -> None:
    """Given a line of three words and three boxes, When it is placed, Then the shares follow the
    words' lengths."""
    line = _line("Ich bin da", boxes=3)
    text = "Ich bin da"

    placed = place([_at(line, text, 0)], _opcodes(text, text), text)

    assert placed[0].shares == pytest.approx((3 / 8, 3 / 8, 2 / 8))
    assert sum(placed[0].shares) == pytest.approx(1.0)


def test_carried_moves_the_ranges_and_keeps_the_page() -> None:
    """Given one placed line and a reading fix in front of it, When it is carried into the
    transcription, Then its range has moved by the fix and its box stayed."""
    reading = "Der Hund bellt. Die Katze sitzt."
    transcription = "Der Hund bellt laut. Die Katze sitzt."
    box = ScanBox(page=2, start=16, end=32, confidence=0.8, boxes=_BOX)

    moved = carried([box], OffsetMap(reading, transcription))

    assert len(moved) == 1
    assert transcription[moved[0].start : moved[0].end] == "Die Katze sitzt."
    assert (moved[0].page, moved[0].confidence, moved[0].boxes) == (2, 0.8, _BOX)


def test_sentences_joins_a_dotted_date() -> None:
    """Given a sentence holding a dotted date, When its sentences are taken, Then the date stays
    inside one sentence."""
    text = "Am 15. 6. 26 schrieb er. Dann ging er."

    assert sentences(text) == ((0, 24), (25, len(text)))
    assert sentence_at(text, 5) is not None
    assert sentence_at(text, 5).start == 0


def test_sentences_joins_an_abbreviation() -> None:
    """Given a sentence ending in an abbreviation, When its sentences are taken, Then the
    abbreviation does not end it."""
    text = "Er kam z. B. später. Sie auch."

    spans = sentences(text)

    assert [text[start:end] for start, end in spans] == ["Er kam z. B. später.", "Sie auch."]
    assert ABBREVIATIONS


def test_sentence_at_says_whether_it_closes() -> None:
    """Given a heading without a full stop and a sentence with one, When each is asked for,
    Then only the sentence is closed."""
    text = "Die Katze\n\nSie sitzt."
    assert sentence_at(text, 2).closed is False
    assert sentence_at(text, 14).closed is True


def test_sentence_at_without_a_sentence() -> None:
    """Given a blank text, When a sentence is asked for, Then there is none."""
    assert sentence_at("   ", 0) is None


def test_marks_draws_the_lines_of_the_sentence() -> None:
    """Given two placed lines of one sentence, When its stretch is marked, Then both lines' boxes
    are drawn and the shade is the mean of their confidences."""
    boxes = (
        ScanBox(page=0, start=0, end=10, confidence=1.0, boxes=_BOX),
        ScanBox(page=0, start=11, end=20, confidence=0.8, boxes=_BOX),
    )

    mark = marks(0, 20, boxes)

    assert mark is not None
    assert mark.polygons == ((0, _BOX[0]), (0, _BOX[0]))
    assert mark.confidence == pytest.approx(0.9)
    assert mark.shade == shade(0.9)


def test_marks_needs_enough_of_the_sentence_on_a_line() -> None:
    """Given a sentence of which one of four lines was placed, When it is marked, Then nothing is
    drawn: the scan shows the page instead."""
    boxes = (ScanBox(page=0, start=0, end=10, confidence=0.9, boxes=_BOX),)

    assert marks(0, 40, boxes) is None


def test_marks_clips_the_line_at_the_sentence_ends() -> None:
    """Given a line whose first word belongs to the sentence before it, When the sentence is
    marked, Then only the boxes of the words inside it are drawn."""
    boxes = (
        ScanBox(
            page=0,
            start=0,
            end=12,
            confidence=0.9,
            boxes=_BOX * 3,
            shares=(0.25, 0.25, 0.5),
        ),
    )

    mark = marks(3, 12, boxes)

    assert mark is not None
    assert len(mark.polygons) == 2


def test_marks_without_a_line() -> None:
    """Given no placed line at all, When a stretch is marked, Then there is no mark."""
    assert marks(0, 10, ()) is None
    assert marks(5, 5, (ScanBox(0, 0, 10, 0.9, _BOX),)) is None


def test_mark_of_line_takes_the_line_under_the_offset() -> None:
    """Given two placed lines, When the second is asked for, Then its own boxes are drawn."""
    boxes = (
        ScanBox(page=0, start=0, end=10, confidence=0.5, boxes=_BOX),
        ScanBox(page=1, start=11, end=20, confidence=0.7, boxes=_BOX),
    )

    mark = mark_of_line(15, boxes)

    assert mark is not None
    assert {page for page, _box in mark.polygons} == {1}
    assert mark.confidence == pytest.approx(0.7)


def test_shade_bands() -> None:
    """Given a confidence in each band, When the shade is asked for, Then the surest is 0 and a
    confidence below the last band is the darkest."""
    assert [shade(1.0), shade(0.95), shade(0.9), shade(0.6)] == [
        0,
        0,
        1,
        len(CONFIDENCE_BANDS),
    ]


def test_layout_round_trips_through_the_cache() -> None:
    """Given a placed line, When it is stored and read back, Then it draws the same box."""
    box = ScanBox(
        page=1,
        start=4,
        end=12,
        confidence=0.91234,
        boxes=_BOX,
        shares=(0.5, 0.5),
    )

    read = layout_from([box.to_dict()])

    assert len(read) == 1
    assert read[0].page == 1
    assert (read[0].start, read[0].end) == (4, 12)
    assert read[0].confidence == pytest.approx(0.9123, abs=1e-3)
    assert read[0].boxes[0][0] == pytest.approx((0.1, 0.1))
    assert read[0].shares == pytest.approx((0.5, 0.5))


def test_layout_skips_a_row_it_cannot_read() -> None:
    """Given one stored place and one row of another shape, When they are read, Then the broken
    row is dropped and the good one kept."""
    good = ScanBox(page=0, start=0, end=4, confidence=0.9, boxes=_BOX).to_dict()

    assert len(layout_from([good, {"page": "x"}])) == 1
    assert layout_from([]) == ()


def test_layout_version_is_a_number() -> None:
    """Given the version the places are stored with, When it is read, Then it names one layout."""
    assert isinstance(LAYOUT_VERSION, int)
    assert LAYOUT_VERSION >= 1


def test_a_page_reading_still_carries_its_lines() -> None:
    """Given one page read, When the reading is built, Then its text and its lines both stand."""
    line = _line("Die Katze sitzt")
    reading = PageReading(text="Die Katze sitzt", lines=(line,))

    assert reading.lines[0].text == "Die Katze sitzt"
    assert PageReading("nur Text").lines == ()


def test_place_ends_a_line_where_its_own_stretch_ends() -> None:
    """Given a line whose end meets a stretch the two readings read differently, When it is
    placed, Then its range ends with its own words and does not reach into that stretch."""
    line = _line("Hallo")
    text = "Hallo Welt"
    reading = "Hallo~~~~Welt"
    opcodes = (("equal", 0, 5, 0, 5), ("replace", 5, 6, 5, 9), ("equal", 6, 10, 9, 13))

    placed = place([_at(line, text, 0)], opcodes, reading)

    assert (placed[0].start, placed[0].end) == (0, 5)


def test_place_shares_follow_what_each_box_reads() -> None:
    """Given a line whose first box holds two words, When it is placed, Then the shares follow
    the boxes' own texts rather than the words."""
    line = ReadLine(
        text="Ich bin da",
        confidence=0.9,
        boxes=_BOX * 2,
        box_texts=("Ich bin", "da"),
    )

    placed = place([_at(line, "Ich bin da", 0)], _opcodes("Ich bin da", "Ich bin da"), "Ich bin da")

    assert placed[0].shares == pytest.approx((7 / 9, 2 / 9))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Das war 2019. Dann kam Corona.", ["Das war 2019.", "Dann kam Corona."]),
        ("Das war 2019. und danach kam Corona.", ["Das war 2019. und danach kam Corona."]),
        ("Wir kamen am 15. Juni an. Es regnete.", ["Wir kamen am 15. Juni an.", "Es regnete."]),
        ("Äpfel, Birnen usw. Dann gingen wir.", ["Äpfel, Birnen usw.", "Dann gingen wir."]),
        ("Äpfel, Birnen usw. und Kirschen.", ["Äpfel, Birnen usw. und Kirschen."]),
        ("Äpfel, Birnen etc. Dann gingen wir.", ["Äpfel, Birnen etc.", "Dann gingen wir."]),
        ("Teil 1.\n\nDie Katze sitzt.", ["Teil 1.", "Die Katze sitzt."]),
    ],
)
def test_sentences_end_at_a_year_or_an_ending_abbreviation(text: str, expected: list[str]) -> None:
    """Given a full stop after a year, ``usw.``/``etc.`` or before a blank line, When the sentences
    are taken, Then it ends the sentence unless a lower-case word goes on, while a day does not."""
    assert [text[start:end] for start, end in sentences(text)] == expected


def test_layout_skips_a_row_that_is_not_a_place() -> None:
    """Given stored rows that are not mappings at all, When they are read, Then they are dropped
    and the good one kept."""
    good = ScanBox(page=0, start=0, end=4, confidence=0.9, boxes=_BOX).to_dict()

    assert len(layout_from([good, "page 1", None, [1, 2]])) == 1


def test_place_ignores_case() -> None:
    """Given a line the reader read in capitals and a reading in mixed case, When it is placed,
    Then it is kept: the two readings agree on the words."""
    line = _line("DIE KATZE SITZT")
    text, reading = "DIE KATZE SITZT", "Die Katze sitzt"

    placed = place([_at(line, text, 0)], _opcodes(text.lower(), reading.lower()), reading)

    assert [(box.start, box.end) for box in placed] == [(0, len(reading))]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Einleitung\nDas ist gut.", ["Einleitung", "Das ist gut."]),
        (
            "- Punkt eins\n- Punkt zwei\nDas ist gut.",
            ["- Punkt eins", "- Punkt zwei", "Das ist gut."],
        ),
        (
            "Page 1:\nDas ist gut.\nPage 2:\nIch bin da.",
            ["Page 1:", "Das ist gut.", "Page 2:", "Ich bin da."],
        ),
        ("Ich ging\nPage 2:\nnach Hause.", ["Ich ging", "Page 2:", "nach Hause."]),
        (
            "Berlin, 15. Mai 2024\nLiebe Klasse,\nheute schreibe ich.",
            ["Berlin, 15. Mai 2024", "Liebe Klasse,\nheute schreibe ich."],
        ),
    ],
)
def test_sentences_cut_off_a_line_that_stands_on_its_own(text: str, expected: list[str]) -> None:
    """Given a heading, list items, page labels or a date on lines of their own, When the
    sentences are taken, Then each of those lines is a stretch of its own."""
    assert [text[start:end] for start, end in sentences(text)] == expected


@pytest.mark.parametrize(
    "text",
    [
        "Gestern bin ich mit meinem Hund\nnach Hause gegangen.",
        "Gestern bin ich mit meinem Hund durch den\nPark gegangen.",
        "Am 15.\nMai 2024 war es so.",
    ],
)
def test_sentences_keep_a_sentence_written_across_lines(text: str) -> None:
    """Given a sentence the handwriting broke across two lines, When the sentences are taken,
    Then it stays one sentence."""
    assert sentences(text) == ((0, len(text)),)


def test_a_heading_is_marked_by_its_own_line() -> None:
    """Given a heading on the line above a sentence, When the cursor stands in the heading, Then
    the heading is not closed, and its line alone is marked."""
    text = "Einleitung\nDas ist gut."
    boxes = (
        ScanBox(page=0, start=0, end=10, confidence=0.9, boxes=_BOX),
        ScanBox(page=0, start=11, end=len(text), confidence=0.5, boxes=_BOX),
    )

    sentence = sentence_at(text, 3)
    mark = mark_of_line(3, boxes)

    assert sentence is not None
    assert (sentence.start, sentence.end, sentence.closed) == (0, 10, False)
    assert mark is not None
    assert mark.confidence == pytest.approx(0.9)


def test_the_first_sentence_of_a_later_page_is_marked() -> None:
    """Given a two-page reading whose second page begins with a short sentence, When that
    sentence is marked, Then the page label above it does not count against it."""
    text = "Page 1:\nDas ist der Anfang hier.\nPage 2:\nIch bin da."
    start = text.index("Ich")
    boxes = (
        ScanBox(page=0, start=8, end=32, confidence=0.9, boxes=_BOX),
        ScanBox(page=1, start=start, end=len(text), confidence=0.9, boxes=_BOX),
    )

    sentence = sentence_at(text, start)

    assert sentence is not None
    assert marks(sentence.start, sentence.end, boxes) is not None


def test_mark_of_line_takes_the_line_a_cursor_ends() -> None:
    """Given two placed lines, When the cursor stands at the end of the first, before its line
    break, Then that line is marked."""
    boxes = (
        ScanBox(page=0, start=0, end=10, confidence=0.5, boxes=_BOX),
        ScanBox(page=0, start=11, end=20, confidence=0.7, boxes=_BOX),
    )

    first, last = mark_of_line(10, boxes), mark_of_line(20, boxes)

    assert first is not None
    assert first.confidence == pytest.approx(0.5)
    assert last is not None
    assert last.confidence == pytest.approx(0.7)


def test_a_sentence_begun_late_on_a_line_goes_on_below() -> None:
    """Given a sentence that begins after another on the same line and goes on below, When the
    sentences are taken, Then its few words on the first line are not taken for a heading."""
    text = "Der Hund bellt. Die\nKatze mauzt."

    assert [text[start:end] for start, end in sentences(text)] == [
        "Der Hund bellt.",
        "Die\nKatze mauzt.",
    ]
