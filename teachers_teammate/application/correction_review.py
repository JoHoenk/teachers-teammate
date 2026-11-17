"""Adapter-facing view of a correction review.

The domain :func:`~teachers_teammate.domain.correction_review.align` needs plain text: the
diff that :class:`~teachers_teammate.application.formatting.TextFormatter` produces.  This
module is the only entry point the GUI uses; the DOCX builder (infrastructure) calls the
domain function directly with the plain text it already has.

:meth:`CorrectionReviewPresenter.for_preview` is the one seam between the stored review and the
old Preview, which still reads the sentence-based one.  It goes with the Preview.
"""

from __future__ import annotations

from functools import lru_cache

from ..domain.correction_review import (
    AlignedReview,
    CorrectionReview,
    align,
)
from ..domain.formatted_text import normalize_text_format
from ..domain.proofreading.legacy_review import sentence_review
from ..domain.proofreading.review import CorrectionReview as StoredReview
from .formatting import TextFormatter

_CACHE_SIZE = 64


@lru_cache(maxsize=_CACHE_SIZE)
def _aligned(raw_text: str, corrected_text: str, review: CorrectionReview) -> AlignedReview:
    return align(raw_text, corrected_text, review)


class CorrectionReviewPresenter:
    """Aligns a stored review with the current text of a document."""

    @staticmethod
    def for_preview(
        raw_text: str, text_format: str | None, review: StoredReview | None
    ) -> CorrectionReview | None:
        """Return the stored review as the sentence-based one the old Preview shows.

        The Preview, its views and its tab title still read the sentence-based review; this is
        the one place that converts, and it goes with the Preview.  *raw_text* is the stored
        text, including Markdown marks: the conversion works on its plain text.
        """
        if review is None:
            return None
        fmt = normalize_text_format(text_format)
        return sentence_review(TextFormatter.plain_text(raw_text, fmt), review)

    @staticmethod
    def aligned(
        raw_text: str,
        corrected_text: str,
        text_format: str | None,
        review: CorrectionReview | None,
    ) -> AlignedReview | None:
        """Return the aligned review, or ``None`` when the document has no review.

        *raw_text* and *corrected_text* are the stored texts, including Markdown marks; they are
        converted to plain text here, because alignment works on words, not on notation.
        """
        if review is None:
            return None
        fmt = normalize_text_format(text_format)
        return _aligned(
            TextFormatter.plain_text(raw_text, fmt),
            TextFormatter.plain_text(corrected_text, fmt),
            review,
        )

    @staticmethod
    def aligned_plain(
        raw_plain: str, corrected_plain: str, review: CorrectionReview | None
    ) -> AlignedReview | None:
        """Return the aligned review for texts that are already plain (no markup left).

        For a caller that has converted the texts itself, so they are not parsed twice.
        """
        if review is None:
            return None
        return _aligned(raw_plain, corrected_plain, review)
