"""Proofreading a text that is too long for one request, in several pieces.

:class:`ChunkingCorrector` decorates any :class:`~teachers_teammate.interfaces.Corrector` whose
provider can only carry a limited amount of text per request; :func:`proofreading_pieces` counts
the requests a text takes, so the GUI counter, the console line and the run cannot disagree.

The pieces are the app's own concern, not the exchange's: an online provider manages its own
window and is left unwrapped, while the Ollama port asks for a fixed one.
"""

from __future__ import annotations

from ...domain.formatted_text import TextFormat
from ...domain.proofreading.result import CorrectionOutcome
from ...domain.proofreading.review import CorrectionReview, fits, merge_reviews
from ...domain.text_chunks import Chunk, split_for_budget
from ...interfaces import Corrector
from ..ocr_formats import plain_text_of

CHUNK_FAILURE = "Proofreading part of the text failed; keeping the original text."
"""Warning of a chunked file whose one piece failed: no half-corrected text is ever stored."""

MISSING_REVIEW_WARNING = "Explanations are missing for part of the text."
"""Warning when only some pieces of a long text got a review answer (the rest lack them)."""


class ChunkingCorrector(Corrector):
    """Proofreads a text that is too long for one request in several pieces.

    Wraps a corrector whose provider can only carry a limited amount of text per request.  The
    wrapped corrector says how much that is (:meth:`Corrector.max_text_chars`), asked for each
    call because the budget depends on the notation: Markdown adds a notation hint to the
    system prompt.  A text that fits — and every text when the budget is not positive — goes to
    the wrapped corrector unchanged, so the common case behaves exactly as before.  A longer
    text is split by :func:`~teachers_teammate.domain.text_chunks.split_for_budget` at
    paragraph, then sentence boundaries; each piece is corrected on its own and the answers are
    merged back into one text, with the original whitespace between the pieces.

    Splitting costs the context across a boundary: a change that depends on a sentence before
    the cut cannot be made.  Cutting at paragraph and sentence ends keeps that rare.

    Failure is all-or-nothing: one failed piece fails the file and keeps the original text,
    because a half-corrected text must never be stored.  The pieces after a failed one are not
    sent: their answers would be thrown away, and with an unreachable server each would wait
    out the timeout.  A piece whose explanation step failed only costs the explanations of that
    piece; the file is not failed and says so in the warning.
    """

    def __init__(self, inner: Corrector) -> None:
        self._inner = inner

    @property
    def inner(self) -> Corrector:
        """Return the corrector that handles one piece."""
        return self._inner

    def split(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> tuple[Chunk, ...] | None:
        """Return the pieces *raw_text* is proofread in, or ``None`` when it goes out whole.

        A text goes out whole when the wrapped corrector has no budget, or the text fits it.
        The one place that decides, so :meth:`correct` and a tool that counts the requests of a
        text cannot disagree.
        """
        max_chars = self._inner.max_text_chars(language, text_format=text_format)
        if max_chars is None or max_chars <= 0 or len(raw_text) <= max_chars:
            return None
        return tuple(split_for_budget(raw_text, max_chars, text_format))

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the proofread text, merging the reviews of all the pieces."""
        chunks = self.split(raw_text, language, text_format=text_format)
        if chunks is None:
            return self._inner.correct(raw_text, language, text_format=text_format)
        outcomes: list[CorrectionOutcome] = []
        for chunk in chunks:
            if not chunk.text.strip():
                # Whitespace-only pieces carry nothing to proofread; a request would be wasted.
                outcomes.append(CorrectionOutcome(text=chunk.text))
                continue
            outcome = self._inner.correct(chunk.text, language, text_format=text_format)
            if outcome.failed:
                return CorrectionOutcome(
                    text=raw_text,
                    review=None,
                    warning=outcome.warning or CHUNK_FAILURE,
                    failed=True,
                )
            outcomes.append(outcome)
        answerable = [
            outcome for outcome, chunk in zip(outcomes, chunks, strict=True) if chunk.text.strip()
        ]
        reviews = [outcome.review for outcome in answerable if outcome.review is not None]
        merged, lost = _merged_review(outcomes, chunks, raw_text, text_format)
        incomplete = lost or (bool(reviews) and len(reviews) < len(answerable))
        warning = join_warnings(
            *(outcome.warning for outcome in outcomes),
            MISSING_REVIEW_WARNING if incomplete else None,
        )
        return CorrectionOutcome(
            text=_join_text(outcomes, chunks),
            review=merged,
            warning=warning,
            left_out=sum(outcome.left_out for outcome in outcomes),
            fell_back=any(outcome.fell_back for outcome in outcomes),
        )


def proofreading_pieces(
    corrector: Corrector, text: str, language: str, *, text_format: TextFormat = "plain"
) -> tuple[int | None, int | None]:
    """Return how many requests proofreading *text* takes, and the characters one carries.

    ``(None, None)`` for a corrector that sends any length in one request (an online
    provider).  The count is the number of non-blank pieces, because
    :meth:`ChunkingCorrector.correct` sends no request for a whitespace-only piece, and 1 when
    the text goes out whole.  The one place that counts, so the GUI counter, the console line
    and the run cannot disagree.
    """
    if not isinstance(corrector, ChunkingCorrector):
        return None, None
    max_chars = corrector.inner.max_text_chars(language, text_format=text_format)
    if max_chars is None:
        return None, None
    chunks = corrector.split(text, language, text_format=text_format)
    if chunks is None:
        return 1, max_chars
    return max(1, sum(1 for chunk in chunks if chunk.text.strip())), max_chars


def join_warnings(*warnings: str | None) -> str | None:
    """Return the distinct warnings joined by a space, or ``None`` when there is none."""
    seen = tuple(dict.fromkeys(warning for warning in warnings if warning))
    return " ".join(seen) or None


def _join_text(outcomes: list[CorrectionOutcome], chunks: tuple[Chunk, ...]) -> str:
    """Return the corrected pieces joined with the separators of the source text."""
    return "".join(
        outcome.text + chunk.separator for outcome, chunk in zip(outcomes, chunks, strict=True)
    )


def _merged_review(
    outcomes: list[CorrectionOutcome],
    chunks: tuple[Chunk, ...],
    raw_text: str,
    text_format: TextFormat,
) -> tuple[CorrectionReview | None, bool]:
    """Return one review over the whole text, and whether a piece lost its review.

    A piece's review is anchored in the plain text of that piece, so it is found in the plain
    text of the whole from a forward cursor and shifted there.  A piece whose plain text is not
    found, or whose shifted review no longer quotes the words it points at, loses its review:
    the file keeps the explanations of the other pieces and says that some are missing.
    """
    whole = plain_text_of(raw_text, text_format)
    cursor = 0
    found: list[tuple[int, CorrectionReview]] = []
    lost = False
    for outcome, chunk in zip(outcomes, chunks, strict=True):
        if not chunk.text.strip():
            continue
        piece_plain = plain_text_of(chunk.text, text_format)
        start = whole.find(piece_plain, cursor)
        if start >= 0:
            # Every piece moves the cursor, a piece without a review too: a later piece with the
            # same words (a repeated paragraph) must not be found at the earlier one's place.
            cursor = start + len(piece_plain)
        if outcome.review is None:
            continue
        if start < 0:
            lost = True
            continue
        shifted = outcome.review.shifted(start)
        if not fits(shifted, whole):
            lost = True
            continue
        found.append((start, outcome.review))
    if not found:
        return None, lost
    return merge_reviews(found, whole), lost
