"""Unit tests for the answer shapes of steps ② and ③ (``infrastructure/proofreading/schemas.py``)."""

from __future__ import annotations

import json

from teachers_teammate.infrastructure.proofreading.schemas import (
    explain_notes,
    explain_schema,
    parse_explain,
    parse_unclear,
)


def _answer(*notes: tuple[int, str, str]) -> str:
    """Return an explain answer as JSON, from ``(id, category, explanation)`` triples."""
    return json.dumps(
        {
            "edits": [
                {"id": number, "category": category, "explanation": explanation}
                for number, category, explanation in notes
            ]
        }
    )


def test_the_schema_pins_the_ids_of_the_batch() -> None:
    """
    Given  a batch of two edits
    When   its answer shape is built
    Then   the id is an enum of exactly those two numbers
    """
    schema = explain_schema([3, 4]).model_json_schema()

    note = schema["$defs"]["ExplainNote"]["properties"]["id"]

    assert note["enum"] == [3, 4]


def test_the_schema_pins_the_number_of_notes() -> None:
    """
    Given  a batch of two edits
    When   its answer shape is built
    Then   the list asks for exactly two entries
    """
    edits = explain_schema([3, 4]).model_json_schema()["properties"]["edits"]

    assert (edits["minItems"], edits["maxItems"]) == (2, 2)


def test_a_complete_answer_is_accepted() -> None:
    """
    Given  one note per edit of the batch
    When   the answer is parsed
    Then   it is accepted and carries both notes
    """
    answer, error = parse_explain(_answer((1, "grammar", "Der Akkusativ."), (2, "spelling", "x")))

    assert error == ""
    assert answer is not None
    assert [note.id for note in answer.edits] == [1, 2]


def test_an_unknown_id_is_read_and_left_to_the_merge() -> None:
    """
    Given  a note for an edit the batch did not ask about
    When   the answer is parsed
    Then   it is read as it is: the merge drops and counts it, because a provider that cannot
           pin the ids answers this way and the rest of the answer is still good
    """
    answer, error = parse_explain(_answer((9, "grammar", "x")))

    assert error == ""
    assert answer is not None
    assert [note.id for note in answer.edits] == [9]


def test_a_missing_note_is_read_and_left_to_the_merge() -> None:
    """
    Given  an answer with fewer notes than the batch has edits
    When   the answer is parsed
    Then   it is read as it is: the merge reports the missing ids, which are asked for once more
    """
    answer, error = parse_explain('{"edits": []}')

    assert error == ""
    assert answer is not None
    assert answer.edits == []


def test_an_unknown_category_is_rejected() -> None:
    """
    Given  a note whose category is not one of the six
    When   the answer is parsed
    Then   it is rejected
    """
    answer, error = parse_explain(_answer((1, "style", "x")))

    assert answer is None
    assert error


def test_an_empty_unclear_list_is_a_valid_answer() -> None:
    """
    Given  a text with nothing unclear
    When   the answer is parsed
    Then   it is accepted and holds no expression
    """
    answer, error = parse_unclear('{"unclear": []}')

    assert error == ""
    assert answer is not None
    assert answer.unclear == []


def test_an_unclear_expression_carries_its_four_fields() -> None:
    """
    Given  one unclear expression
    When   the answer is parsed
    Then   the quote, the reason, the suggestion and the flag are read
    """
    raw = json.dumps(
        {
            "unclear": [
                {
                    "quote": "Er war dort.",
                    "why": "Wer ist er?",
                    "suggestion": "Mein Bruder war dort.",
                    "determinable": False,
                }
            ]
        }
    )

    answer, error = parse_unclear(raw)

    assert error == ""
    assert answer is not None
    assert answer.unclear[0].quote == "Er war dort."
    assert answer.unclear[0].determinable is False


def test_the_notes_become_domain_values() -> None:
    """
    Given  a validated explain answer
    When   its notes are read
    Then   they are the domain notes the review stores
    """
    answer, _ = parse_explain(_answer((1, "grammar", "Der Akkusativ.")))

    notes = explain_notes(answer)

    assert [(note.id, note.category, note.explanation) for note in notes] == [
        (1, "grammar", "Der Akkusativ.")
    ]
