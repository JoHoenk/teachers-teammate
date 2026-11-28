"""Task text: what the batch finds in a queue, and what a marked passage does to a text."""

from __future__ import annotations

from itertools import pairwise

from teachers_teammate.domain.task_text import (
    Segment,
    TaskDocument,
    detect_task_text,
    found_passages,
    lines_of,
    locate,
    new_passages,
    normalise,
    passage_parts,
)
from teachers_teammate.domain.task_text import _ratio

SCHOOL = "Gymnasium Musterstadt\nEnglische Klassenarbeit"
TASK_ONE = "Aufgabe 1 Read the text and answer the questions."
TASK_TWO = "Aufgabe 2 Erkläre in eigenen Worten, was der Autor meint."

SENTENCES = (
    "Ich denke, der Text handelt von einem Jungen, der umzieht.",
    "Meiner Meinung nach geht es um eine Klasse auf Reisen.",
    "Der Autor erzählt von einer Familie in Berlin.",
    "In dem Text geht es um einen Streit zwischen Freunden.",
    "Ich glaube, der Text spielt in einer kleinen Stadt.",
    "Die Hauptfigur wirkt auf mich sehr unsicher.",
    "Am Ende findet sie endlich ihren eigenen Weg.",
    "Die Sprache des Autors ist einfach und klar.",
    "Mich hat die Stelle mit dem Brief besonders berührt.",
    "Ich würde den Text meiner Schwester empfehlen.",
    "Der Anfang ist spannend, das Ende eher langweilig.",
    "Vielleicht will der Autor zeigen, wie schwer Abschied ist.",
)


def _answer(key: int, slot: int = 0) -> str:
    """Return an answer that no other document of a queue of up to 1728 writes."""
    return (
        f"{SENTENCES[key % len(SENTENCES)]} "
        f"{SENTENCES[(key // len(SENTENCES) + slot) % len(SENTENCES)]} "
        f"{SENTENCES[key // len(SENTENCES) ** 2 % len(SENTENCES)]}"
    )


def _document(name: str, lines: list[str]) -> TaskDocument:
    """Return one document of the queue, its lines joined as a transcription."""
    return TaskDocument(name=name, plain="\n".join(lines))


def _worksheet(count: int, *, offset: int = 0, printer: str = SCHOOL) -> list[TaskDocument]:
    """Return *count* documents of one invented worksheet, each with its own answers."""
    return [
        _document(
            f"doc{index}",
            [
                printer,
                "Klasse 8a",
                "Name: Schüler",
                TASK_ONE,
                _answer(offset + index),
                TASK_TWO,
                _answer(offset + index, 1),
            ],
        )
        for index in range(count)
    ]


def test_one_worksheet_finds_its_header_and_its_tasks() -> None:
    """Given four documents of one worksheet, When the batch runs, Then they are one worksheet,
    its header and both tasks are found, and a student's answer is not among them.
    """
    documents = _worksheet(4)

    detection = detect_task_text(documents)

    assert len(detection.worksheets) == 1
    sheet = detection.worksheets[0]
    assert sheet.documents == ("doc0", "doc1", "doc2", "doc3")
    found = detection.passages["doc1"]
    assert any(TASK_ONE in passage for passage in found)
    assert any(TASK_TWO in passage for passage in found)
    assert not any(_answer(0) in passage for passage in found)


def test_mixed_queue_finds_one_school_in_two_worksheets() -> None:
    """Given two classes with one school header and their own task, When the batch runs, Then
    each class is a worksheet of its own and each task is found in its own group.
    """
    task_eight_b = "Aufgabe 1 Beschreibe das Bild auf Seite 3."
    documents = _worksheet(4)
    documents += [
        _document(
            f"b{index}",
            [
                SCHOOL,
                "Klasse 8b",
                "Name: Schüler",
                task_eight_b,
                _answer(4 + index),
            ],
        )
        for index in range(4)
    ]
    documents += [
        _document(f"essay{index}", [_answer(8 + index), _answer(8 + index, 3)])
        for index in range(3)
    ]

    detection = detect_task_text(documents)

    sheets = {sheet.documents for sheet in detection.worksheets}
    assert ("doc0", "doc1", "doc2", "doc3") in sheets
    assert ("b0", "b1", "b2", "b3") in sheets
    assert any(TASK_ONE in passage for passage in detection.passages["doc0"])
    assert any(task_eight_b in passage for passage in detection.passages["b0"])
    assert not any(TASK_ONE in passage for passage in detection.passages["b0"])


def test_documents_sharing_no_header_form_no_worksheet_of_their_own() -> None:
    """Given three essays with nothing in common, When the batch runs, Then nothing is found:
    a queue of one worksheet whose print does not start at the top needs three documents.
    """
    documents = [
        _document(f"essay{index}", [_answer(index), _answer(index, 3)]) for index in range(3)
    ]

    detection = detect_task_text(documents)

    assert detection.passages == {}
    assert detection.worksheets == ()


def test_letter_openings_stay_the_students() -> None:
    """Given a batch of letters whose salutations and openings differ, When the batch runs, Then
    nothing is left alone: a set phrase each student writes differently is their own text.
    """
    salutations = (
        "Liebe Frau Meier,",
        "Sehr geehrte Frau Meier,",
        "Hallo Frau Meier,",
        "Guten Tag,",
    )
    openings = (
        "ich schreibe dir, weil ich mich über den Ausflug beschweren möchte.",
        "ich schreibe Ihnen, da mir der Ausflug überhaupt nicht gefallen hat.",
        "ich möchte mich beschweren, denn der Ausflug war schlecht geplant.",
        "ich wende mich an dich, weil der Ausflug ein Reinfall war.",
    )
    closings = ("Viele Grüße", "Mit freundlichen Grüßen", "Bis bald", "Liebe Grüße")
    documents = [
        _document(
            f"letter{index}",
            [salutations[index], openings[index], _answer(index), closings[index]],
        )
        for index in range(4)
    ]

    detection = detect_task_text(documents)

    assert detection.passages == {}


def test_a_students_copy_of_a_task_between_student_lines_is_proofread() -> None:
    """Given one document carrying the task again with a spelling mistake between two answers,
    When the batch runs, Then the copy is not taken: only the block the printer wrote is.
    """
    documents = _worksheet(4)
    documents[0] = _document(
        "doc0",
        [
            SCHOOL,
            "Klasse 8a",
            "Name: Schüler",
            TASK_ONE,
            _answer(0),
            "Aufgabe 1 Read the txt and answr the question.",
            _answer(1),
            TASK_TWO,
            _answer(2),
        ],
    )

    detection = detect_task_text(documents)

    found = detection.passages["doc0"]
    assert not any("Read the txt" in passage for passage in found)
    assert any(TASK_TWO in passage for passage in found)


def test_a_misread_printed_line_inside_a_block_is_taken() -> None:
    """Given one scan that misreads a word of the task, When the batch runs, Then the misread
    line is left alone with the block it stands in.
    """
    documents = _worksheet(4)
    documents[0] = _document(
        "doc0",
        [
            SCHOOL,
            "Klasse 8a",
            "Name: Schüler",
            "Aufgabe 1 Read the tex and answr the questions.",
            _answer(0),
            TASK_TWO,
            _answer(1),
        ],
    )

    detection = detect_task_text(documents)

    found = detection.passages["doc0"]
    assert any("Read the tex and answr" in passage for passage in found)


def test_a_label_keeps_its_fill_in_the_students() -> None:
    """Given a form label with the answer behind it, When the batch runs, Then the label is left
    alone and the answer the student wrote is proofread.
    """
    documents = [
        _document(
            f"doc{index}",
            [
                SCHOOL,
                f"Name: {name}",
                "Klasse: 8a",
                TASK_ONE,
                _answer(index),
            ],
        )
        for index, name in enumerate(("Anna Müller", "Ben Schmidt", "Clara Weber", "Deniz Yilmaz"))
    ]

    detection = detect_task_text(documents)

    found = detection.passages["doc0"]
    assert any(passage.startswith("Gymnasium Musterstadt") for passage in found)
    assert not any("Anna Müller" in passage for passage in found)
    assert not any(_answer(0) in passage for passage in found)


def test_a_task_heading_with_a_colon_keeps_its_instruction() -> None:
    """Given a task line written "Aufgabe 1: …" above each answer, When the batch runs, Then the
    whole line is printed text, the instruction behind the colon included.
    """
    task = "Aufgabe 1: Beschreibe dein Lieblingstier in drei Sätzen."
    documents = [_document(f"doc{index}", [SCHOOL, task, _answer(index)]) for index in range(4)]

    detection = detect_task_text(documents)

    assert any(task in passage for passage in detection.passages["doc0"])


def test_a_fill_in_every_document_shares_is_printed() -> None:
    """Given a label whose fill-in reads the same in every document, When the batch runs, Then
    the line is printed text whole, and a fill-in that differs stays the student's.
    """
    documents = [
        _document(f"doc{index}", [SCHOOL, "Thema: Die Umwelt", f"Name: {name}", _answer(index)])
        for index, name in enumerate(("Anna", "Ben", "Clara", "Deniz"))
    ]

    detection = detect_task_text(documents)
    found = detection.passages["doc0"]

    assert any("Thema: Die Umwelt" in passage for passage in found)
    assert not any("Anna" in passage for passage in found)
    segments = locate(documents[0].plain, found)
    task = [documents[0].plain[seg.start : seg.end] for seg in segments if seg.kind == "task"]
    assert any("Die Umwelt" in stretch for stretch in task)


def test_a_marked_label_line_is_found_whole() -> None:
    """Given a passage that holds a label with its fill-in, When it is looked for, Then the line
    is found whole and left alone.
    """
    plain = "Aufgabe: Lies den Text!\nIch habe ihn gelesen.\n"

    segments = locate(plain, ["Aufgabe: Lies den Text!"])

    assert segments[0] == Segment(0, len("Aufgabe: Lies den Text!"), "task")


def test_one_line_headers_that_differ_are_two_worksheets() -> None:
    """Given two classes whose sheets carry one header line each, a different one, When the
    batch runs, Then they are two worksheets, each with its own task found.
    """
    task_a = "Aufgabe 1 Beschreibe das Bild auf Seite drei."
    task_b = "Aufgabe 1 Erzähle von deinen letzten Ferien."
    documents = [
        _document(f"a{index}", ["Klasse 7a Deutsch", task_a, _answer(index)]) for index in range(3)
    ] + [
        _document(f"b{index}", ["Klasse 7b Englisch", task_b, _answer(3 + index)])
        for index in range(3)
    ]

    detection = detect_task_text(documents)

    assert len(detection.worksheets) == 2
    assert any(task_a in passage for passage in detection.passages["a0"])
    assert any(task_b in passage for passage in detection.passages["b0"])


def test_a_passage_the_marks_cover_already_is_not_new() -> None:
    """Given a document marked with its header, When the batch finds the header again, a line
    more, and a line less, Then only the one with the new line is new.
    """
    plain = f"{SCHOOL}\nKlasse 8a\n{_answer(0)}\n"
    existing = (SCHOOL,)

    fresh = new_passages(plain, existing, ["Gymnasium Musterstadt", f"{SCHOOL}\nKlasse 8a"])

    assert fresh == (f"{SCHOOL}\nKlasse 8a",)


def test_a_text_without_lines_is_the_students() -> None:
    """Given a text of page labels only, When it is located, Then it is one student stretch."""
    assert locate("Page 1:\nPage 2:\n", ["Aufgabe 1"]) == (
        Segment(0, len("Page 1:\nPage 2:\n"), "student"),
    )


def test_a_printed_line_broken_differently_is_taken() -> None:
    """Given one scan that breaks a printed line at another word, When the batch runs, Then the
    two halves together are the line the other documents carry.
    """
    documents = _worksheet(4)
    documents[0] = _document(
        "doc0",
        [
            SCHOOL,
            "Klasse 8a",
            "Name: Schüler",
            "Aufgabe 1 Read the text and answer",
            "the questions.",
            _answer(0),
            TASK_TWO,
            _answer(1),
        ],
    )

    detection = detect_task_text(documents)

    found = detection.passages["doc0"]
    assert any("Read the text and answer\nthe questions." in passage for passage in found)


def test_fewer_than_three_documents_find_nothing() -> None:
    """Given two copies of one worksheet, When the batch runs, Then nothing is found: one copy
    does not repeat, and the teacher marks the lines.
    """
    detection = detect_task_text(_worksheet(2))

    assert detection.passages == {}
    assert detection.worksheets == ()


def test_a_not_task_entry_beats_the_detection() -> None:
    """Given a teacher who wants the task proofread after all in one document, When the batch
    runs, Then that line is not taken there, and the other documents keep it.
    """
    documents = _worksheet(4)
    documents[0] = TaskDocument(
        name="doc0",
        plain=documents[0].plain,
        not_task=(TASK_TWO,),
    )

    detection = detect_task_text(documents)

    assert not any(TASK_TWO in passage for passage in detection.passages["doc0"])
    assert any(TASK_ONE in passage for passage in detection.passages["doc0"])
    assert any(TASK_TWO in passage for passage in detection.passages["doc1"])


def test_a_passage_a_document_already_holds_is_found_again() -> None:
    """Given a document that already holds a batch passage, When the batch runs again, Then the
    same passage is found, so the caller can keep what it has.
    """
    documents = _worksheet(4)
    documents[0] = TaskDocument(
        name="doc0",
        plain=documents[0].plain,
        marks=(TASK_ONE,),
    )

    detection = detect_task_text(documents)

    assert any(TASK_ONE in passage for passage in detection.passages["doc0"])


def _comparisons(count: int) -> tuple[int, int]:
    """Return how many line comparisons the batch makes over *count* documents, and how many
    documents it served."""
    calls = 0

    def counting(one: str, other: str) -> float:
        nonlocal calls
        calls += 1
        return _ratio(one, other)

    detection = detect_task_text(_worksheet(count), similarity=counting)
    return calls, detection.documents_with_task_text


def test_the_comparisons_grow_with_the_queue_not_with_its_square() -> None:
    """Given queues of thirty and of three hundred documents of one worksheet, When the batch
    runs, Then every document is served, and ten times the documents take well under twenty
    times the comparisons.
    """
    small, served_small = _comparisons(30)
    large, served_large = _comparisons(300)

    assert (served_small, served_large) == (30, 300)
    assert large < 20 * small


def test_lines_keep_their_range_and_their_page() -> None:
    """Given a text with a page label and a blank line, When its lines are read, Then the label
    is no line, the blank line is none, and every line keeps its range and its page.
    """
    plain = "Page 1:\nErste Zeile\n\nZweite Zeile\nPage 2:\nDritte Zeile"

    lines = lines_of(plain)

    assert [line.text for line in lines] == ["Erste Zeile", "Zweite Zeile", "Dritte Zeile"]
    assert [line.page for line in lines] == [0, 0, 1]
    assert [plain[line.start : line.end] for line in lines] == [line.text for line in lines]


def test_normalise_drops_markers_numbering_and_blanks() -> None:
    """Given lines that differ only in a list marker, a number and a fill-in blank, When they are
    normalised, Then they are the same line.
    """
    assert normalise("- Name: ____") == normalise("1.  name: .....")
    assert normalise("Aufgabe 1: Lies den Text!") == "aufgabe 1: lies den text"


def test_segments_cover_the_text_without_gaps() -> None:
    """Given a text with one marked passage, When it is located, Then the segments follow each
    other without a gap, and the passage is left alone.
    """
    plain = "\n".join([SCHOOL, TASK_ONE, _answer(0), _answer(1)])
    passages = (TASK_ONE,)

    segments = locate(plain, passages)

    assert segments[0].start == 0
    assert segments[-1].end == len(plain)
    assert all(left.end == right.start for left, right in pairwise(segments))
    task = "".join(
        plain[segment.start : segment.end] for segment in segments if segment.kind == "task"
    )
    assert task == TASK_ONE
    assert "".join(plain[segment.start : segment.end] for segment in segments) == plain


def test_a_passage_that_is_not_found_is_ignored() -> None:
    """Given a passage the text no longer holds, When it is located, Then the whole text is the
    student's, as it is without any marking.
    """
    plain = "\n".join([SCHOOL, _answer(0)])

    segments = locate(plain, ("Ein Satz, den es nicht gibt.",))

    assert segments == (Segment(0, len(plain), "student"),)


def test_a_marked_passage_is_found_with_a_misread_word() -> None:
    """Given a marked passage whose words the reading misspelled once, When it is located, Then
    the line is still left alone.
    """
    plain = "\n".join([SCHOOL, "Aufgabe 1 Read the tex and answer the questions.", _answer(0)])

    segments = locate(plain, (TASK_ONE,))

    task = [plain[segment.start : segment.end] for segment in segments if segment.kind == "task"]
    assert task == ["Aufgabe 1 Read the tex and answer the questions."]


def test_a_not_task_entry_wins_over_a_marked_passage() -> None:
    """Given a marked passage and the teacher's wish to proofread it after all, When it is
    located, Then the line is the student's again.
    """
    plain = "\n".join([TASK_ONE, _answer(0)])
    passages = (TASK_ONE,)

    segments = locate(plain, passages, (TASK_ONE,))

    assert segments == (Segment(0, len(plain), "student"),)


def test_found_passages_keep_the_text_the_document_holds() -> None:
    """Given a marked passage, When what the document really holds is asked for, Then it comes
    back with its range and with the text as the reading has it.
    """
    plain = "\n".join([SCHOOL, "Aufgabe 1 Read the tex and answer the questions.", _answer(0)])

    found = found_passages(plain, (TASK_ONE,))

    assert len(found) == 1
    assert found[0].text == "Aufgabe 1 Read the tex and answer the questions."
    assert plain[found[0].start : found[0].end] == found[0].text
    assert found[0].passage == TASK_ONE


def test_passage_parts_are_the_located_text() -> None:
    """Given a passage the reading spells with a misread word, When the fingerprint parts are
    asked for, Then the part is the text that is really there.
    """
    misread = "Aufgabe 1 Read the tex and answer the questions."
    plain = "\n".join([misread, TASK_TWO])

    parts = passage_parts(plain, (TASK_ONE, TASK_TWO))

    assert parts == (misread, TASK_TWO)
