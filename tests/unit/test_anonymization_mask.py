"""Unit tests for hiding names in a text and putting them back."""

from __future__ import annotations

from teachers_teammate.domain.anonymization import mask, restore, restore_with_offsets

ANNA = {"[PERSON_1]": "Anna Müller"}
TWO_PEOPLE = {"[PERSON_1]": "Anna", "[PERSON_2]": "Anna Müller"}


def test_restore_puts_the_original_back() -> None:
    """
    Given  a text with placeholders and the map behind them
    When   restore() is called
    Then   every occurrence carries the original again
    """
    text = "Dear [PERSON_1], [PERSON_1] wrote it."

    assert restore(text, ANNA) == "Dear Anna Müller, Anna Müller wrote it."


def test_mask_replaces_every_occurrence() -> None:
    """
    Given  a text naming a person three times
    When   mask() is called with that person's placeholder
    Then   all three occurrences are placeholders
    """
    text = "Anna Müller said: Anna Müller comes. Signed, Anna Müller."

    assert mask(text, ANNA) == "[PERSON_1] said: [PERSON_1] comes. Signed, [PERSON_1]."


def test_mask_and_restore_round_trip() -> None:
    """
    Given  a text and a placeholder map
    When   it is masked and then restored
    Then   the original text is back
    """
    text = "Anna Müller and Bernd meet at the harbour."
    mapping = ANNA | {"[PERSON_2]": "Bernd"}

    assert restore(mask(text, mapping), mapping) == text


def test_mask_prefers_the_longest_original() -> None:
    """
    Given  a map with a first name and the full name that contains it
    When   the full name appears in the text
    Then   the whole name becomes one placeholder, not the first name plus a rest
    """
    text = "Anna Müller teaches Anna."

    masked = mask(text, TWO_PEOPLE)

    assert "[PERSON_2]" in masked
    assert masked.startswith("[PERSON_2] teaches [PERSON_1].")


def test_mask_keeps_a_name_that_is_part_of_a_longer_word() -> None:
    """
    Given  a map with the name "Anna"
    When   the text contains "Annabel"
    Then   the longer word is left alone
    """
    assert mask("Annabel met Anna.", {"[PERSON_1]": "Anna"}) == "Annabel met [PERSON_1]."


def test_mask_handles_values_that_are_not_word_bounded() -> None:
    """
    Given  a map with a phone number that starts with "+"
    When   the number appears in the text
    Then   it is replaced as a whole
    """
    mapping = {"[PHONE_1]": "+49 30 1234567"}

    assert mask("Call +49 30 1234567 now.", mapping) == "Call [PHONE_1] now."


def test_mask_treats_the_original_literally() -> None:
    """
    Given  a map whose original contains regex special characters
    When   mask() is called
    Then   only that literal text is replaced
    """
    mapping = {"[ORG_1]": "Meyer & Söhne (GmbH)"}

    masked = mask("Die Meyer & Söhne (GmbH) stellt ein.", mapping)

    assert masked == "Die [ORG_1] stellt ein."


def test_mask_with_an_empty_map_or_text_changes_nothing() -> None:
    """
    Given  an empty map, and separately an empty text
    When   mask() is called
    Then   the text comes back unchanged
    """
    assert mask("Anna Müller", {}) == "Anna Müller"
    assert mask("", ANNA) == ""


def test_mask_ignores_placeholders_without_an_original() -> None:
    """
    Given  a map entry whose original is empty
    When   mask() is called
    Then   it is skipped instead of matching at every position
    """
    assert mask("Anna Müller", {"[PERSON_1]": ""}) == "Anna Müller"


def test_restore_with_offsets_returns_the_text_restore_returns() -> None:
    """
    Given  a text naming two people, one of them three times
    When   the names are put back with their offsets
    Then   the text is the one restore() gives, and every replaced run is recorded
    """
    mapping = {"[PERSON_1]": "Anna Müller", "[PERSON_10]": "Bo"}
    text = "[PERSON_1] und [PERSON_10] und [PERSON_1] und [PERSON_1]."

    restored, offsets = restore_with_offsets(text, mapping)

    assert restored == restore(text, mapping)
    assert restored == "Anna Müller und Bo und Anna Müller und Anna Müller."
    assert offsets.runs == ((0, 10, 0, 11), (15, 26, 16, 18), (31, 41, 23, 34), (46, 56, 39, 50))


def test_restore_with_offsets_carries_a_position_past_a_name() -> None:
    """
    Given  a text whose first word is a placeholder
    When   a position after the name is carried over
    Then   it moves by the characters the name is longer than the placeholder
    """
    text = "[PERSON_1] go home"
    restored, offsets = restore_with_offsets(text, ANNA)

    assert restored == "Anna Müller go home"
    assert offsets.start(11) == 12
    assert restored[12:] == text[11:]


def test_restore_with_offsets_without_a_map_changes_nothing() -> None:
    """
    Given  a text and no placeholders to put back
    When   the names are restored with their offsets
    Then   the text comes back unchanged and no run is recorded
    """
    restored, offsets = restore_with_offsets("Anna Müller", {})

    assert restored == "Anna Müller"
    assert offsets.runs == ()


def test_restore_does_not_replace_a_placeholder_that_an_original_contains() -> None:
    """
    Given  a map whose first original looks like the second placeholder
    When   restore() and restore_with_offsets() put the originals back
    Then   both give the same text and the lookalike is left as the original wrote it
    """
    mapping = {"[PERSON_1]": "see [PERSON_2]", "[PERSON_2]": "Anna"}
    text = "[PERSON_1] and [PERSON_2]"

    restored, _offsets = restore_with_offsets(text, mapping)

    assert restore(text, mapping) == restored == "see [PERSON_2] and Anna"
