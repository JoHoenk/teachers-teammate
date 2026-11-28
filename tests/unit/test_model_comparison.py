"""Tests for ``domain/proofreading/model_comparison.py``: several models on one text."""

from __future__ import annotations

from teachers_teammate.domain.proofreading.edits import edits_from_hunks
from teachers_teammate.domain.proofreading.model_comparison import Version, compare
from teachers_teammate.domain.proofreading.option_marks import OptionMark, Verdict, identity
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    EditNote,
    EditPart,
    UnclearNote,
)
from teachers_teammate.domain.proofreading.sentences import sentence_spans
from teachers_teammate.domain.proofreading.word_diff import word_diff

SOURCE = (
    "Ich habe dass Buch gelesen. Danach bin ich schlafen gegangen.\n\n"
    "Das macht Sinn, finde ich. Meine Schwester sieht das auch so."
)

GEMMA = (
    "Ich habe das Buch gelesen, danach bin ich schlafen gegangen.\n\n"
    "Das ergibt Sinn, finde ich. Meine Schwester sieht das auch so."
)
QWEN = (
    "Ich habe das Buch gelesen. Danach bin ich schlafen gegangen.\n\n"
    "Das ist sinnvoll, finde ich. Meine Schwester sieht das genauso."
)
LLAMA = (
    "Ich habe dass Buch gelesen. Danach bin ich schlafen gegangen.\n\n"
    "Das ergibt Sinn, finde ich. Meine Schwester sieht das auch so."
)

#: A note of a model that explains nothing in its own text: an edit the review claims.
ORPHAN = EditNote(
    id=99,
    parts=(EditPart(0, 3, "Ich", "Ich"),),
    sentences=(0,),
    category="style",
    explanation="Nichts.",
)


def _review(
    corrected: str,
    *,
    category: str = "grammar",
    explanation: str = "Weil es so ist.",
    extra: EditNote | None = None,
) -> CorrectionReview:
    """Return the review a model would write about its answer *corrected* to :data:`SOURCE`."""
    found = edits_from_hunks(word_diff(SOURCE, corrected), SOURCE, sentence_spans(SOURCE))
    notes = [
        EditNote(
            id=edit.id,
            parts=edit.parts,
            sentences=edit.sentences,
            category=category,
            explanation=explanation,
        )
        for edit in found
    ]
    return CorrectionReview(edits=(*notes, *([extra] if extra is not None else [])))


def _figures(comparison, key: str):
    """Return the figures of *key* in *comparison*, which the golden answers all have."""
    figures = comparison.figures(key)
    assert figures is not None
    return figures


def _versions() -> tuple[Version, ...]:
    """Return the three answers of the golden comparison, in the order a window would list them."""
    return (
        Version("gemma", GEMMA, _review(GEMMA), seconds=90.0),
        Version("qwen", QWEN, _review(QWEN, extra=ORPHAN), seconds=30.0),
        Version("llama", LLAMA),
    )


def test_the_change_sites_hold_every_wording_in_text_order() -> None:
    """
    Given  three answers to one German text
    When   they are compared
    Then   every place any of them changed is a site, with one option per wording
    """
    comparison = compare(SOURCE, _versions())

    assert [
        (site.original, [(option.replacement, option.keys) for option in site.options])
        for site in comparison.sites
    ] == [
        ("dass", [("das", ("gemma", "qwen"))]),
        (".", [(",", ("gemma",))]),
        ("Danach", [("danach", ("gemma",))]),
        ("macht", [("ergibt", ("gemma", "llama")), ("ist", ("qwen",))]),
        ("Sinn", [("sinnvoll", ("qwen",))]),
        ("auch so", [("genauso", ("qwen",))]),
    ]


def test_two_fixes_of_one_error_are_one_site_with_two_options() -> None:
    """
    Given  two answers that correct the same words in different ways
    When   they are compared
    Then   the place is one site, the wordings are two options, and nobody agrees
    """
    comparison = compare(SOURCE, _versions())

    site = next(site for site in comparison.sites if site.original == "macht")

    assert (site.start, site.end) == (67, 72)
    assert [option.replacement for option in site.options] == ["ergibt", "ist"]
    assert (site.agreed, site.unanimous) == (2, False)


def test_shared_and_alone_count_the_other_versions() -> None:
    """
    Given  a change two versions make and one no other version touches
    When   the figures of each version are read
    Then   the shared change counts for both, and the lone one only for its model
    """
    comparison = compare(SOURCE, _versions())
    gemma, qwen, llama = comparison.versions

    assert (gemma.changes, gemma.shared, gemma.alone) == (4, 2, 2)
    assert (qwen.changes, qwen.shared, qwen.alone) == (4, 1, 2)
    assert (llama.changes, llama.shared, llama.alone) == (1, 1, 0)


def test_the_same_insertion_either_side_of_a_space_is_one_change() -> None:
    """
    Given  two versions that insert the same comma, one before the space and one after it
    When   the comparison is made
    Then   it is one site with one option, shared by both
    """
    comparison = compare(
        "Ja das ist gut",
        (Version("before", "Ja, das ist gut"), Version("after", "Ja , das ist gut")),
    )
    before, after = comparison.versions

    assert (before.changes, before.shared) == (1, 1)
    assert (after.changes, after.shared) == (1, 1)
    assert len(comparison.sites) == 1
    assert len(comparison.sites[0].options) == 1


def test_a_change_written_either_side_of_a_space_is_in_every_run() -> None:
    """
    Given  a version whose second answer inserts the comma on the other side of the space
    When   its runs are counted
    Then   the change is in both of them, so none of its changes was made once
    """
    comparison = compare(
        "Ja das ist gut",
        (Version("a", "Ja, das ist gut", repeats=("Ja , das ist gut",)),),
    )

    figures = comparison.versions[0]

    assert (figures.in_every_run, figures.once) == (1, 0)


def test_the_explanation_of_an_option_belongs_to_the_version_that_made_it() -> None:
    """
    Given  two versions that fix "macht" differently, one of them without a review
    When   the options of that site are read
    Then   each carries its own model's explanation, and the silent one explains nothing
    """
    comparison = compare(SOURCE, _versions())

    site = next(site for site in comparison.sites if site.original == "macht")
    explained, silent = site.options

    assert [(made.key, made.category, made.explanation) for made in explained.made_by] == [
        ("gemma", "grammar", "Weil es so ist."),
        ("llama", "other", ""),
    ]
    assert [(made.key, made.explanation) for made in silent.made_by] == [
        ("qwen", "Weil es so ist.")
    ]


def test_a_note_that_explains_nothing_in_the_text_is_an_orphan() -> None:
    """
    Given  a review whose extra note matches no change of its answer
    When   the figures are read
    Then   the note counts as an orphan, and the changes it does explain still count
    """
    comparison = compare(SOURCE, _versions())

    assert _figures(comparison, "qwen").explained.orphans == 1
    assert _figures(comparison, "qwen").explained.explained == 4
    assert _figures(comparison, "gemma").explained.orphans == 0


def test_a_row_is_as_wide_as_the_widest_change() -> None:
    """
    Given  an answer that deletes the end of one sentence and the whole next one
    When   the rows of the comparison are read
    Then   both sentences are one row, so the deletion is not cut in two
    """
    source = "Ich habe dass Buch gelesen. Danach bin ich schlafen gegangen."
    merged = "Ich habe das Buch gelesen und danach geschlafen."

    comparison = compare(source, (Version("gemma", merged),))

    assert len(comparison.rows) == 1
    assert comparison.rows[0].source == source
    assert comparison.rows[0].versions == (merged,)


def test_every_row_reads_the_same_in_every_version() -> None:
    """
    Given  three answers to one text
    When   the rows of the comparison are read
    Then   each row holds the student's sentence and how every version reads it
    """
    comparison = compare(SOURCE, _versions())

    assert [row.source for row in comparison.rows] == [
        "Ich habe dass Buch gelesen. ",
        "Danach bin ich schlafen gegangen.\n\n",
        "Das macht Sinn, finde ich. ",
        "Meine Schwester sieht das auch so.",
    ]
    assert [row.versions[0] for row in comparison.rows] == [
        "Ich habe das Buch gelesen, ",
        "danach bin ich schlafen gegangen.\n\n",
        "Das ergibt Sinn, finde ich. ",
        "Meine Schwester sieht das auch so.",
    ]
    assert comparison.rows[3].versions[1] == "Meine Schwester sieht das genauso."


def test_a_row_says_which_version_changed_it() -> None:
    """
    Given  one answer that changes the first sentence, and one that changes the last
    When   the rows of the comparison are read
    Then   each row says of every version whether it touched that sentence
    """
    comparison = compare(SOURCE, _versions())

    assert [row.changed for row in comparison.rows] == [
        (True, True, False),
        (True, False, False),
        (True, True, True),
        (False, True, False),
    ]


def test_no_answer_changed_a_row_nothing_touched() -> None:
    """
    Given  an answer that changes nothing at all
    When   the rows of the comparison are read
    Then   no row is marked as changed
    """
    comparison = compare(SOURCE, (Version("gemma", SOURCE),))

    assert [row.changed for row in comparison.rows] == [(False,), (False,), (False,), (False,)]


def test_a_failed_answer_reports_what_went_wrong() -> None:
    """
    Given  an answer that failed, lost text and put marks into the text
    When   its figures are read
    Then   every problem is named, in a fixed order, and the time is per 100 words
    """
    marked = (
        "Ich habe **das** Buch gelesen. Danach bin ich schlafen gegangen.\n\n"
        "Das macht Sinn, finde ich."
    )
    comparison = compare(
        SOURCE,
        (
            Version("broken", SOURCE, failed=True, left_out=1, seconds=21.0),
            Version("marked", marked),
        ),
    )
    broken, marks = comparison.versions

    assert broken.problems == ("failed", "left_out")
    assert marks.problems == ("text_lost", "marks_added")
    assert round(broken.seconds_per_100_words, 2) == 100.0


def test_a_text_that_lost_words_is_a_problem() -> None:
    """
    Given  an answer that cut most of the text away
    When   its figures are read
    Then   the loss is a problem of its own
    """
    comparison = compare(SOURCE, (Version("short", "Ich habe das Buch gelesen."),))

    assert comparison.versions[0].loss.lost
    assert comparison.versions[0].problems == ("text_lost",)


def test_the_words_of_the_text_are_counted_once() -> None:
    """
    Given  three answers to one text
    When   the comparison is read
    Then   the text's words are counted once, for the row's time per 100 words
    """
    comparison = compare(SOURCE, _versions())

    assert comparison.words == 21
    assert comparison.versions[0].words == 21


def test_an_unclear_expression_of_one_model_is_counted() -> None:
    """
    Given  a review that names one expression unclear
    When   the figures of that version are read
    Then   the unclear expression is counted, and the other versions count none
    """
    unclear = UnclearNote(
        raw_start=40,
        raw_end=47,
        expression="macht Sinn",
        why="Umgangssprache.",
        recommendation="ergibt Sinn",
    )
    review = CorrectionReview(edits=_review(QWEN).edits, unclear=(unclear,))

    comparison = compare(
        SOURCE, (Version("qwen", QWEN, review), Version("gemma", GEMMA, _review(GEMMA)))
    )

    assert [figures.unclear for figures in comparison.versions] == [1, 0]


# ── the teacher's marks ────────────────────────────────────────────────────

#: The hash of the text the marks of these tests are about.
TEXT_SHA = "sha-of-the-student-text"


def _marks(comparison, chosen: dict[str, Verdict]) -> tuple[OptionMark, ...]:
    """Return the teacher's verdicts on every option of *comparison* whose wording is *chosen*."""
    return tuple(
        OptionMark(
            text_sha=TEXT_SHA,
            start=option.start,
            end=option.end,
            replacement_sha256=identity(TEXT_SHA, option.start, option.end, option.replacement)[3],
            verdict=verdict,
        )
        for site in comparison.sites
        for option in site.options
        if (verdict := chosen.get(option.replacement)) is not None
    )


def _judged(chosen: dict[str, Verdict]):
    """Return the golden comparison again, with the teacher's verdicts on *chosen* wordings."""
    plain = compare(SOURCE, _versions(), text_sha=TEXT_SHA)
    return compare(SOURCE, _versions(), text_sha=TEXT_SHA, marks=_marks(plain, chosen))


def test_two_right_fixes_of_one_error_are_both_found() -> None:
    """
    Given  two answers that fix "macht" differently, one of them the teacher calls right too
    When   the figures are read
    Then   each of them found both fixes of that place, and missed nothing there
    """
    comparison = _judged({"ergibt": Verdict.RIGHT, "ist": Verdict.RIGHT})

    gemma, qwen, _llama = comparison.versions

    assert (gemma.marked.right, gemma.marked.found, gemma.marked.missed) == (1, 2, 0)
    assert (qwen.marked.right, qwen.marked.found, qwen.marked.missed) == (1, 2, 0)


def test_a_right_change_no_model_made_is_missed() -> None:
    """
    Given  a right fix only one of three answers made
    When   the figures of the others are read
    Then   the fix counts as missed there, and as made by the model that wrote it
    """
    comparison = _judged({"ist": Verdict.RIGHT})

    gemma, qwen, llama = comparison.versions

    assert (gemma.marked.found, gemma.marked.missed) == (0, 1)
    assert (qwen.marked.found, qwen.marked.missed) == (1, 0)
    assert (llama.marked.found, llama.marked.missed) == (0, 1)


def test_an_unmarked_change_counts_for_nothing() -> None:
    """
    Given  a comparison nobody marked
    When   the figures are read
    Then   every change is unmarked and no score is shown
    """
    comparison = compare(SOURCE, _versions(), text_sha=TEXT_SHA)

    assert [figures.marked.right for figures in comparison.versions] == [0, 0, 0]
    assert [figures.marked.unmarked for figures in comparison.versions] == [4, 4, 1]
    assert [figures.marked.f_beta for figures in comparison.versions] == [None, None, None]


def test_a_wrong_change_counts_against_the_model() -> None:
    """
    Given  an answer whose fix the teacher calls wrong
    When   its figures are read
    Then   the change is wrong, not right, and nothing is missed because of it
    """
    comparison = _judged({"ergibt": Verdict.WRONG})

    gemma = comparison.versions[0]

    assert (gemma.marked.right, gemma.marked.wrong, gemma.marked.missed) == (0, 1, 0)
    assert gemma.marked.score.precision == 0.0


def test_the_score_is_read_once_every_change_is_marked() -> None:
    """
    Given  an answer whose every change is marked
    When   its figures are read
    Then   precision, recall and F0.5 say how right it was
    """
    comparison = _judged(dict.fromkeys(("das", ",", "danach", "ergibt", "ist"), Verdict.RIGHT))

    gemma = comparison.versions[0]

    assert gemma.marked.complete
    assert (gemma.marked.right, gemma.marked.missed) == (4, 0)
    assert gemma.marked.score.precision == 1.0
    assert round(gemma.marked.f_beta, 3) == 1.0


def test_the_marks_of_another_text_decide_nothing() -> None:
    """
    Given  marks that belong to another text than the one compared
    When   the figures are read
    Then   no change is marked, and an answer is neither judged nor missed
    """
    plain = compare(SOURCE, _versions(), text_sha=TEXT_SHA)
    marks = _marks(plain, {"ergibt": Verdict.RIGHT, "ist": Verdict.RIGHT})

    comparison = compare(SOURCE, _versions(), text_sha="another-text", marks=marks)

    assert [figures.marked.unmarked for figures in comparison.versions] == [4, 4, 1]
    assert [figures.marked.missed for figures in comparison.versions] == [0, 0, 0]


def test_a_second_answer_that_repeats_every_change_leaves_nothing_to_chance() -> None:
    """
    Given  a model whose second answer changed the same places as its first
    When   the comparison is read
    Then   the model is one column with two runs, every change in both, none only once
    """
    versions = (Version("gemma", GEMMA, _review(GEMMA), repeats=(GEMMA,)),)

    comparison = compare(SOURCE, versions)

    gemma = _figures(comparison, "gemma")
    assert len(comparison.versions) == 1
    assert gemma.runs == 2
    assert (gemma.changes, gemma.in_every_run, gemma.once) == (4, 4, 0)
    assert [option.once for site in comparison.sites for option in site.options] == [
        False,
        False,
        False,
        False,
    ]


def test_a_change_one_answer_only_made_is_unsure() -> None:
    """
    Given  a model whose second answer left the place its first answer fixed alone
    When   the comparison is read
    Then   that option is unsure, and one change of the model came only once
    """
    again = GEMMA.replace("Das ergibt Sinn", "Das macht Sinn")
    versions = (Version("gemma", GEMMA, _review(GEMMA), repeats=(again,)),)

    comparison = compare(SOURCE, versions)

    site = next(site for site in comparison.sites if site.original == "macht")
    assert [(option.replacement, option.once) for option in site.options] == [("ergibt", True)]
    gemma = _figures(comparison, "gemma")
    assert (gemma.changes, gemma.in_every_run, gemma.once) == (4, 3, 1)


def test_a_period_added_at_the_end_of_the_text_is_in_the_last_row() -> None:
    """
    Given  a text with no final period, and one answer that adds it and one that leaves it
    When   the rows of the comparison are read
    Then   the answer that added it changed the row and reads it with the period; the other not
    """
    source = "Ich gehe nach Hause"

    comparison = compare(
        source,
        (Version("a", "Ich gehe nach Hause."), Version("b", "Ich gehe nach Hause")),
    )

    assert [(row.versions, row.changed) for row in comparison.rows] == [
        (("Ich gehe nach Hause.", "Ich gehe nach Hause"), (True, False))
    ]


def test_a_word_inserted_where_a_sentence_starts_is_in_that_sentences_row() -> None:
    """
    Given  an answer that inserts a word before the second sentence
    When   the rows of the comparison are read
    Then   that sentence's row is the changed one, reads with the word, and the first row is not
    """
    source = "Er kam. ich ging."

    comparison = compare(source, (Version("a", "Er kam. Dann ich ging."),))

    assert [(row.source, row.versions, row.changed) for row in comparison.rows] == [
        ("Er kam. ", ("Er kam. ",), (False,)),
        ("ich ging.", ("Dann ich ging.",), (True,)),
    ]


def test_every_version_reads_back_as_its_whole_text_with_insertions_at_the_edges() -> None:
    """
    Given  answers that insert at the start of a sentence and at the end of the text
    When   each version's rows are joined
    Then   they give that version's whole answer
    """
    source = "Er kam. ich ging"
    answers = {
        "a": "Er kam. Dann ich ging",
        "b": "Er kam. ich ging.",
        "c": "Er kam. Dann ich ging.",
    }

    comparison = compare(source, tuple(Version(key, text) for key, text in answers.items()))

    for index, text in enumerate(answers.values()):
        assert "".join(row.versions[index] for row in comparison.rows) == text


def test_the_marks_of_a_version_are_those_of_its_first_answer() -> None:
    """
    Given  a model whose second answer wrote a change its first did not, and the teacher called
           both wordings right
    When   the figures are read
    Then   the marks count the changes of the first answer, as its other figures do
    """
    second = GEMMA.replace("Das ergibt Sinn", "Das macht Sinn").replace("auch so", "genauso")
    versions = (Version("gemma", GEMMA, _review(GEMMA), repeats=(second,)),)
    plain = compare(SOURCE, versions, text_sha=TEXT_SHA)
    every_wording = {option.replacement: Verdict.RIGHT for s in plain.sites for option in s.options}
    assert "genauso" in every_wording  # only the second answer wrote this one

    judged = compare(SOURCE, versions, text_sha=TEXT_SHA, marks=_marks(plain, every_wording))

    gemma = _figures(judged, "gemma")
    assert gemma.changes == 4
    assert (gemma.marked.right, gemma.marked.unmarked) == (4, 0)


def test_a_text_pair_is_diffed_once_for_every_reader() -> None:
    """
    Given  a source and an answer whose diff was asked for as edits
    When   the hunks of the same pair are asked for as well, and the edits again
    Then   the word diff was made once: the other readers took it from the cache
    """
    from teachers_teammate.domain.proofreading.edit_agreement import ordered_edits  # noqa: PLC0415
    from teachers_teammate.domain.proofreading.word_diff import text_opcodes  # noqa: PLC0415

    source = "Er gehen nach hause und isst Brot."
    answer = "Er geht nach Hause und isst Brot."
    text_opcodes.cache_clear()

    ordered_edits(source, answer)
    word_diff(source, answer)
    ordered_edits(source, answer)

    info = text_opcodes.cache_info()
    assert (info.misses, info.currsize) == (1, 1)
