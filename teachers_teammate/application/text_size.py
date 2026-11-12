"""How long a document is, and how many proofreading requests it takes.

Shown quietly in the GUI once OCR is done: a text that goes out in several pieces cannot be
changed across a piece boundary, so the teacher should know before proofreading starts.  The
count comes from the code the run uses (:meth:`~.correction.ChunkingCorrector.split`), so the
estimate and the run cannot disagree.

It is an estimate, and the wording says "about": the text really sent is anonymized when
personal-info removal is on, and placeholders change its length slightly; the content review
reads the *proofread* text, not the OCR text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..domain.formatted_text import TextFormat
    from ..interfaces import Corrector


@dataclass(frozen=True)
class TextSizeEstimate:
    """What one document costs: its length, and the requests it takes."""

    words: int
    """Words of the text without markup (``TextFormatter.plain_text``)."""
    pieces: int | None
    """Proofreading requests the text takes; ``None`` when proofreading is off or online."""
    words_per_piece: int | None
    """Words one proofreading request can carry, for the tooltip; ``None`` when unlimited."""
    content_review_fits: bool | None
    """Whether the whole text fits one content review; ``None`` when it is off or online."""


def proofreading_pieces(
    corrector: Corrector, raw_text: str, language: str, *, text_format: TextFormat = "plain"
) -> tuple[int | None, int | None]:
    """Return how many requests proofreading *raw_text* takes, and the words one carries.

    ``(None, None)`` when the corrector has no request limit (an online provider): it handles
    any length in one request.  The count comes from
    :func:`~teachers_teammate.infrastructure.correction.proofreading_pieces`, the same place
    the run and the console line ask; only the words are worked out here.
    """
    from ..infrastructure.correction import proofreading_pieces as count_pieces  # noqa: PLC0415
    from .formatting import TextFormatter  # noqa: PLC0415

    pieces, max_chars = count_pieces(corrector, raw_text, language, text_format=text_format)
    if pieces is None or max_chars is None:
        return None, None
    plain = TextFormatter.plain_text(raw_text, text_format)
    words = len(plain.split())
    words_per_piece = max(1, round(words * max_chars / len(plain))) if plain else 0
    return pieces, words_per_piece
