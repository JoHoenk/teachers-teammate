"""Unit tests for proofreading a long text in several requests (``ChunkingCorrector``)."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.correction_review import (
    ChangeNote,
    CorrectionOutcome,
    CorrectionReview,
    SentenceNote,
    UnclearNote,
)
from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.text_chunks import split_for_budget
from teachers_teammate.infrastructure.correction import (
    CHUNK_FAILURE,
    FALLBACK_WARNING,
    MISSING_REVIEW_WARNING,
    ChunkingCorrector,
)
from teachers_teammate.interfaces import Corrector

_SENTENCE = "Das ist ein deutscher Satz. "


def _german_text(paragraphs: int = 25) -> str:
    """Return a German text of about 25,000 characters in *paragraphs* paragraphs."""
    body = _SENTENCE * 35
    body = body + "X" * (1000 - len(body)) + "."
    return "\n\n".join([body] * paragraphs)


def _sentence(original: str, corrected: str) -> SentenceNote:
    """Return a sentence note with one explained change."""
    return SentenceNote(
        original=original,
        corrected=corrected,
        changes=(
            ChangeNote(
                original=original,
                corrected=corrected,
                category="spelling",
                explanation="Tippfehler.",
            ),
        ),
    )


class _RecordingCorrector(Corrector):
    """Stand-in for the wrapped corrector: records its calls and scripted answers.

    ``failing`` holds the indices of the calls that fail, ``without_review`` the ones that
    fall back to text mode.  Every other call answers with the upper-cased text and a review
    of two sentences, of which the second one is ``unplaced``.
    """

    def __init__(
        self,
        *,
        max_chars: int | None = 10_000,
        notation_overhead: int = 0,
        failing: frozenset[int] = frozenset(),
        without_review: frozenset[int] = frozenset(),
        fallback_warning: str | None = FALLBACK_WARNING,
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
        del language, text_format
        index = len(self.calls)
        self.calls.append(raw_text)
        if index in self._failing:
            return CorrectionOutcome(text=raw_text, warning="server unreachable", failed=True)
        if index in self._without_review:
            return CorrectionOutcome(
                text=raw_text.upper(),
                review=None,
                warning=self._fallback_warning,
                fell_back=True,
            )
        review = CorrectionReview(
            sentences=(_sentence("a", "A"), _sentence("b", "B")),
            unclear=(UnclearNote(expression="c", why="d", recommendation="e"),),
            unplaced=(1,),
        )
        return CorrectionOutcome(text=raw_text.upper(), review=review)


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
def test_the_reviews_of_all_pieces_are_merged_and_unplaced_shifted() -> None:
    """
    Given  a long text whose every piece explains two sentences, one of them unplaceable
    When   it is proofread through the chunking corrector
    Then   the merged review counts all six sentences and points at the right unplaced ones
    """
    text = _german_text()
    corrector = ChunkingCorrector(_RecordingCorrector())

    outcome = corrector.correct(text, "German")

    assert outcome.review is not None
    assert len(outcome.review.sentences) == 6
    assert len(outcome.review.unclear) == 3
    assert outcome.review.unplaced == (1, 3, 5)


@pytest.mark.use_case("Chunking_Corrector")
def test_one_failed_piece_fails_the_whole_file_with_the_original_text() -> None:
    """
    Given  a long text whose second piece fails
    When   it is proofread through the chunking corrector
    Then   the outcome is failed and carries the untouched source text
    """
    text = _german_text()
    corrector = ChunkingCorrector(_RecordingCorrector(failing=frozenset({1})))

    outcome = corrector.correct(text, "German")

    assert outcome.failed is True
    assert outcome.text == text
    assert outcome.review is None
    assert outcome.warning is not None
    assert "server unreachable" in outcome.warning


@pytest.mark.use_case("Chunking_Corrector")
def test_a_failed_piece_without_a_message_still_says_the_file_failed() -> None:
    """
    Given  a long text whose one piece fails without a message of its own
    When   it is proofread through the chunking corrector
    Then   the warning is the chunking failure message, so the user is not left without one
    """
    text = _german_text()

    class _Silent(_RecordingCorrector):
        def correct(
            self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
        ) -> CorrectionOutcome:
            self.calls.append(raw_text)
            return CorrectionOutcome(text=raw_text, failed=True)

    outcome = ChunkingCorrector(_Silent()).correct(text, "German")

    assert outcome.warning == CHUNK_FAILURE


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
    assert len(outcome.review.sentences) == 4  # the two pieces that answered


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

    outcome = ChunkingCorrector(inner).correct(text, "German")

    assert outcome.failed is True
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
