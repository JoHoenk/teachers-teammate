"""Unit tests for the caps that keep corpus text out of a stored run (``redaction.py``)."""

from __future__ import annotations

import pytest

from tools.model_eval.redaction import (
    ELLIPSIS,
    MAX_EXPLANATION_CHARS,
    MAX_REPLACEMENT_WORDS,
    WORD_RUN,
    Redactor,
    cut_fields,
    explanation_field,
    field_is_cut,
    fits_replacement,
    forbidden_runs,
    quotes_corpus,
    replacement_field,
    shorten,
    keyed_sha256,
    sha256_text,
    word_count,
    word_runs,
    words,
    compared_words,
)

SOURCE = "Der Hund laufen schnell über die Wiese und bellt laut."
"""A source of the item whose explanations may not copy it."""
REFERENCE = "Der Hund läuft schnell über die Wiese und bellt laut."


def _redactor(*, restricted: bool = False) -> Redactor:
    """Return the caps of an item whose source and reference are the module's texts."""
    return Redactor.for_item(
        SOURCE, REFERENCE, restricted=restricted, key=b"corpus-key" if restricted else b""
    )


# ── words and runs ───────────────────────────────────────────────────────────


def test_words_count_marks_but_not_whitespace():
    """
    Given  a text with punctuation and extra spaces
    When   its words are listed and counted
    Then   marks count as words and whitespace does not
    """
    assert words("Der Hund , der bellt .") == ("Der", "Hund", ",", "der", "bellt", ".")
    assert word_count("  Der   Hund  ") == 2
    assert word_count("") == 0


def test_compared_words_drop_the_marks_of_the_whole_text():
    """
    Given  a text with Markdown bold and an HTML tag around words
    When   the words for comparison are taken
    Then   the marks are gone and only the words are left
    """
    assert compared_words("Der **Hund** und <sup>die</sup> Katze") == (
        "Der",
        "Hund",
        "und",
        "die",
        "Katze",
    )


def test_word_runs_are_case_folded_and_ignore_marks():
    """
    Given  a text with a bold word and mixed case
    When   its runs of three words are taken
    Then   they are case folded, ignore the marks and there is one run per start position
    """
    runs = word_runs("Der **Hund** laufen schnell über die Wiese", size=3)
    assert ("der", "hund", "laufen") in runs
    assert len(runs) == 5


def test_a_text_shorter_than_the_run_has_no_runs():
    """
    Given  a text of two words
    When   its runs of the forbidden length are taken
    Then   there are none
    """
    assert word_runs("zwei Worte", size=WORD_RUN) == ()


def test_quotes_corpus_finds_five_consecutive_source_words():
    """
    Given  the forbidden runs of a source and its reference
    When   a text with five and one with four consecutive source words is checked
    Then   only the one with five counts as quoting the corpus
    """
    forbidden = forbidden_runs(SOURCE, REFERENCE)
    assert quotes_corpus("Der Hund laufen schnell über", forbidden)
    assert not quotes_corpus("Der Hund laufen schnell", forbidden)


# ── replacement of a model edit ──────────────────────────────────────────────


def test_replacement_of_nine_words_becomes_a_hash():
    """
    Given  a replacement of nine words, one more than the cap
    When   it is stored as a field
    Then   only its hash and word count are kept, and the field counts as cut
    """
    long = " ".join(f"wort{number}" for number in range(1, 10))
    assert word_count(long) == MAX_REPLACEMENT_WORDS + 1
    field = replacement_field(long, frozenset())
    assert field == {"h": sha256_text(long), "n": 9}
    assert field_is_cut(field)


def test_replacement_of_eight_words_stays_text():
    """
    Given  a replacement of eight words, exactly the cap
    When   it is stored as a field
    Then   the text is kept
    """
    eight = " ".join(f"wort{number}" for number in range(1, 9))
    assert word_count(eight) == MAX_REPLACEMENT_WORDS
    assert replacement_field(eight, frozenset()) == {"r": eight}


def test_a_short_replacement_that_copies_the_source_becomes_a_hash():
    """
    Given  a short replacement that copies five words of the source
    When   it is stored by the caps of the item
    Then   only the hash and the word count are kept
    """
    field = _redactor().replacement("laufen schnell über die Wiese")
    assert field["h"] == sha256_text("laufen schnell über die Wiese")
    assert field["n"] == 5


def test_a_short_replacement_of_the_models_own_words_stays_text():
    """
    Given  a short replacement in the model's own words
    When   it is stored by the caps of the item
    Then   the text is kept
    """
    assert _redactor().replacement("bellt sehr laut") == {"r": "bellt sehr laut"}


def test_a_deletion_is_stored_as_an_empty_replacement():
    """
    Given  a replacement that deletes words
    When   it is stored as a field
    Then   the empty text is kept and it fits the caps
    """
    assert replacement_field("", frozenset()) == {"r": ""}
    assert fits_replacement("", frozenset())


# ── explanations ─────────────────────────────────────────────────────────────


def test_five_copied_words_cut_the_explanation_to_an_ellipsis():
    """
    Given  an explanation that copies five words of the source
    When   it is stored by the caps of the item
    Then   the text is replaced by an ellipsis and its hash is kept
    """
    copied = "Hier steht laufen schnell über die Wiese, das ist falsch."
    field = _redactor().explanation(copied)
    assert field["explanation"] == ELLIPSIS
    assert field["explanation_sha256"] == sha256_text(copied)


def test_four_copied_words_are_kept():
    """
    Given  an explanation that copies only four words of the source
    When   it is stored by the caps of the item
    Then   it is kept in full and no hash is recorded
    """
    kept = "Hier steht laufen schnell über die, das ist falsch."
    field = _redactor().explanation(kept)
    assert field["explanation"] == kept
    assert field["explanation_sha256"] is None


def test_a_mark_between_the_words_does_not_help():
    """
    Given  an explanation that copies five source words with a bold mark inside
    When   it is stored by the caps of the item
    Then   it is cut all the same
    """
    copied = "Hier steht laufen **schnell** über die Wiese."
    assert _redactor().explanation(copied)["explanation"] == ELLIPSIS


def test_a_long_explanation_is_shortened_and_marked():
    """
    Given  an explanation far over the character cap
    When   it is shortened
    Then   it has exactly the cap's length and ends with an ellipsis
    """
    long = "Ein sehr langer Satz. " * 100
    shortened = shorten(long)
    assert len(shortened) == MAX_EXPLANATION_CHARS
    assert shortened.endswith(ELLIPSIS)


def test_an_explanation_of_a_restricted_item_keeps_no_words():
    """
    Given  an item of a restricted corpus
    When   an explanation is stored
    Then   no words are kept, only the hash, and the field counts as cut
    """
    field = _redactor(restricted=True).explanation("Das Verb muss im Plural stehen.")
    assert field == {
        "explanation": "",
        "explanation_sha256": keyed_sha256(b"corpus-key", "Das Verb muss im Plural stehen."),
    }
    assert field_is_cut(field)


def test_a_restricted_item_keeps_no_replacement_even_a_short_one():
    """
    Given  an item of a restricted corpus
    When   a one-word replacement is stored
    Then   only its hash and word count are kept
    """
    field = _redactor(restricted=True).replacement("läuft")
    assert field == {"h": keyed_sha256(b"corpus-key", "läuft"), "n": 1}


# ── the strict check ─────────────────────────────────────────────────────────


def test_accepts_what_the_caps_produce_and_refuses_a_copied_run():
    """
    Given  the caps of an item
    When   fields are checked in the strict mode
    Then   what the caps produce passes, a copied run or a long replacement is refused
    """
    redactor = _redactor()
    assert redactor.accepts(redactor.replacement("bellt sehr laut"))
    assert redactor.accepts_explanation("Hier steht ein anderer Fehler.")
    assert not redactor.accepts({"r": "laufen schnell über die Wiese"})
    assert not redactor.accepts_explanation("Hier steht laufen schnell über die Wiese.")
    assert not redactor.accepts({"r": " ".join(["wort"] * (MAX_REPLACEMENT_WORDS + 1))})


def test_the_cut_forms_are_accepted_so_a_second_export_changes_nothing():
    """
    Given  fields the caps have already cut
    When   they are checked again
    Then   they are accepted, so exporting twice changes nothing
    """
    redactor = _redactor()
    for field in (
        redactor.replacement("laufen schnell über die Wiese"),
        redactor.explanation("Hier steht laufen schnell über die Wiese."),
    ):
        assert redactor.accepts(field)
    assert redactor.accepts_explanation(ELLIPSIS)


def test_a_restricted_item_refuses_any_explanation_text():
    """
    Given  an item of a restricted corpus
    When   explanations are checked
    Then   only the empty text passes, even the ellipsis is refused
    """
    redactor = _redactor(restricted=True)
    assert redactor.accepts_explanation("")
    assert not redactor.accepts_explanation("Das Verb muss im Plural stehen.")
    assert not redactor.accepts_explanation(ELLIPSIS)


def test_an_explanation_over_the_character_cap_is_refused():
    """
    Given  an explanation one character over the cap
    When   it is checked
    Then   it is refused
    """
    redactor = _redactor()
    assert not redactor.accepts_explanation("a" * (MAX_EXPLANATION_CHARS + 1))


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ([{"r": "läuft"}, {"h": "ab" * 32, "n": 2}], 1),
        ([{"explanation": "", "explanation_sha256": "ab" * 32}], 1),
        ([{"explanation": "", "explanation_sha256": None}], 0),
    ],
)
def test_cut_fields_counts_what_had_to_be_cut(fields, expected):
    """
    Given  fields with hashed replacements, withheld explanations and kept ones
    When   the cut fields are counted
    Then   only the fields that lost their text are counted
    """
    assert cut_fields(fields) == expected


def test_a_restricted_item_hashes_with_the_corpus_key_not_a_plain_hash():
    """
    Given  a restricted item and an open one
    When   each hashes the same replacement
    Then   the open item keeps the plain SHA-256, the restricted one a keyed hash that differs from
           it and from the same text under another key, so a dictionary cannot reverse it
    """
    plain = sha256_text("läuft")
    open_item = Redactor.for_item(SOURCE, REFERENCE).replacement(
        "Der Hund läuft schnell über die Wiese und bellt laut"
    )
    keyed = _redactor(restricted=True).replacement("läuft")
    other = Redactor.for_item(SOURCE, REFERENCE, restricted=True, key=b"other").replacement("läuft")

    assert open_item["h"] == sha256_text("Der Hund läuft schnell über die Wiese und bellt laut")
    assert keyed["h"] != plain
    assert keyed["h"] != other["h"]
    assert keyed["h"] == keyed_sha256(b"corpus-key", "läuft")


def test_a_restricted_item_without_a_key_is_refused():
    """
    Given  an item of a restricted corpus and no corpus key
    When   its caps are built
    Then   it is refused: a plain hash would be reversible
    """
    with pytest.raises(ValueError, match="corpus key"):
        Redactor.for_item(SOURCE, REFERENCE, restricted=True)
