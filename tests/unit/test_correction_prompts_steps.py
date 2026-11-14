"""Unit tests for the fixed prompts of the exchange (``domain/correction_prompts.py``)."""

from __future__ import annotations

from teachers_teammate.domain.correction_prompts import (
    CORRECT_SYSTEM,
    EXPLAIN_SYSTEM,
    NOTES_HEADING,
    NOTES_LIMIT,
    PROMPT_VERSION,
    UNCLEAR_SYSTEM,
    correct_user,
    explain_body,
    explain_user,
    notes_block,
    parameters,
    unclear_user,
)
from teachers_teammate.domain.proofreading.edits import batches, edits, sentence_groups
from teachers_teammate.domain.proofreading.sentences import sentence_spans
from teachers_teammate.domain.proofreading.word_diff import word_diff


def test_the_parameters_name_both_languages_and_the_level() -> None:
    """
    Given  a text language, an explanation language and a level
    When   the parameter lines are built
    Then   all three are named, so no prompt needs a language placeholder
    """
    lines = parameters("German", level="B1", explanation_language="English")

    assert lines == (
        "Text language: German\nExplanation language: English\nCEFR level of the student: B1"
    )


def test_the_explanation_language_defaults_to_the_text_language() -> None:
    """
    Given  no explanation language
    When   the parameter lines are built
    Then   the text language is named for the explanations as well
    """
    assert "Explanation language: German" in parameters("German", level="A2")


def test_notes_are_a_marked_block() -> None:
    """
    Given  teacher notes
    When   the notes block is built
    Then   the heading marks them as context, not as an instruction
    """
    assert notes_block("  Klasse 7b, viele Flüchtlinge.  ") == (
        f"\n\n{NOTES_HEADING}\nKlasse 7b, viele Flüchtlinge."
    )


def test_without_notes_there_is_no_block() -> None:
    """
    Given  no teacher notes
    When   the notes block is built
    Then   it is empty, so the user message has no empty heading
    """
    assert notes_block("   ") == ""


def test_the_correct_message_carries_parameters_notes_and_text() -> None:
    """
    Given  a text, a language, a level and notes
    When   the user message of step ① is built
    Then   it holds the parameters, the notes and the text, in that order
    """
    user = correct_user("Ich ging.", language="German", level="B1", notes="Klasse 7b")

    assert user.startswith("Text language: German")
    assert NOTES_HEADING in user
    assert user.endswith("Text:\n\nIch ging.")


def test_the_explain_message_carries_the_edits() -> None:
    """
    Given  a body of numbered edits
    When   the user message of step ② is built
    Then   it holds the parameters and the body under the edits heading
    """
    user = explain_user('⟦1⟧ "a" → "b"', language="German", level="B1")

    assert user.endswith('Edits:\n\n⟦1⟧ "a" → "b"')


def test_the_unclear_message_carries_the_corrected_text() -> None:
    """
    Given  a corrected text
    When   the user message of step ③ is built
    Then   it holds the text under the text heading
    """
    user = unclear_user("Ich ging.", language="German", level="B1")

    assert user.endswith("Text:\n\nIch ging.")


def test_the_body_shows_every_edit_numbered_in_its_sentence() -> None:
    """
    Given  a correction with one edit
    When   the body of the explain request is built
    Then   the sentence is shown before and after, the edit marked and listed
    """
    source = "Ich habe ein grosse Hund."
    corrected = "Ich habe einen großen Hund."
    hunks = word_diff(source, corrected)
    spans = sentence_spans(source)
    found = edits(source, corrected)
    batch = batches(found, sentence_groups(spans, found), 20)[0]

    body = explain_body(source, corrected, spans, batch, hunks)

    assert "Sentence 1 (before): Ich habe ein grosse Hund." in body
    assert "Sentence 1 (after):  Ich habe ⟦1⟧einen ⟦1⟧großen Hund." in body
    assert '⟦1⟧ "ein grosse" → "einen großen"' in body


def test_the_body_labels_a_group_of_two_sentences() -> None:
    """
    Given  a correction that joins two sentences into one edit
    When   the body of the explain request is built
    Then   the label names both sentences
    """
    source = "Ich ging nach Hause. Weil es regnete."
    corrected = "Ich ging nach Hause, weil es regnete."
    hunks = word_diff(source, corrected)
    spans = sentence_spans(source)
    found = edits(source, corrected)
    batch = batches(found, sentence_groups(spans, found), 20)[0]

    body = explain_body(source, corrected, spans, batch, hunks)

    assert "Sentences 1-2 (before):" in body
    assert "Sentences 1-2 (after):" in body


def test_the_prompts_are_versioned_and_english() -> None:
    """
    Given  the three fixed system prompts
    When   they are read
    Then   they carry a version, name no language placeholder and no output format
    """
    assert PROMPT_VERSION
    for prompt in (CORRECT_SYSTEM, EXPLAIN_SYSTEM, UNCLEAR_SYSTEM):
        assert "{language}" not in prompt
        assert "{level}" not in prompt
        assert "JSON" not in prompt  # the answer shape is the schema's, not the prompt's


def test_the_notes_limit_is_a_short_block() -> None:
    """
    Given  the notes limit
    When   it is read
    Then   it is short enough to stay context, not a second instruction
    """
    assert 0 < NOTES_LIMIT <= 1000
