"""GLEU, the corpus score of a correction, from counts that can be summed over texts.

The app does not need GLEU, so it lives here and not in ``teachers_teammate/domain/``.  The
formula is the one of ``compute_gleu`` in Napoles' ``gec-ranking`` repository, which is what the
GEC shared tasks report: for every n-gram order up to four, the hypothesis' n-grams that the
reference has, minus the ones the source has and the reference removed, floored at zero; then the
geometric mean of the four precisions, with a brevity penalty against the reference.

The unit is the whole text, one item being one line of the official script, and the numerator is
floored per text exactly as the script floors it.  That is why :class:`GleuCounts` keeps the
floored numerator and not matches and penalties separately: summing the two parts would not
reproduce the script.  Counts add up, so a language's GLEU is computed from the summed counts of
its texts and not as a mean of per-text scores.

This is our own implementation of the published metric, not a copy of the authors' script (the
repository states no license, so no code of it is included).  If you use GLEU, cite:

* Courtney Napoles, Keisuke Sakaguchi, Matt Post and Joel Tetreault (2015): *Ground Truth for
  Grammatical Error Correction Metrics*.  Proceedings of ACL-IJCNLP 2015 (Short Papers), pages
  588-593.  https://aclanthology.org/P15-2097
* Courtney Napoles, Keisuke Sakaguchi, Matt Post and Joel Tetreault (2016): *GLEU Without
  Tuning*.  arXiv:1605.02592.  http://arxiv.org/abs/1605.02592 (the version without a tunable
  weight, which is the formula implemented here).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import math

from teachers_teammate.domain.correction_review import tokenize

ORDER = 4
"""The n-gram orders GLEU averages over, as the official script does."""

SOURCE_PREFIX = "gleu_src"
"""Key prefix of the source's counts; the reference length is the same as the model's."""


@dataclass(frozen=True)
class GleuCounts:
    """The counts of one text, or of many texts added up, that GLEU is computed from."""

    hyp_len: int
    """Tokens of the hypothesis."""

    ref_len: int
    """Tokens of the reference."""

    matched: tuple[int, ...]
    """Per order ``n = 1..4``: ``max(|H ∩ R| - |H ∩ (S - R)|, 0)``, the floored numerator."""

    possible: tuple[int, ...]
    """Per order ``n``: ``max(hyp_len + 1 - n, 0)``, the denominator."""


def tokens(text: str) -> list[str]:
    """Return the diff tokens of *text* without the whitespace ones.

    The app's own tokenizer, so a GLEU token is the token the diff and the edits count, and the
    text is the one without added marks.
    """
    return [token for token in tokenize(text) if token.strip()]


def _ngrams(words: Sequence[str], n: int) -> Counter[tuple[str, ...]]:
    """Return the n-grams of *words* with their counts."""
    return Counter(tuple(words[index : index + n]) for index in range(len(words) + 1 - n))


def _removed(source: Counter[tuple[str, ...]], reference: Counter[tuple[str, ...]]) -> Counter:
    """Return the source's n-grams that the reference does not have at all.

    The script's ``get_ngram_diff``: a key the reference has, however often, is not removed, so
    this is not a Counter subtraction.
    """
    return Counter({gram: count for gram, count in source.items() if gram not in reference})


def gleu_counts(source: str, hypothesis: str, reference: str) -> GleuCounts:
    """Return the GLEU counts of *hypothesis* against *reference*, both derived from *source*.

    Args:
        source: The text both corrections start from.
        hypothesis: What the writing model produced.
        reference: The reference correction of the same text.

    Returns:
        The counts of this one text, floored per order as the official script floors them.
    """
    words = tokens(hypothesis)
    reference_words = tokens(reference)
    source_words = tokens(source)
    matched = []
    possible = []
    for n in range(1, ORDER + 1):
        hypothesis_grams = _ngrams(words, n)
        reference_grams = _ngrams(reference_words, n)
        removed = _removed(_ngrams(source_words, n), reference_grams)
        hits = sum((hypothesis_grams & reference_grams).values())
        penalties = sum((hypothesis_grams & removed).values())
        matched.append(max(hits - penalties, 0))
        possible.append(max(len(words) + 1 - n, 0))
    return GleuCounts(
        hyp_len=len(words),
        ref_len=len(reference_words),
        matched=tuple(matched),
        possible=tuple(possible),
    )


def add(counts: Iterable[GleuCounts]) -> GleuCounts:
    """Return the counts of all *counts* added up, as one text would have them."""
    total = GleuCounts(0, 0, (0,) * ORDER, (0,) * ORDER)
    for item in counts:
        total = GleuCounts(
            hyp_len=total.hyp_len + item.hyp_len,
            ref_len=total.ref_len + item.ref_len,
            matched=tuple(
                one + other for one, other in zip(total.matched, item.matched, strict=True)
            ),
            possible=tuple(
                one + other for one, other in zip(total.possible, item.possible, strict=True)
            ),
        )
    return total


def gleu(counts: GleuCounts) -> float | None:
    """Return the GLEU of *counts*, or ``None`` when nothing was counted.

    ``None`` is what a text with an empty hypothesis gets, so a group without a scored text is
    left out of a mean instead of counting as 0.00.  A zero anywhere else is a real 0.0: the
    script has no smoothing.
    """
    if counts.hyp_len == 0:
        return None
    if counts.ref_len == 0 or any(value == 0 for value in (*counts.matched, *counts.possible)):
        return 0.0
    log_precision = (
        sum(
            math.log(matched / possible)
            for matched, possible in zip(counts.matched, counts.possible, strict=True)
        )
        / ORDER
    )
    return math.exp(min(0.0, 1.0 - counts.ref_len / counts.hyp_len) + log_precision)


def to_scores(
    counts: GleuCounts, prefix: str = "gleu", *, reference_length: bool = True
) -> dict[str, int]:
    """Return *counts* as the integer keys a run stores.

    Args:
        counts: The counts of one text.
        prefix: ``"gleu"`` for the model's counts, :data:`SOURCE_PREFIX` for the source's.
        reference_length: Whether to write ``{prefix}_r``.  The source's counts share the
            reference length with the model's, so they leave it out.

    Returns:
        ``{prefix}_c``, ``{prefix}_r``, ``{prefix}_m1`` … ``{prefix}_m4``, ``{prefix}_p1`` …
        ``{prefix}_p4``, all integers.
    """
    scores = {f"{prefix}_c": counts.hyp_len}
    if reference_length:
        scores[f"{prefix}_r"] = counts.ref_len
    for n, (matched, possible) in enumerate(
        zip(counts.matched, counts.possible, strict=True), start=1
    ):
        scores[f"{prefix}_m{n}"] = matched
        scores[f"{prefix}_p{n}"] = possible
    return scores


def from_scores(
    scores: Mapping[str, int | float], prefix: str = "gleu", *, ref_len: int | None = None
) -> GleuCounts:
    """Return the counts that :func:`to_scores` wrote into *scores* under *prefix*.

    Args:
        scores: The stored numbers of one item.
        prefix: ``"gleu"`` for the model's counts, :data:`SOURCE_PREFIX` for the source's.
        ref_len: The reference length to use when *scores* has no ``{prefix}_r``, which is how
            the source's counts are read back.

    Raises:
        KeyError: A key of the counts is missing, which is what validation reports as a problem.
    """
    length = scores.get(f"{prefix}_r", ref_len)
    if length is None:
        raise KeyError(f"{prefix}_r")
    return GleuCounts(
        hyp_len=int(scores[f"{prefix}_c"]),
        ref_len=int(length),
        matched=tuple(int(scores[f"{prefix}_m{n}"]) for n in range(1, ORDER + 1)),
        possible=tuple(int(scores[f"{prefix}_p{n}"]) for n in range(1, ORDER + 1)),
    )
