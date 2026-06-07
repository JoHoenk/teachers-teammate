"""Pure benchmark comparison metrics for OCR outputs.

These helpers contain no I/O and no framework dependencies — they take text in
and return plain value objects.  They are consumed by
:class:`~teachers_teammate.application.benchmark_service.BenchmarkApplicationService`
(never directly by the GUI, which only renders the resulting value objects).

The benchmark compares exactly two stored OCR runs and answers "how much do they
agree?" without a ground-truth reference (cross-consensus): a similarity score
plus per-run text statistics.
"""

from __future__ import annotations

from dataclasses import dataclass
import difflib
import re

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_compare(text: str) -> str:
    """Return *text* lowercased with runs of whitespace collapsed to single spaces.

    Normalisation keeps formatting noise (indentation, line wrapping, casing)
    from dominating the similarity score so the comparison reflects the actual
    recognised content.
    """
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def similarity(a: str, b: str) -> float:
    """Return a 0.0-1.0 similarity ratio between *a* and *b*.

    Uses :class:`difflib.SequenceMatcher` on the normalised texts (stdlib, no
    extra dependency).  Two empty texts are defined as identical (1.0).
    """
    na, nb = normalize_for_compare(a), normalize_for_compare(b)
    if not na and not nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


@dataclass(frozen=True)
class TextStats:
    """Basic size statistics for one OCR output."""

    chars: int
    words: int
    lines: int


def text_stats(text: str) -> TextStats:
    """Return character, word, and line counts for *text*.

    ``lines`` counts non-empty lines; ``words`` splits on any whitespace.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    return TextStats(
        chars=len(text),
        words=len(text.split()),
        lines=len(lines),
    )


@dataclass(frozen=True)
class PairComparison:
    """Result of comparing two OCR outputs."""

    similarity: float
    stats_a: TextStats
    stats_b: TextStats


def compare_pair(a: str, b: str) -> PairComparison:
    """Return the :class:`PairComparison` of OCR outputs *a* and *b*."""
    return PairComparison(
        similarity=similarity(a, b),
        stats_a=text_stats(a),
        stats_b=text_stats(b),
    )
