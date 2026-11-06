"""Unit tests for teachers_teammate.domain.correction_review."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.correction_review import (
    AlignedChange,
    AlignedReview,
    AlignedUnclear,
    ChangeNote,
    CorrectionReview,
    Hunk,
    SentenceNote,
    UnclearNote,
    align,
    review_summary,
    splice,
)


def _change(original: str, corrected: str, category: str = "grammar") -> ChangeNote:
    """Return a change note with a fixed explanation."""
    return ChangeNote(
        original=original, corrected=corrected, category=category, explanation="Explained."
    )


def _sentence(original: str, corrected: str, *changes: ChangeNote) -> SentenceNote:
    """Return a sentence note carrying *changes*."""
    return SentenceNote(original=original, corrected=corrected, changes=changes)


# ── splice: placing the corrected sentences ────────────────────────────────


def test_splice_replaces_an_exact_anchor_and_keeps_the_rest() -> None:
    """
    Given  a source text and a sentence whose original wording occurs verbatim
    When   splice() is called
    Then   only that sentence is replaced and nothing is reported as unplaced
    """
    text, unplaced = splice("Helo wrold. It is nice.", [_sentence("Helo wrold.", "Hello world.")])

    assert text == "Hello world. It is nice."
    assert unplaced == ()


def test_splice_matches_across_an_ocr_line_break() -> None:
    """
    Given  a source text whose sentence is broken by a line break and a sentence anchor
           written on one line
    When   splice() is called
    Then   the whitespace-insensitive lookup finds it and the line break is replaced too
    """
    text, unplaced = splice("Helo\nwrold. Fine.", [_sentence("Helo wrold.", "Hello world.")])

    assert text == "Hello world. Fine."
    assert unplaced == ()


def test_splice_falls_back_to_a_fuzzy_match() -> None:
    """
    Given  a sentence anchor that differs from the source only in its final punctuation
    When   splice() is called
    Then   the fuzzy lookup finds it and the sentence is applied
    """
    text, unplaced = splice("It is nice", [_sentence("It is nice.", "It was nice.")])

    assert text == "It was nice."
    assert unplaced == ()


def test_splice_reports_a_sentence_it_cannot_find() -> None:
    """
    Given  a sentence anchor that appears nowhere in the source text
    When   splice() is called
    Then   the text is unchanged and the sentence's index is reported as unplaced
    """
    text, unplaced = splice(
        "Completely different text.", [_sentence("Nothing like this at all here.", "Other.")]
    )

    assert text == "Completely different text."
    assert unplaced == (0,)


def test_splice_places_a_repeated_sentence_at_its_next_occurrence() -> None:
    """
    Given  the same sentence twice in the source and two notes for it in order
    When   splice() is called
    Then   each note replaces the next occurrence, not the first one twice
    """
    text, unplaced = splice(
        "It is nice. It is nice.",
        [_sentence("It is nice.", "It was nice."), _sentence("It is nice.", "It were nice.")],
    )

    assert text == "It was nice. It were nice."
    assert unplaced == ()


def test_splice_keeps_line_breaks_and_paragraphs_between_anchors() -> None:
    """
    Given  a source text with a title and two paragraphs
    When   splice() replaces one paragraph
    Then   the surrounding line breaks and paragraphs are copied verbatim
    """
    text, unplaced = splice(
        "Title\n\nFirst para.\n\nSecond para.", [_sentence("First para.", "First paragraph.")]
    )

    assert text == "Title\n\nFirst paragraph.\n\nSecond para."
    assert unplaced == ()


def test_splice_keeps_the_markdown_marks_before_an_anchor() -> None:
    """
    Given  Markdown source whose crossed-out word precedes the corrected sentence
    When   splice() replaces the sentence after the marks
    Then   the notation marks of the source survive untouched
    """
    text, _unplaced = splice("~~old~~ new sentence.", [_sentence("new sentence.", "new sentence!")])

    assert text == "~~old~~ new sentence!"


# ── align: matching notes to the local diff ────────────────────────────────


def test_align_attaches_a_note_to_its_replacement_hunk() -> None:
    """
    Given  a raw and a corrected text that differ in one replaced word, with a matching note
    When   align() is called
    Then   the single hunk carries that note
    """
    review = CorrectionReview(
        sentences=(
            _sentence(
                "Yesterday she have gone home.",
                "Yesterday she has gone home.",
                _change("have", "has"),
            ),
        )
    )

    aligned = align("Yesterday she have gone home.", "Yesterday she has gone home.", review)

    assert aligned.change_count == 1
    entry = aligned.changes[0]
    assert entry.number == 1
    assert (entry.hunk.original, entry.hunk.corrected) == ("have", "has")
    assert entry.note is not None
    assert entry.explanation == "Explained."
    assert entry.category == "grammar"
    assert aligned.orphan_notes == ()


def test_align_attaches_a_note_to_an_insertion_hunk() -> None:
    """
    Given  a note for an inserted word
    When   align() is called
    Then   the insertion hunk carries the note
    """
    review = CorrectionReview(
        sentences=(
            _sentence("I like cats.", "I like big cats.", _change("", "big", "word_choice")),
        )
    )

    aligned = align("I like cats.", "I like big cats.", review)

    assert aligned.changes[0].hunk.kind == "insert"
    assert aligned.changes[0].note is not None
    assert aligned.changes[0].category == "word_choice"


def test_align_attaches_a_note_to_a_deletion_hunk() -> None:
    """
    Given  a note for a deleted word
    When   align() is called
    Then   the deletion hunk carries the note
    """
    review = CorrectionReview(
        sentences=(_sentence("I like big cats.", "I like cats.", _change("big ", "", "other")),)
    )

    aligned = align("I like big cats.", "I like cats.", review)

    assert aligned.changes[0].hunk.kind == "delete"
    assert aligned.changes[0].note is not None


def test_align_reports_a_note_without_a_hunk_as_an_orphan() -> None:
    """
    Given  a review that claims an edit the corrected text does not contain
    When   align() is called
    Then   there is no hunk and the note is reported as an orphan
    """
    change = _change("have", "has")
    review = CorrectionReview(sentences=(_sentence("A b.", "A c.", change),))

    aligned = align("A b.", "A b.", review)

    assert aligned.changes == ()
    assert aligned.orphan_notes == (change,)


def test_align_treats_a_reverted_change_as_an_orphan() -> None:
    """
    Given  a corrected text in which the teacher reverted the model's edit
    When   align() is called
    Then   the diff of the reverted text has no hunk and the explanation is an orphan
    """
    change = _change("have", "has")
    review = CorrectionReview(sentences=(_sentence("She have gone.", "She has gone.", change),))

    aligned = align("She has gone.", "She has gone.", review)

    assert aligned.changes == ()
    assert aligned.orphan_notes == (change,)


def test_align_shows_a_teacher_addition_as_an_unexplained_change() -> None:
    """
    Given  a corrected text that contains an addition the review knows nothing about
    When   align() is called with a review without sentences
    Then   the addition appears as a hunk without a note
    """
    aligned = align("She has gone.", "She has gone home.", CorrectionReview())

    assert aligned.change_count == 1
    assert aligned.changes[0].note is None
    assert aligned.changes[0].explanation == ""
    assert aligned.changes[0].sentence == -1


def test_align_ignores_a_line_break_joined_into_a_space() -> None:
    """
    Given  a sentence written across an OCR line break that the model returned on one line
    When   align() is called
    Then   the whitespace difference is no change, so only the real edit is listed
    """
    raw = "She was 16\nyears old. It was good."
    corrected = "She was 16 years old. It were good."
    review = CorrectionReview(
        sentences=(_sentence("It was good.", "It were good.", _change("was", "were")),)
    )

    aligned = align(raw, corrected, review)

    assert [change.hunk.summary for change in aligned.changes] == ["was → were"]


def test_hunk_with_only_whitespace_left_on_one_side_is_a_deletion() -> None:
    """
    Given  a hunk whose corrected side is whitespace only
    When   the hunk's summary is read
    Then   it reads as a deletion instead of an empty replacement
    """
    aligned = align("She was 16 years old.", "She was  years old.", CorrectionReview())

    assert [change.hunk.kind for change in aligned.changes] == ["delete"]
    assert aligned.changes[0].hunk.summary == "16 → (deleted)"


def test_align_anchors_an_unclear_expression_in_the_raw_text() -> None:
    """
    Given  an unclear note whose expression occurs in the raw text
    When   align() is called
    Then   the note is anchored to the span of that expression
    """
    review = CorrectionReview(
        unclear=(UnclearNote(expression="It", why="Unclear referent.", recommendation="The film"),)
    )

    aligned = align("It was very nice.", "It was very nice.", review)

    assert aligned.unclear_count == 1
    entry = aligned.unclear[0]
    assert entry.anchored
    assert (entry.start, entry.end) == (0, 2)
    assert entry.note.why == "Unclear referent."


def test_align_anchors_a_short_unclear_expression_as_a_whole_word() -> None:
    """
    Given  the unclear expression "it", which also occurs inside "Without"
    When   align() is called
    Then   the note is anchored to the word "it", not to the letters inside "Without"
    """
    text = "Without the teacher, it was hard."
    review = CorrectionReview(
        unclear=(UnclearNote(expression="it", why="Unclear referent.", recommendation="the test"),)
    )

    entry = align(text, text, review).unclear[0]

    assert entry.start == text.index(", it ") + 2
    assert text[entry.start : entry.end] == "it"


def test_align_keeps_an_unanchored_unclear_expression_in_the_list() -> None:
    """
    Given  an unclear note whose expression does not occur in the raw text
    When   align() is called
    Then   the note is returned unanchored so the list can still show it
    """
    review = CorrectionReview(
        unclear=(UnclearNote(expression="somewhere else", why="Why.", recommendation="Fix"),)
    )

    aligned = align("It was very nice.", "It was very nice.", review)

    assert aligned.unclear_count == 1
    assert not aligned.unclear[0].anchored
    assert aligned.unclear[0].note.expression == "somewhere else"


def test_align_strips_notation_marks_from_the_anchors() -> None:
    """
    Given  a review whose anchors still carry Markdown marks and plain-text input
    When   align() is called
    Then   the anchors match the plain words and the hunk carries its note
    """
    review = CorrectionReview(
        sentences=(_sentence("I ~~hate~~ this.", "I ~~love~~ this.", _change("hate", "love")),)
    )

    aligned = align("I hate this.", "I love this.", review)

    assert aligned.change_count == 1
    assert aligned.changes[0].note is not None


def test_align_lists_sentences_that_could_not_be_placed() -> None:
    """
    Given  a review whose only sentence was not placed in the text by splice()
    When   align() is called
    Then   the sentence is reported under "not in the text" instead of being matched
    """
    sentence = _sentence("Nowhere to be found.", "Somewhere.", _change("Nowhere", "Somewhere"))
    review = CorrectionReview(sentences=(sentence,), unplaced=(0,))

    aligned = align("Completely different text.", "Completely different text.", review)

    assert aligned.unplaced == (sentence,)
    assert aligned.changes == ()
    assert aligned.orphan_notes == ()


def test_aligned_review_counts_greet_with_the_display_numbering() -> None:
    """
    Given  a review with one changed word and one unclear expression
    When   align() is called
    Then   the counts and the numbering match what the notes pane shows
    """
    review = CorrectionReview(
        sentences=(_sentence("She have gone.", "She has gone.", _change("have", "has")),),
        unclear=(UnclearNote(expression="She", why="Who?", recommendation="Anna"),),
    )

    aligned: AlignedReview = align("She have gone.", "She has gone.", review)

    assert (aligned.change_count, aligned.unclear_count) == (1, 1)
    assert aligned.changes[0].number == 1
    assert aligned.unclear[0].number == 1


# ── Records and PII ────────────────────────────────────────────────────────


def _full_review() -> CorrectionReview:
    """Return a review using every field, including an unplaced sentence."""
    return CorrectionReview(
        sentences=(
            _sentence(
                "She have gone.",
                "She has gone.",
                _change("have", "has"),
                ChangeNote("", "home", "word_choice", "Adds the destination."),
            ),
            _sentence("Not in the text.", "Still not.", _change("Not", "Still", "other")),
        ),
        unclear=(
            UnclearNote(
                expression="She",
                why="Could be someone else.",
                recommendation="Anna",
                is_suggestion=True,
            ),
        ),
        unplaced=(1,),
    )


def test_review_round_trips_through_its_dict() -> None:
    """
    Given  a review with sentences, changes, unclear notes and an unplaced index
    When   it is written with to_dict() and read back with from_dict()
    Then   the result is equal to the original
    """
    review = _full_review()

    assert CorrectionReview.from_dict(review.to_dict()) == review


def test_from_dict_rejects_an_unknown_version() -> None:
    """
    Given  a stored record with a version this build does not know
    When   from_dict() is called
    Then   it returns None so the caller simply shows no review
    """
    record = _full_review().to_dict()
    record["version"] = 99

    assert CorrectionReview.from_dict(record) is None


def test_from_dict_rejects_a_non_dict_record() -> None:
    """
    Given  a stored value that is not a dict at all
    When   from_dict() is called
    Then   it returns None instead of raising
    """
    assert CorrectionReview.from_dict("not a record") is None
    assert CorrectionReview.from_dict(None) is None


def test_map_text_applies_the_function_to_every_text_field() -> None:
    """
    Given  a review with personal names in sentences, changes and unclear notes
    When   map_text() restores the names
    Then   every text field is rewritten and the categories and flags are unchanged
    """
    restored = _full_review().map_text(lambda text: text.replace("Anna", "PERSON_1"))

    assert restored.unclear[0].recommendation == "PERSON_1"
    assert restored.unclear[0].is_suggestion is True
    assert restored.sentences[0].changes[0].explanation == "Explained."
    assert restored.unplaced == (1,)


def test_review_counts_and_placed_partition() -> None:
    """
    Given  a review whose second sentence could not be placed
    When   its counts and its placed/unplaced sentences are read
    Then   the counts cover every change and the two lists are complementary
    """
    review = _full_review()

    assert review.change_count == 3
    assert review.unclear_count == 1
    assert review.placed_sentences() == (review.sentences[0],)
    assert review.unplaced_sentences() == (review.sentences[1],)


def _counts(*, changes: int, unclear: int) -> AlignedReview:
    """Return an aligned review with the given numbers of hunks and unclear expressions."""
    hunk = Hunk(original="a", corrected="b", kind="replace")
    return AlignedReview(
        changes=tuple(AlignedChange(i + 1, hunk, None, -1) for i in range(changes)),
        unclear=tuple(
            AlignedUnclear(i + 1, UnclearNote(expression="x", why="y", recommendation="z"))
            for i in range(unclear)
        ),
    )


def test_review_summary_names_both_counts() -> None:
    """
    Given  reviews with changes, with unclear items only, and with neither
    When   review_summary() is called
    Then   it names the counts, uses the caller's separator, and is empty without content
    """
    both = _counts(changes=3, unclear=1)
    only_unclear = _counts(changes=0, unclear=1)

    assert review_summary(both) == "3 changes, 1 unclear"
    assert review_summary(both, separator=" \u00b7 ") == "3 changes \u00b7 1 unclear"
    assert review_summary(only_unclear) == "0 changes, 1 unclear"
    assert review_summary(AlignedReview()) == ""
    assert review_summary(None) == ""


def test_review_summary_uses_the_singular_for_one_change() -> None:
    """
    Given  a review with exactly one change and no unclear expression
    When   review_summary() is called
    Then   it reads "1 change" without an unclear part
    """
    assert review_summary(_counts(changes=1, unclear=0)) == "1 change"


# ── minor changes: spelling and punctuation ────────────────────────────────


def _mixed_review() -> AlignedReview:
    raw = "She have a dogs, and he run home."
    corrected = "She has a dog, and he runs home."
    review = CorrectionReview(
        sentences=(
            _sentence(
                raw,
                corrected,
                _change("have", "has", "grammar"),
                _change("dogs", "dog", "spelling"),
                _change("run", "runs", "grammar"),
            ),
        )
    )
    return align(raw, corrected, review)


def test_spelling_and_punctuation_changes_are_minor() -> None:
    """
    Given  an aligned review with two grammar changes and one spelling change
    When   the change lists are read
    Then   the spelling change is minor and not listed, the grammar changes are listed, and
           the diff count still includes all three
    """
    aligned = _mixed_review()

    assert aligned.change_count == 3
    assert [c.hunk.original for c in aligned.listed_changes] == ["have", "run"]
    assert [c.hunk.original for c in aligned.minor_changes] == ["dogs"]
    assert (aligned.listed_count, aligned.minor_count) == (2, 1)


def test_a_comma_without_a_note_is_minor_but_a_word_without_a_note_is_not() -> None:
    """
    Given  one inserted comma and one changed word, neither explained by a note
    When   is_minor is read
    Then   the comma is minor (nothing a teacher still has to be told) and the word is not
    """
    aligned = align("Tom is here and he sings.", "Tom is here, and he sang.", CorrectionReview())

    by_kind = {c.hunk.corrected.strip(): c for c in aligned.changes}
    assert by_kind[","].is_minor
    assert not by_kind["sang"].is_minor


def test_a_punctuation_note_makes_its_change_minor() -> None:
    """
    Given  a comma change that the model explained as punctuation
    When   is_minor is read
    Then   it is minor because of its category
    """
    raw, corrected = "Tom is here and sings.", "Tom is here, and sings."
    review = CorrectionReview(
        sentences=(_sentence(raw, corrected, _change("here", "here,", "punctuation")),)
    )

    aligned = align(raw, corrected, review)

    assert [c.is_minor for c in aligned.changes] == [True]


def test_review_summary_counts_only_the_listed_changes() -> None:
    """
    Given  an aligned review with two listed changes and one minor one
    When   review_summary() is called
    Then   it counts the two listed changes; a review with minor changes only names them
           as minor corrections instead of claiming "0 changes"
    """
    assert review_summary(_mixed_review()) == "2 changes"

    raw, corrected = "A dogs runs.", "A dog runs."
    only_minor = align(
        raw,
        corrected,
        CorrectionReview(
            sentences=(_sentence(raw, corrected, _change("dogs", "dog", "spelling")),)
        ),
    )
    assert review_summary(only_minor) == "1 minor correction"


# ── splice: fuzzy anchors replace the whole sentence ───────────────────────


def test_splice_fuzzy_match_replaces_the_complete_sentence() -> None:
    """
    Given  an anchor that differs from the source in the case of its first letter
    When   splice() falls back to the fuzzy lookup
    Then   the whole source sentence is replaced and no character of it is left behind
    """
    source = "Yesterday she have gone to the cinema with his friend and it were nice."
    sentence = _sentence(
        "yesterday she have gone to the cinema with his friend and it were nice.",
        "Yesterday she has gone to the cinema with her friend and it was nice.",
    )

    text, unplaced = splice(source, [sentence])

    assert text == "Yesterday she has gone to the cinema with her friend and it was nice."
    assert unplaced == ()


def test_splice_fuzzy_match_tolerates_changed_quotes() -> None:
    """
    Given  an anchor in which the model turned straight quotes into typographic ones
    When   splice() is called
    Then   the sentence is still found and replaced
    """
    source = 'He said "I goes home" to me. Then he left.'
    sentence = _sentence("He said “I goes home” to me.", 'He said "I go home" to me.')

    text, unplaced = splice(source, [sentence])

    assert text == 'He said "I go home" to me. Then he left.'
    assert unplaced == ()


def test_splice_fuzzy_match_finds_a_sentence_far_from_the_previous_one() -> None:
    """
    Given  a changed sentence that follows many unchanged sentences and a slightly altered anchor
    When   splice() is called
    Then   it is placed, because only changed sentences are returned and are rarely adjacent
    """
    source = "This sentence is fine. " * 10 + "She go to school every days."
    sentence = _sentence("she go to school every days.", "She goes to school every day.")

    text, unplaced = splice(source, [sentence])

    assert text.endswith("She goes to school every day.")
    assert text.count("This sentence is fine.") == 10
    assert unplaced == ()


def test_splice_fuzzy_match_leaves_unrelated_text_unplaced() -> None:
    """
    Given  an anchor that resembles nothing in the source
    When   splice() is called
    Then   the sentence is reported as unplaced and the text is unchanged
    """
    text, unplaced = splice("Totally different words here.", [_sentence("Completely other.", "x")])

    assert text == "Totally different words here."
    assert unplaced == (0,)


# ── align: matching notes to hunks ─────────────────────────────────────────


def test_align_explains_a_punctuation_only_change() -> None:
    """
    Given  a note for an inserted comma
    When   align() runs on the texts with that comma added
    Then   the hunk carries the note and the note is not reported as an orphan
    """
    note = ChangeNote("", ",", "punctuation", "Comma after the name.")
    review = CorrectionReview(
        sentences=(_sentence("We met Tom", "We met Tom,", note),),
    )

    aligned = align("We met Tom and left.", "We met Tom, and left.", review)

    assert [c.note for c in aligned.changes] == [note]
    assert aligned.orphan_notes == ()


def test_align_attaches_one_note_to_every_hunk_it_covers() -> None:
    """
    Given  one note for ``his friend -> her friends``, which the word diff splits in two hunks
    When   align() runs
    Then   both hunks carry the note, neither counts as the teacher's, and nothing is orphaned
    """
    note = ChangeNote("his friend", "her friends", "word_choice", "Agreement.")
    review = CorrectionReview(
        sentences=(_sentence("She met his friend.", "She met her friends.", note),)
    )

    aligned = align("She met his friend.", "She met her friends.", review)

    assert len(aligned.changes) == 2
    assert all(change.note is note for change in aligned.changes)
    assert not any(change.by_teacher for change in aligned.changes)
    assert aligned.orphan_notes == ()


def test_align_tells_a_model_change_without_note_from_a_teacher_edit() -> None:
    """
    Given  a changed sentence whose note covers one edit, a second model edit without a note,
           and a teacher's edit in a sentence the model did not touch
    When   align() runs
    Then   the unexplained model edit is not marked as the teacher's, the teacher's edit is
    """
    note = ChangeNote("have", "has", "grammar", "Agreement.")
    review = CorrectionReview(
        sentences=(_sentence("She have gone home.", "She has gone back.", note),)
    )

    aligned = align(
        "She have gone home. We was there.", "She has gone back. We were there.", review
    )

    by_text = {c.hunk.original or c.hunk.corrected: c for c in aligned.changes}
    assert by_text["have"].note is note
    assert by_text["home"].note is None
    assert by_text["home"].by_teacher is False
    assert by_text["was"].note is None
    assert by_text["was"].by_teacher is True


def test_align_anchors_unclear_expressions_whatever_their_order() -> None:
    """
    Given  unclear expressions listed in a different order than they occur in the text
    When   align() runs
    Then   every expression is anchored at its own place
    """
    raw = "It was nice. Then they left with him."
    review = CorrectionReview(
        unclear=(
            UnclearNote(expression="they", why="Who?", recommendation="the girls"),
            UnclearNote(expression="It", why="What?", recommendation="The film"),
        )
    )

    aligned = align(raw, raw, review)

    spans = {item.note.expression: (item.start, item.end) for item in aligned.unclear}
    assert spans == {"they": (18, 22), "It": (0, 2)}


def test_align_gives_a_repeated_unclear_expression_separate_places() -> None:
    """
    Given  two unclear notes with the same expression that occurs twice in the text
    When   align() runs
    Then   each note is anchored at a different occurrence
    """
    raw = "He saw it. Then it fell."
    note = UnclearNote(expression="it", why="What?", recommendation="the cup")
    aligned = align(raw, raw, CorrectionReview(unclear=(note, note)))

    assert [item.start for item in aligned.unclear] == [7, 16]


def test_align_quotes_sentences_as_they_read_in_the_corrected_text() -> None:
    """
    Given  a review whose corrected sentence the teacher has since edited
    When   align() runs
    Then   sentence_texts holds the sentence as it now reads where it can be found, and the
           model's wording where it can no longer be found
    """
    review = CorrectionReview(
        sentences=(
            _sentence("A bad one.", "A good one.", _change("bad", "good")),
            _sentence("Another bad.", "Another good.", _change("bad", "good")),
        )
    )

    aligned = align("A bad one. Another bad.", "A good one. Something else.", review)

    assert aligned.sentence_texts == ("A good one.", "Another good.")


def test_align_does_not_borrow_a_note_for_an_edit_outside_every_changed_sentence() -> None:
    """
    Given  a review that explains a comma after "Tom" in the first sentence, and a teacher
           who adds a comma of their own in an untouched second sentence
    When   align() is called
    Then   the teacher's comma has no note and is marked as the teacher's own edit, while the
           model's comma keeps its explanation
    """
    raw = "Tom said hello. However I left early."
    review = CorrectionReview(
        sentences=(
            _sentence("Tom said hello.", "Tom, said hello.", _change("Tom", "Tom,", "punctuation")),
        )
    )
    spliced, _unplaced = splice(raw, review.sentences)
    edited = spliced.replace("However I", "However, I")

    aligned = align(raw, edited, review)

    assert aligned.change_count == 2
    assert aligned.changes[0].note is not None
    assert aligned.changes[1].note is None
    assert aligned.changes[1].by_teacher is True


@pytest.mark.parametrize(
    "record",
    [
        {"version": 1, "sentences": [{"original": "a", "corrected": "b", "changes": ["x"]}]},
        {"version": 1, "sentences": ["not a sentence"]},
        {"version": 1, "unclear": [None]},
    ],
)
def test_from_dict_rejects_malformed_nested_records(record: dict) -> None:
    """
    Given  a stored record whose nested entries have the wrong shape
    When   from_dict() is called
    Then   it returns None instead of raising, so loading a file never crashes
    """
    assert CorrectionReview.from_dict(record) is None
