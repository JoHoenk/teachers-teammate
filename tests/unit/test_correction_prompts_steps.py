"""Unit tests for the fixed prompts of the exchange (``domain/correction_prompts.py``)."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.correction_prompts import (
    CORRECT_SYSTEM,
    CORRECT_VARIANTS,
    EXPLAIN_SYSTEM,
    NOTES_HEADING,
    NOTES_LIMIT,
    PROMPT_VERSION,
    UNCLEAR_SYSTEM,
    correct_system,
    correct_user,
    explain_body,
    explain_user,
    notes_block,
    parameters,
    prompt_identity,
    unclear_user,
)
from teachers_teammate.domain.proofreading.edits import batches, edits, sentence_groups
from teachers_teammate.domain.proofreading.review import CHANGE_CATEGORIES
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
    assert PROMPT_VERSION.strip()
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


def test_the_standard_prompt_is_the_default_and_a_variant_is_chosen_by_its_id() -> None:
    """
    Given  the standard prompt of step one and its named variants
    When   the prompt is asked for with no id and with each id
    Then   no id gives the standard prompt and each id gives its own text, different from the
           standard one and from the others
    """
    assert correct_system() == CORRECT_SYSTEM
    assert correct_system("") == CORRECT_SYSTEM
    texts = [correct_system(variant) for variant in CORRECT_VARIANTS]

    assert set(CORRECT_VARIANTS) == {"keep", "find", "shot"}
    assert len({CORRECT_SYSTEM, *texts}) == len(texts) + 1


def test_an_unknown_variant_is_refused_and_the_message_lists_the_known_ones() -> None:
    """
    Given  an id that names no variant
    When   the prompt is asked for
    Then   a ValueError names the id and the ids that exist
    """
    with pytest.raises(ValueError, match="no step ① prompt variant 'banana'") as refused:
        correct_system("banana")

    assert "find, keep, shot" in str(refused.value)


@pytest.mark.parametrize("variant", sorted(CORRECT_VARIANTS))
def test_a_variant_keeps_the_rules_about_the_answer_the_standard_prompt_has(variant: str) -> None:
    """
    Given  each variant of the step-one prompt
    When   its text is read
    Then   it ends as the standard prompt ends (the answer is the text, no lead-in, no remark), has
           no placeholder a message would have to fill, and asks for no answer format of its own
    """
    text = correct_system(variant)

    assert text.endswith(CORRECT_SYSTEM[CORRECT_SYSTEM.index("Keep the shape of the text") :])
    assert "{" not in text
    assert "json" not in text.lower()


@pytest.mark.parametrize("variant", sorted(CORRECT_VARIANTS))
def test_a_variant_applies_to_every_language_it_is_sent_with(variant: str) -> None:
    """
    Given  each variant of the step-one prompt, which a model gets for every text language
    When   its text is read
    Then   the variants with rules of German and English name them as examples and ask for the
           same kinds of check in the language of the text
    """
    text = correct_system(variant)

    if variant == "find":
        assert "look for the same kinds of mistake in the language of the text" in text
    if variant == "keep":
        assert "whether the student wrote it or left it out" in text


def test_the_examples_of_the_shot_variant_cannot_be_copied_as_a_label() -> None:
    """
    Given  the variant with two worked examples
    When   its text is read
    Then   the examples are labelled in a way that is not the "Text:" the message ends with or a
           "Corrected:" a model could put in front of its answer, and they say they are not the
           student's text
    """
    text = correct_system("shot")

    assert "never copy them into your answer" in text
    assert "\nText:" not in text
    assert "Corrected:" not in text
    assert text.count("your answer:") == 2


def test_the_keep_variant_forbids_adding_an_optional_comma_and_not_only_keeping_one() -> None:
    """
    Given  the variant for models that over-edit
    When   its text is read
    Then   an optional comma is no mistake whether it is there or missing, and a sentence with a
           mistake is changed only in the words of the mistake
    """
    text = correct_system("keep")

    assert "do not add them either" in text
    assert "change only the words the mistake is in" in text


def test_a_variant_has_an_identity_that_follows_its_text(monkeypatch) -> None:
    """
    Given  the identity a cache key and a fingerprint carry for a variant
    When   the text of a variant changes under the same id
    Then   the standard prompt has none, each variant has its own, and a changed text is another one
    """
    assert prompt_identity("") == ""
    assert prompt_identity("keep").startswith("keep-")
    assert len({prompt_identity(variant) for variant in CORRECT_VARIANTS}) == 3
    before = prompt_identity("keep")

    monkeypatch.setitem(CORRECT_VARIANTS, "keep", CORRECT_VARIANTS["keep"] + " Another rule.")

    assert prompt_identity("keep") != before
    with pytest.raises(ValueError, match="no step"):
        prompt_identity("banana")


def test_the_explain_prompt_names_exactly_the_categories_the_review_stores() -> None:
    """
    Given  the category line of the explain prompt and the categories a review stores
    When   the two are compared
    Then   they name the same categories, so a new category cannot be left out of the prompt
    """
    line = next(line for line in EXPLAIN_SYSTEM.splitlines() if line.startswith("Categories:"))

    named = [name.strip() for name in line.removeprefix("Categories:").rstrip(".").split(",")]

    assert named == list(CHANGE_CATEGORIES)
