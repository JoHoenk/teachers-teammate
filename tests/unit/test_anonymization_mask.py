"""Unit tests for hiding names in a text and putting them back."""

from __future__ import annotations

from teachers_teammate.domain.anonymization import mask, restore

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
