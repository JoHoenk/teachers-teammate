"""What a corpus adapter is: the facts a manifest needs, and how a pointer becomes a text.

One module per corpus sits next to this one, and :mod:`tools.evaluation.input.corpora` finds them at run
time.  An adapter knows its corpus and nothing else: its licence, whether it is restricted, the
languages and splits it has, the files it reads, and how one of its pointers becomes a text.  Which
texts a test set chooses is a *recipe* (:mod:`tools.evaluation.input.recipes`), not a corpus: one corpus
serves several sets, and one set draws on several corpora.

An adapter reads only the files it pins (:meth:`CorpusAdapter.files`).  A manifest records their
hashes, so a file the adapter read but did not pin would make that check meaningless.

Pointers are dicts in the corpus's own terms; each adapter's docstring lists its own.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from ...core.labels import ReferenceLabel
from ...metrics.offsets import char_spans_to_tokens
from ...metrics.quality import Span, untouched_spans

T = TypeVar("T")


@dataclass(frozen=True)
class ResolvedItem:
    """One item of a test set, built from the corpora by way of its pointer."""

    id: str
    language: str
    kind: str
    """``"errors"`` (a text with a reference correction) or ``"correct"`` (sentences already
    right, which must stay untouched)."""

    corpus: str
    """The corpus the text comes from, as the adapter's ``NAME`` names it."""

    restricted: bool
    """Whether the corpus's licence forbids storing any of its words.

    Required, not defaulted: a forgotten flag would let a restricted corpus's words into the
    repository.
    """

    origin: str
    """Where the text comes from, in the builder's words; kept so a licence question can be
    answered later."""

    source: str
    reference: str
    sentences: tuple[str, ...] = ()
    """The already-correct sentences of a ``"correct"`` item, in order."""

    labels: tuple[ReferenceLabel, ...] = ()
    """The corpus label of every change the reference makes; empty when the corpus has none."""

    sentence_spans: tuple[Span, ...] = ()
    """The **character** span of every sentence of the corpus's own segmentation, in text order.

    Defaulted, unlike ``restricted``: a missing segmentation leaks nothing, it only means the
    corpus has none (the invented French texts) or its M2 sentences did not fit.
    """

    unchanged_sentences: tuple[int, ...] = ()
    """Indices into :attr:`sentence_spans` of the sentences the reference leaves alone.

    A sentence counts when the corpus says it is unchanged *and* the word diff of source and
    reference does not touch it (:func:`~tools.evaluation.metrics.quality.
    untouched_spans`).
    """

    level: str = ""
    """The CEFR level the corpus gives the text (``B1``), or ``""`` when it gives none.

    W&I names the level of every essay; Falko-MERLIN's files carry none.  A test set's recipe
    decides whether the level goes into the manifest.
    """


def pointers(item: Mapping[str, Any]) -> list[Any]:
    """Return the pointers of *item* as a list, whether the manifest holds one or several.

    Raises:
        ValueError: if there is no pointer at all, or one is not an object.
    """
    pointer: Any = item["pointer"]
    found = list(pointer) if isinstance(pointer, list) else [pointer]
    if not found:
        raise ValueError(f"{item.get('id')}: the item has no pointer")
    if not all(isinstance(one, dict) for one in found):
        raise ValueError(f"{item.get('id')}: a pointer is not an object")
    return found


class CorpusAdapter(ABC):
    """One corpus: what it may be used for, which files it is, how a pointer becomes a text."""

    NAME: ClassVar[str]
    """The name manifests and items use: ``"falko-merlin"``."""

    LICENCE: ClassVar[str]
    """Copied word for word into every manifest that uses the corpus."""

    RESTRICTED: ClassVar[bool]
    """The licence forbids passing on any part: no word of it is committed."""

    LANGUAGES: ClassVar[tuple[str, ...]]
    """The codes of :data:`~.languages.LANGUAGE_NAMES` the corpus has texts in."""

    HINT: ClassVar[str]
    """What ``--corpus NAME=PATH`` expects, for refusals and ``--help``."""

    SPLITS: ClassVar[tuple[str, ...]] = ()
    """The splits of the corpus, the default first; ``()`` for a corpus without splits."""

    DEFAULT_ROOT: ClassVar[Path | None] = None
    """Where the corpus sits when it is in the repository; ``None`` for one that is not."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._readings: dict[tuple[str, str], Any] = {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self.root)!r})"

    def split_name(self, split: str | None) -> str:
        """Return *split*, or the default split of the corpus (``""`` when it has none).

        Raises:
            ValueError: if the corpus has no such split, or has no splits at all and a pointer
                names one.
        """
        if not self.SPLITS:
            if split is not None:
                raise ValueError(f"{self.NAME} has no splits, so a pointer cannot name one")
            return ""
        if split is None:
            return self.SPLITS[0]
        if split not in self.SPLITS:
            raise ValueError(f"{self.NAME} has no split {split!r}; known: {', '.join(self.SPLITS)}")
        return split

    def splits_of(self, item: Mapping[str, Any]) -> frozenset[str]:
        """Return the splits the pointers of *item* name, the default one when a pointer names none.

        Raises:
            ValueError: for an unknown split, or a ``split`` key on a corpus without splits.
        """
        found = {self.split_name(pointer.get("split")) for pointer in pointers(item)}
        return frozenset(split for split in found if split)

    def files(self, splits: Collection[str] | None = None) -> dict[str, Path]:
        """Every file the adapter reads, keyed by the name the manifest records.

        *splits* narrows the result to those splits; ``None`` returns every split.  A corpus
        without splits has one set of files, whatever is asked for.  The keys are the corpus's own
        names (``json/A.dev.json``), so a reader can tell which file a hash belongs to without
        knowing where the corpora sit on the builder's disk.

        Raises:
            ValueError: for a split the corpus does not have.
        """
        if not self.SPLITS:
            return self._files_of("")
        if splits is None:
            chosen = list(self.SPLITS)
        else:
            chosen = [self.split_name(split) for split in splits]
            chosen.sort(key=self.SPLITS.index)
        files: dict[str, Path] = {}
        for split in chosen:
            files.update(self._files_of(split))
        return files

    @abstractmethod
    def _files_of(self, split: str) -> dict[str, Path]:
        """Return the files of one split, in the order the manifest records them."""

    @abstractmethod
    def resolve(self, item: Mapping[str, Any]) -> ResolvedItem:
        """Build *item* from its pointer.

        Raises:
            ValueError: if the pointer does not fit the corpus.
        """

    def make(  # noqa: PLR0913  # one keyword per fact of a resolved item an adapter may fill
        self,
        item: Mapping[str, Any],
        *,
        origin: str,
        source: str,
        reference: str,
        sentences: Collection[str] = (),
        labels: Collection[ReferenceLabel] = (),
        sentence_chars: Collection[Span] = (),
        corpus_unchanged: Collection[bool] = (),
        level: str = "",
    ) -> ResolvedItem:
        """Fill id, language, kind, corpus and restricted from *item* and the class.

        *sentence_chars* are the character spans of the corpus's own sentences, and
        *corpus_unchanged* says, per sentence, whether the corpus itself calls it unchanged.  Both
        are filled into :attr:`ResolvedItem.sentence_spans` and
        :attr:`ResolvedItem.unchanged_sentences` for an ``"errors"`` item; a text whose spans do
        not fit the tokenisation gets neither, with one line saying so.

        Raises:
            ValueError: if the two collections differ in length, or the spans are not ordered and
                disjoint.
        """
        spans, unchanged = self._sentence_spans(source, reference, sentence_chars, corpus_unchanged)
        return ResolvedItem(
            id=item["id"],
            language=item["language"],
            kind=item["kind"],
            corpus=item["corpus"],
            restricted=self.RESTRICTED,
            origin=origin,
            source=source,
            reference=reference,
            sentences=tuple(sentences),
            labels=tuple(labels),
            sentence_spans=spans,
            unchanged_sentences=unchanged,
            level=level,
        )

    @staticmethod
    def _sentence_spans(
        source: str,
        reference: str,
        sentence_chars: Collection[Span],
        corpus_unchanged: Collection[bool],
    ) -> tuple[tuple[Span, ...], tuple[int, ...]]:
        """Return the character spans of the sentences and the indices of the unchanged ones.

        The spans are kept in characters, as the test record stores them; the word diff that
        decides which sentences the reference leaves alone counts tokens, so the spans are mapped
        for that one question and not stored.

        Raises:
            ValueError: if the two collections differ in length, or the spans are not ordered and
                disjoint.
        """
        if len(sentence_chars) != len(corpus_unchanged):
            raise ValueError(
                f"{len(sentence_chars)} sentence spans, {len(corpus_unchanged)} unchanged flags"
            )
        if not sentence_chars:
            return (), ()
        spans = tuple(sentence_chars)
        previous = 0
        for start, end in spans:
            if start < previous or end < start:
                raise ValueError(f"the sentence spans are not ordered and disjoint: {spans}")
            previous = end
        try:
            tokens = char_spans_to_tokens(source, spans)
        except ValueError as error:
            print(f"no sentence spans for a text starting {source[:40]!r}: {error}")
            return (), ()
        untouched = untouched_spans(source, reference, tokens)
        return spans, tuple(
            index
            for index, (kept, alone) in enumerate(zip(corpus_unchanged, untouched, strict=True))
            if kept and alone
        )

    def _reading(self, name: str, split: str, build: Callable[[], T]) -> T:
        """Return the reading *name* of *split*, built once per split.

        A dict on the instance, not ``functools.cache`` on a method: that would keep the adapter
        alive for as long as the process runs.
        """
        key = (name, split)
        if key not in self._readings:
            self._readings[key] = build()
        return self._readings[key]


__all__ = ["CorpusAdapter", "ResolvedItem", "char_spans_to_tokens", "pointers"]
