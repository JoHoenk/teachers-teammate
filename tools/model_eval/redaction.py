"""The caps that keep corpus text out of the repository, as pure functions.

The evaluation runs are committed, the corpora are not: every field an exported run holds must
survive the rule "counts and scores go in, texts stay out".  This module decides,
for one field at a time, whether it may be stored as text or has to be replaced by a hash.

A **word** is a word or a mark of the app's tokeniser (:func:`tokenize`); whitespace is not
counted.  The caps:

* the replacement of a model edit keeps its text only while it has at most
  :data:`MAX_REPLACEMENT_WORDS` words **and** copies no :data:`WORD_RUN` consecutive words of the
  item's source or reference; otherwise it becomes ``{"h": sha256, "n": words}``;
* an explanation that copies :data:`WORD_RUN` consecutive source or reference words is withheld
  (stored as :data:`ELLIPSIS`); one over :data:`MAX_EXPLANATION_CHARS` characters is shortened;
* everything of an item of a ``restricted`` corpus stores no words at all: replacements as hashes,
  explanations as ``""`` with their hash kept.

The comparison is lenient in the way a reader would be: marks are dropped and case is folded, so
``Die ,`` and ``die,`` count as the same words.  Punctuation and other marks are words of their own
(a comma a model copied is a copied word), which makes the rule stricter than a word-only count
and therefore safe.

Nothing here reads a corpus or a file: the caller (:mod:`.store`) builds the forbidden runs from
the texts it has and asks :class:`Redactor` about each field.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Set
from dataclasses import dataclass
import hashlib
import hmac

from teachers_teammate.domain.correction_review import strip_marks, tokenize

WORD_RUN = 5
"""How many consecutive words a field may not copy from the source or reference of its item."""

MAX_REPLACEMENT_WORDS = 8
"""Longest replacement of a model edit that is still stored as text."""

MAX_EXPLANATION_CHARS = 600
"""Longest explanation that is stored in full, counted in characters, not words."""

ELLIPSIS = "…"
"""What an explanation that had to be cut is stored as."""


Run = tuple[str, ...]
"""A stretch of consecutive words, normalised (marks dropped, case folded)."""


def sha256_text(text: str) -> str:
    """Return the SHA-256 of *text* as UTF-8, which is how every hash in a run file is made."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def keyed_sha256(key: bytes, text: str) -> str:
    """Return the HMAC-SHA256 of *text* under *key*, as hex.

    A plain hash of a word or two can be reversed by trying a dictionary, which would hand a
    restricted corpus's words to anyone who reads the repository.  A keyed hash can only be
    checked by someone who holds the key, and the key of a restricted corpus is made from the
    corpus itself (:meth:`~.corpora._base.CorpusAdapter.redaction_key`).
    """
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def words(text: str) -> tuple[str, ...]:
    """Return the words and marks of *text* as the app's tokeniser sees them, without whitespace."""
    return tuple(token for token in tokenize(text) if token.strip())


def word_count(text: str) -> int:
    """Return how many words and marks *text* has (whitespace not counted)."""
    return len(words(text))


def normalize_word(token: str) -> str:
    """Return *token*, case folded, for comparing two runs."""
    return token.casefold()


def compared_words(text: str) -> tuple[str, ...]:
    """Return the words of *text* as the lenient comparison sees them: marks removed, without space.

    The marks are removed from the whole text before it is split, because the app's tokeniser makes
    two tokens of ``**`` and one of ``<sup>``, so stripping each token alone would leave them in.
    """
    return tuple(token for token in tokenize(strip_marks(text)) if token.strip())


def word_runs(text: str, size: int = WORD_RUN) -> tuple[Run, ...]:
    """Return every stretch of *size* consecutive words of *text*, in text order.

    Markdown marks are ignored and case is folded, so two texts that a reader would call the same
    produce the same runs.  A text shorter than *size* words has none.
    """
    normalized = [normalize_word(token) for token in compared_words(text)]
    if size <= 0 or len(normalized) < size:
        return ()
    return tuple(
        tuple(normalized[start : start + size]) for start in range(len(normalized) - size + 1)
    )


def forbidden_runs(*texts: str, size: int = WORD_RUN) -> frozenset[Run]:
    """Return the runs of *texts* a stored field may not repeat: the item's source and reference."""
    return frozenset(run for text in texts for run in word_runs(text, size))


def quotes_corpus(text: str, forbidden: Set[Run], *, size: int = WORD_RUN) -> bool:
    """Whether *text* repeats a forbidden run, i.e. copies *size* consecutive corpus words."""
    return any(run in forbidden for run in word_runs(text, size))


def fits_replacement(
    text: str,
    forbidden: Set[Run],
    *,
    restricted: bool = False,
    size: int = WORD_RUN,
) -> bool:
    """Whether the replacement of a model edit may be stored as text, word for word."""
    if restricted:
        return False
    return word_count(text) <= MAX_REPLACEMENT_WORDS and not quotes_corpus(
        text, forbidden, size=size
    )


def replacement_field(
    text: str,
    forbidden: Set[Run],
    *,
    restricted: bool = False,
    size: int = WORD_RUN,
    digest: Callable[[str], str] = sha256_text,
) -> dict:
    """Return how the replacement of a model edit is stored: as text, or as hash and length.

    Args:
        text: The words the model put in place of the source words (may be empty: a deletion).
        forbidden: The runs of the item's source and reference (see :func:`forbidden_runs`).
        restricted: Whether the item belongs to a restricted corpus, whose items store no words.
        size: Length of the forbidden runs, for a caller that wants a stricter rule.
        digest: The hash of a replacement that is not stored; keyed for a restricted item.

    Returns:
        ``{"r": text}`` while :func:`fits_replacement` allows it, else ``{"h": hash, "n": words}``.
    """
    if fits_replacement(text, forbidden, restricted=restricted, size=size):
        return {"r": text}
    return {"h": digest(text), "n": word_count(text)}


def explanation_field(
    text: str,
    forbidden: Set[Run],
    *,
    restricted: bool = False,
    size: int = WORD_RUN,
    digest: Callable[[str], str] = sha256_text,
) -> dict:
    """Return how an explanation is stored: its text, a cut mark, or a hash only.

    An explanation that copies :data:`WORD_RUN` consecutive words of the item's source or
    reference quotes the corpus and is stored as :data:`ELLIPSIS`; one of a restricted item is
    stored as ``""`` with its hash, because even a short explanation of such an item would tell
    a reader something about text the licence forbids passing on.  A long explanation is
    shortened to :data:`MAX_EXPLANATION_CHARS` characters, which keeps a model's rambling out of
    the repository.

    Args:
        text: The explanation the model gave (``""`` when it gave none).
        forbidden: The runs of the item's source and reference (see :func:`forbidden_runs`).
        restricted: Whether the item belongs to a restricted corpus, whose items store no words.
        size: Length of the forbidden runs.
        digest: The hash kept of a withheld explanation; keyed for a restricted item.

    Returns:
        ``{"explanation": text, "explanation_sha256": None}``, or with the cut form and the hash
        of the original text when the field had to be cut.
    """
    if restricted:
        return {"explanation": "", "explanation_sha256": digest(text)}
    shortened = shorten(text)
    if quotes_corpus(shortened, forbidden, size=size):
        return {"explanation": ELLIPSIS, "explanation_sha256": digest(text)}
    if shortened != text:
        return {"explanation": shortened, "explanation_sha256": digest(text)}
    return {"explanation": text, "explanation_sha256": None}


def shorten(text: str) -> str:
    """Return *text*, cut to :data:`MAX_EXPLANATION_CHARS` characters and marked as cut."""
    if len(text) <= MAX_EXPLANATION_CHARS:
        return text
    return text[: MAX_EXPLANATION_CHARS - len(ELLIPSIS)].rstrip() + ELLIPSIS


def field_is_cut(field: dict) -> bool:
    """Whether a stored field was cut: a replacement without ``r``, an explanation with a hash."""
    if "r" in field:
        return False
    if "explanation" in field:
        return field.get("explanation_sha256") is not None
    return True


@dataclass(frozen=True)
class Redactor:
    """One item's caps: its forbidden runs and whether the item stores words at all.

    An item's source and reference are the corpus text it may not repeat, so they are read once
    and asked about every field of the item.
    """

    forbidden: frozenset[Run]
    """The runs a stored field of this item may not repeat."""

    restricted: bool = False
    """Whether the item's corpus forbids passing on any part, so the item stores no words."""

    key: bytes = b""
    """The key of a restricted corpus's hashes (:func:`keyed_sha256`); required when restricted."""

    @classmethod
    def for_item(cls, *texts: str, restricted: bool = False, key: bytes = b"") -> Redactor:
        """Return the caps of an item whose source and reference are *texts*.

        Raises:
            ValueError: if the item is restricted and no *key* is given, because a plain hash of
                a word or two of a restricted corpus could be reversed with a dictionary.
        """
        if restricted and not key:
            raise ValueError("an item of a restricted corpus needs the corpus key to hash a field")
        return cls(forbidden_runs(*texts), restricted, key)

    def _digest(self, text: str) -> str:
        """Return the hash a field of this item keeps: keyed for a restricted item."""
        return keyed_sha256(self.key, text) if self.restricted else sha256_text(text)

    def replacement(self, text: str) -> dict:
        """Return the stored form of one model replacement (see :func:`replacement_field`)."""
        return replacement_field(
            text, self.forbidden, restricted=self.restricted, digest=self._digest
        )

    def explanation(self, text: str) -> dict:
        """Return the stored form of one explanation (see :func:`explanation_field`)."""
        return explanation_field(
            text, self.forbidden, restricted=self.restricted, digest=self._digest
        )

    def accepts(self, field: dict) -> bool:
        """Whether a stored replacement or explanation keeps to this item's caps (strict check)."""
        if "r" in field and not fits_replacement(
            field["r"], self.forbidden, restricted=self.restricted
        ):
            return False
        if "explanation" in field:
            return self.accepts_explanation(field["explanation"])
        return True

    def accepts_explanation(self, explanation: str) -> bool:
        """Whether a stored explanation text keeps to this item's caps."""
        if len(explanation) > MAX_EXPLANATION_CHARS:
            return False
        if explanation in {"", ELLIPSIS}:
            return not self.restricted or explanation == ""
        if self.restricted:
            return False
        return not quotes_corpus(explanation, self.forbidden)


def cut_fields(fields: Iterable[dict]) -> int:
    """Return how many of *fields* had to be cut, for the count a run header records."""
    return sum(1 for field in fields if field_is_cut(field))
