"""Unit tests for proofreading a long text in several requests (``ChunkingCorrector``)."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.result import CorrectionOutcome
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    EditNote,
    UnclearNote,
    fits,
)
from teachers_teammate.domain.text_chunks import split_for_budget
from teachers_teammate.exceptions import StageError, StageErrorKind
from teachers_teammate.infrastructure.proofreading.chunking import (
    CHUNK_FAILURE,
    MISSING_REVIEW_WARNING,
    ChunkingCorrector,
)
from teachers_teammate.infrastructure.ocr_formats import plain_text_of
from teachers_teammate.interfaces import Corrector

_SENTENCE = "Das ist ein deutscher Satz. "

_FALLBACK = "Explanations could not be produced for this file; only the corrections are shown."
"""What a piece that fell back to corrections only says; the wrapper joins it."""


def _german_text(paragraphs: int = 25) -> str:
    """Return a German text of about 25,000 characters in *paragraphs* paragraphs."""
    body = _SENTENCE * 35
    body = body + "X" * (1000 - len(body)) + "."
    return "\n\n".join([body] * paragraphs)


def _review_of(text: str, text_format: TextFormat = "plain") -> CorrectionReview:
    """Return a review of the plain text of *text*: one explained edit and one unclear
    expression, both on its first word."""
    plain = plain_text_of(text, text_format)
    word = plain.split(maxsplit=1)[0] if plain.split() else ""
    end = len(word)
    return CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(raw_start=0, raw_end=end, original=word, corrected=word.upper()),),
                sentences=(0,),
                category="spelling",
                explanation="Tippfehler.",
            ),
        ),
        unclear=(
            UnclearNote(
                raw_start=0,
                raw_end=end,
                expression=word,
                why="d",
                recommendation="e",
            ),
        ),
    )


class _RecordingCorrector(Corrector):
    """Stand-in for the wrapped corrector: records its calls and scripted answers.

    ``failing`` holds the indices of the calls that fail, ``without_review`` the ones that
    fall back to text mode.  Every other call answers with the upper-cased text and a review of
    one edit and one unclear expression, both on the first word of the piece.
    """

    def __init__(
        self,
        *,
        max_chars: int | None = 10_000,
        notation_overhead: int = 0,
        failing: frozenset[int] = frozenset(),
        without_review: frozenset[int] = frozenset(),
        fallback_warning: str | None = _FALLBACK,
    ) -> None:
        self.calls: list[str] = []
        self._max_chars = max_chars
        self._notation_overhead = notation_overhead
        self._failing = failing
        self._without_review = without_review
        self._fallback_warning = fallback_warning

    def max_text_chars(self, language: str, *, text_format: TextFormat = "plain") -> int | None:
        """Return the scripted budget; Markdown costs *notation_overhead* characters of it."""
        del language
        if self._max_chars is None:
            return None
        return self._max_chars - (self._notation_overhead if text_format == "markdown" else 0)

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the scripted answer for this call."""
        del language
        index = len(self.calls)
        self.calls.append(raw_text)
        if index in self._failing:
            raise StageError("server unreachable", kind=StageErrorKind.PROVIDER, stage="correction")
        if index in self._without_review:
            return CorrectionOutcome(
                text=raw_text.upper(),
                review=None,
                warning=self._fallback_warning,
                fell_back=True,
            )
        return CorrectionOutcome(text=raw_text.upper(), review=_review_of(raw_text, text_format))


@pytest.mark.use_case("Chunking_Corrector")
def test_a_short_text_goes_to_the_wrapped_corrector_unchanged() -> None:
    """
    Given  a text shorter than the request budget
    When   it is proofread through the chunking corrector
    Then   exactly one request is made, with the whole text, as before
    """
    inner = _RecordingCorrector()
    corrector = ChunkingCorrector(inner)

    outcome = corrector.correct("Ein kurzer Satz.", "German")

    assert inner.calls == ["Ein kurzer Satz."]
    assert outcome.text == "EIN KURZER SATZ."


@pytest.mark.use_case("Chunking_Corrector")
def test_a_long_text_is_proofread_in_three_requests() -> None:
    """
    Given  a German text of about 25,000 characters and a budget of 10,000
    When   it is proofread through the chunking corrector
    Then   three requests are made and the merged text is the joined corrected pieces
    """
    text = _german_text()
    inner = _RecordingCorrector()
    corrector = ChunkingCorrector(inner)

    outcome = corrector.correct(text, "German")

    assert len(inner.calls) == 3
    chunks = split_for_budget(text, 10_000)
    assert outcome.text == "".join(chunk.text.upper() + chunk.separator for chunk in chunks)
    assert outcome.failed is False


@pytest.mark.use_case("Chunking_Corrector")
def test_the_pieces_are_the_original_text_piece_by_piece() -> None:
    """
    Given  a long text
    When   it is proofread through the chunking corrector
    Then   the requests carry the pieces of the source text, in order and without a gap
    """
    text = _german_text()
    inner = _RecordingCorrector()

    ChunkingCorrector(inner).correct(text, "German")

    assert (
        "".join(
            call + chunk.separator
            for call, chunk in zip(inner.calls, split_for_budget(text, 10_000), strict=True)
        )
        == text
    )


@pytest.mark.use_case("Chunking_Corrector")
def test_the_reviews_of_all_pieces_are_merged_onto_the_whole_text() -> None:
    """
    Given  a long text whose every piece explains one edit and one unclear expression
    When   it is proofread through the chunking corrector
    Then   the merged review holds all three edits and all three unclear notes, numbered in text
           order, and every offset points at the words it quotes in the whole text
    """
    text = _german_text()
    corrector = ChunkingCorrector(_RecordingCorrector())

    outcome = corrector.correct(text, "German")

    assert outcome.review is not None
    assert [note.id for note in outcome.review.edits] == [1, 2, 3]
    assert len(outcome.review.unclear) == 3
    assert fits(outcome.review, text)


@pytest.mark.use_case("Chunking_Corrector")
def test_a_markdown_text_keeps_its_offsets_in_the_plain_whole_text() -> None:
    """
    Given  a long Markdown text whose every piece explains one edit
    When   it is proofread through the chunking corrector
    Then   the merged review fits the plain text of the whole, so the marks land on the words
    """
    text = "\n\n".join(f"**Fett** und *kursiv* in Absatz {number}." for number in range(400))
    corrector = ChunkingCorrector(_RecordingCorrector())

    outcome = corrector.correct(text, "German", text_format="markdown")

    assert outcome.review is not None
    assert len(outcome.review.edits) > 1
    assert fits(outcome.review, plain_text_of(text, "markdown"))


@pytest.mark.use_case("Chunking_Corrector")
def test_a_piece_whose_review_does_not_fit_loses_it_and_warns() -> None:
    """
    Given  a long text whose pieces answer with a review that quotes words not at its offsets
    When   it is proofread through the chunking corrector
    Then   no review is stored and the warning says that explanations are missing
    """
    text = _german_text()

    class _WrongOffsets(_RecordingCorrector):
        """A corrector whose review quotes words that are not where it says they are."""

        def correct(
            self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
        ) -> CorrectionOutcome:
            self.calls.append(raw_text)
            return CorrectionOutcome(
                text=raw_text.upper(),
                review=CorrectionReview(
                    edits=(
                        EditNote(
                            id=1,
                            parts=(
                                EditPart(raw_start=0, raw_end=3, original="zzz", corrected="ZZZ"),
                            ),
                            sentences=(0,),
                            category="spelling",
                            explanation="Tippfehler.",
                        ),
                    ),
                ),
            )

    outcome = ChunkingCorrector(_WrongOffsets()).correct(text, "German")

    assert outcome.failed is False
    assert outcome.review is None
    assert outcome.warning is not None
    assert MISSING_REVIEW_WARNING in outcome.warning


@pytest.mark.use_case("Chunking_Corrector")
def test_one_failed_piece_fails_the_whole_file() -> None:
    """
    Given  a long text whose second piece fails
    When   it is proofread through the chunking corrector
    Then   the failure of that piece ends the stage, so no half-corrected text is returned
    """
    text = _german_text()
    inner = _RecordingCorrector(failing=frozenset({1}))
    corrector = ChunkingCorrector(inner)

    with pytest.raises(StageError) as raised:
        corrector.correct(text, "German")

    assert raised.value.kind == StageErrorKind.PROVIDER
    assert raised.value.stage == "correction"
    assert "server unreachable" in str(raised.value)
    assert len(inner.calls) == 2  # the piece after the failed one was not sent


class _Unusable(Corrector):
    """A corrector whose second piece comes back as an unusable answer, not as an error."""

    def __init__(self, warning: str | None) -> None:
        self.calls: list[str] = []
        self._warning = warning

    def max_text_chars(self, language: str, *, text_format: TextFormat = "plain") -> int | None:
        """Return the budget the text is split by."""
        return 60

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Answer the first piece, and return the source with ``failed`` for the second."""
        self.calls.append(raw_text)
        if len(self.calls) == 2:
            return CorrectionOutcome(text=raw_text, warning=self._warning, failed=True)
        return CorrectionOutcome(text=raw_text.upper())


@pytest.mark.use_case("Chunking_Corrector")
def test_a_piece_with_an_unusable_answer_fails_the_whole_text_with_the_original() -> None:
    """
    Given  a long text whose second piece comes back as a failed outcome
    When   it is proofread through the chunking corrector
    Then   the outcome is failed, carries the untouched source text and the reason of the piece,
           and the pieces after the failed one are not sent
    """
    text = _german_text()
    inner = _Unusable("The model added text of its own; keeping original text.")

    outcome = ChunkingCorrector(inner).correct(text, "German")

    assert outcome.failed is True
    assert outcome.text == text
    assert outcome.review is None
    assert outcome.warning == "The model added text of its own; keeping original text."
    assert len(inner.calls) == 2


@pytest.mark.use_case("Chunking_Corrector")
def test_an_unusable_piece_without_a_reason_says_the_file_failed() -> None:
    """
    Given  a long text whose second piece fails without a reason of its own
    When   it is proofread through the chunking corrector
    Then   the warning is the chunking failure message, so the teacher is not left without one
    """
    outcome = ChunkingCorrector(_Unusable(None)).correct(_german_text(), "German")

    assert outcome.failed is True
    assert outcome.warning == CHUNK_FAILURE


@pytest.mark.use_case("Chunking_Corrector")
def test_a_failure_without_a_message_reaches_the_caller_unchanged() -> None:
    """
    Given  a long text whose piece fails without a sentence of its own
    When   it is proofread through the chunking corrector
    Then   the failure arrives as it was raised: the wrapped corrector owns its wording
    """
    text = _german_text()

    class _Silent(_RecordingCorrector):
        def correct(
            self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
        ) -> CorrectionOutcome:
            self.calls.append(raw_text)
            raise StageError("", kind=StageErrorKind.PROVIDER, stage="correction")

    with pytest.raises(StageError):
        ChunkingCorrector(_Silent()).correct(text, "German")


@pytest.mark.use_case("Chunking_Corrector")
def test_a_piece_that_fell_back_to_text_mode_only_warns() -> None:
    """
    Given  a long text whose second piece produced no review answer
    When   it is proofread through the chunking corrector
    Then   the file is not failed but warns that explanations are missing for part of the text
    """
    text = _german_text()
    corrector = ChunkingCorrector(_RecordingCorrector(without_review=frozenset({1})))

    outcome = corrector.correct(text, "German")

    assert outcome.failed is False
    assert outcome.warning is not None
    assert MISSING_REVIEW_WARNING in outcome.warning
    assert outcome.review is not None
    assert len(outcome.review.edits) == 2  # the two pieces that answered


@pytest.mark.use_case("Chunking_Corrector")
def test_corrections_only_mode_never_warns_about_missing_explanations() -> None:
    """
    Given  a corrector that answers without a review and without a warning
    When   a long text is proofread through the chunking corrector
    Then   no warning is raised, because no explanations were asked for
    """
    text = _german_text()
    every = frozenset({0, 1, 2})
    corrector = ChunkingCorrector(_RecordingCorrector(without_review=every, fallback_warning=None))

    outcome = corrector.correct(text, "German")

    assert outcome.failed is False
    assert outcome.review is None
    assert outcome.warning is None


@pytest.mark.use_case("Chunking_Corrector")
def test_a_non_positive_budget_disables_the_split() -> None:
    """
    Given  a budget of zero, which cannot fit anything
    When   a long text is proofread
    Then   the wrapped corrector gets the whole text in one call, so nothing loops
    """
    text = _german_text()
    inner = _RecordingCorrector(max_chars=0)

    ChunkingCorrector(inner).correct(text, "German")

    assert inner.calls == [text]


@pytest.mark.use_case("Chunking_Corrector")
def test_an_unbounded_corrector_gets_the_whole_text() -> None:
    """
    Given  a wrapped corrector that reports no limit
    When   a long text is proofread
    Then   it gets the whole text in one call
    """
    text = _german_text()
    inner = _RecordingCorrector(max_chars=None)

    ChunkingCorrector(inner).correct(text, "German")

    assert inner.calls == [text]


@pytest.mark.use_case("Chunking_Corrector")
def test_the_budget_follows_the_notation_of_each_call() -> None:
    """
    Given  a wrapped corrector whose Markdown requests carry a long notation hint
    When   the same text is proofread as plain text and as Markdown
    Then   the Markdown pieces are smaller, so the hint still fits the window
    """
    text = _german_text()
    plain = _RecordingCorrector(notation_overhead=4_000)
    markdown = _RecordingCorrector(notation_overhead=4_000)

    ChunkingCorrector(plain).correct(text, "German")
    ChunkingCorrector(markdown).correct(text, "German", text_format="markdown")

    assert max(len(call) for call in plain.calls) > 6_000
    assert max(len(call) for call in markdown.calls) <= 6_000
    assert len(markdown.calls) > len(plain.calls)


@pytest.mark.use_case("Chunking_Corrector")
def test_the_pieces_after_a_failed_one_are_not_sent() -> None:
    """
    Given  a long text whose first piece fails
    When   it is proofread through the chunking corrector
    Then   no request is made for the later pieces, because their answers would be discarded
    """
    text = _german_text()
    inner = _RecordingCorrector(failing=frozenset({0}))

    with pytest.raises(StageError):
        ChunkingCorrector(inner).correct(text, "German")

    assert len(inner.calls) == 1


@pytest.mark.use_case("Chunking_Corrector")
def test_a_piece_that_fell_back_marks_the_merged_outcome_as_fallen_back() -> None:
    """
    Given  a long text whose second piece fell back to corrections only
    When   it is proofread through the chunking corrector
    Then   the merged outcome says it fell back, so no speed sample is taken of a mixed run;
           and a text whose pieces all answered with a review does not say so
    """
    text = _german_text()
    fell_back = ChunkingCorrector(_RecordingCorrector(without_review=frozenset({1})))
    clean = ChunkingCorrector(_RecordingCorrector())

    assert fell_back.correct(text, "German").fell_back is True
    assert clean.correct(text, "German").fell_back is False


@pytest.mark.use_case("Chunking_Corrector")
def test_a_repeated_piece_is_not_found_at_the_place_of_an_earlier_one_without_a_review() -> None:
    """
    Given  a long text of repeated paragraphs, whose first piece produced no review
    When   it is proofread through the chunking corrector
    Then   the explanations of the third piece stay with the third piece and not the first
    """
    text = _german_text()
    corrector = ChunkingCorrector(_RecordingCorrector(without_review=frozenset({0})))

    outcome = corrector.correct(text, "German")

    assert outcome.review is not None
    starts = [note.raw_start for note in outcome.review.edits]
    assert len(starts) == 2
    assert min(starts) > 0


@pytest.mark.use_case("Chunking_Corrector")
def test_stretches_left_out_in_the_pieces_are_added_up_and_keep_their_flag() -> None:
    """
    Given  a long text whose pieces each came back with a note for a stretch the model left out
    When   the pieces are merged
    Then   the outcome counts the stretches of every piece, and the merged review has all the
           notes, still flagged and moved to their place in the whole text
    """
    text = _german_text()

    class _LeavesOut(_RecordingCorrector):
        def correct(self, raw_text, language, *, text_format="plain"):
            outcome = super().correct(raw_text, language, text_format=text_format)
            note = UnclearNote(
                raw_start=0,
                raw_end=min(10, len(raw_text)),
                expression=raw_text[:10],
                why="left out",
                recommendation="",
                left_out=True,
            )
            review = outcome.review or CorrectionReview()
            return CorrectionOutcome(
                text=outcome.text,
                review=CorrectionReview(edits=review.edits, unclear=(note,)),
                left_out=1,
            )

    outcome = ChunkingCorrector(_LeavesOut()).correct(text, "German")

    assert outcome.left_out == 3
    assert outcome.review is not None
    assert [note.left_out for note in outcome.review.unclear] == [True, True, True]
    assert [note.raw_start for note in outcome.review.unclear] == sorted(
        note.raw_start for note in outcome.review.unclear
    )
