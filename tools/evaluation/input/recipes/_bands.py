"""The machinery two recipes share: bands, slots, filters, the draw and the items.

``gec-2026-12`` and its held-out twin pick texts the same way, and differ only in the splits they
read, the slots, the seed, the numbers and what an item records.  The *procedure* lives here once;
a recipe module holds its own :class:`Rules` (bands, character limits, filters), slots, seed and
docstring and calls :func:`select_texts`.  Nothing here has a default number: a recipe never
changes once its manifest is committed, so a change made for one recipe must not be able to move
what another one selects.  The module name starts with ``_``, so the recipe finder does not take
it for a recipe.

A recipe that reads the corpora's default splits passes no split, and its pointers carry none;
one that reads another passes the split's name, and every pointer names it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import contextlib
from dataclasses import dataclass
import io
import random
from typing import cast

from teachers_teammate.domain.proofreading.edit_agreement import ordered_edits

from ...core.constants import TextKind
from .. import testset
from ..corpora.falko_merlin import FalkoMerlin
from ..corpora.wi import WriteAndImprove

_words = testset.words


@dataclass(frozen=True)
class Rules:
    """The numbers one recipe selects by.  A recipe writes its own and never shares them."""

    bands: Mapping[str, tuple[int, int]]
    """The bands in whitespace words, and the order the ids run in."""

    split_chars: int
    """A slot that wants ``"split"`` takes texts of at least this many characters."""

    one_request_chars: int
    """A slot that wants ``"whole"`` takes texts of at most this many characters."""

    min_edits: int
    """Fewer reference changes than this and a text measures nothing."""

    max_edits_per_100_words: int
    """More changes than this per 100 words and the text is a rewrite, not a correction."""

    def band_of(self, words: int) -> str | None:
        """Return the band *words* falls in, or ``None`` when it falls in none."""
        for band, (low, high) in self.bands.items():
            if low <= words <= high:
                return band
        return None


@dataclass(frozen=True)
class Slot:
    """One draw of the selection: how many texts of which band, from which origins."""

    language: str
    band: str
    source: tuple[str, ...]
    """The MERLIN / Falko origins, or the W&I levels, a text may come from."""

    count: int
    chars: str = "any"
    """``"any"``, ``"split"`` (at least :attr:`Rules.split_chars`) or ``"whole"`` (at most
    :attr:`Rules.one_request_chars`)."""

    @property
    def name(self) -> str:
        """Return the slot as the refusal names it: ``de page falko any``."""
        return f"{self.language} {self.band} {'+'.join(self.source)} {self.chars}"


@dataclass(frozen=True)
class Candidate:
    """One text of a corpus that could be chosen, with what the filters look at."""

    key: str
    """The text's own id: ``falko:sa009`` for German, the essay id for English."""

    language: str
    source: str
    """The origin (``falko``, ``merlin``) or the W&I level (``A``, ``B``, ``C``)."""

    pointer: dict
    band: str
    words: int
    chars: int
    edits: int
    level: str = ""
    """The CEFR level the corpus gives the text (``B1``), or ``""`` when it gives none."""


def select_texts(
    corpora: testset.Corpora,
    *,
    testset_id: str,
    rules: Rules,
    slots: Sequence[Slot],
    seed: int,
    german_split: str | None = None,
    english_split: str | None = None,
    with_level: bool = False,
) -> list[dict]:
    """Return the German and English items of a test set, in the order the records are written.

    Raises:
        ValueError: if a slot has fewer candidates than it needs; the message names the slot.
    """
    candidates = _candidates(corpora, rules, german_split, english_split)
    chosen = _draw(candidates, rules, slots, seed, testset_id)
    return _items(chosen, rules, with_level)


# ── the candidates ───────────────────────────────────────────────────────────


def _candidates(
    corpora: testset.Corpora, rules: Rules, german_split: str | None, english_split: str | None
) -> list[Candidate]:
    """Return every text of the chosen splits that fits a band and the filters."""
    found = _german_candidates(corpora, rules, german_split) + _english_candidates(
        corpora, rules, english_split
    )
    return sorted(found, key=lambda candidate: candidate.key)


def _with_split(pointer: dict, split: str | None) -> dict:
    """Return *pointer* naming *split*, or unchanged for the default split."""
    return pointer if split is None else {**pointer, "split": split}


def _german_candidates(
    corpora: testset.Corpora, rules: Rules, split: str | None
) -> list[Candidate]:
    """Return the German texts of one Falko-MERLIN split that could be chosen."""
    german = cast(FalkoMerlin, corpora.adapter("falko-merlin"))
    pointers = [
        (
            key,
            {
                "id": f"de-{key}",
                "language": "de",
                "kind": TextKind.ERRORS,
                "corpus": "falko-merlin",
                "pointer": _with_split(
                    {
                        "origin": origin,
                        "text": key.split(":", 1)[1],
                        "sentences": [0, len(pairs)],
                    },
                    split,
                ),
            },
        )
        for origin, by_id in german.texts(split).items()
        for key, pairs in by_id.items()
    ]
    return _scan(pointers, corpora, rules, source_of=lambda item: item["pointer"]["origin"])


def _english_candidates(
    corpora: testset.Corpora, rules: Rules, split: str | None
) -> list[Candidate]:
    """Return the English essays of one W&I split that could be chosen.

    An essay's source is its ``text`` as the corpus gives it, so the band is read from there and an
    essay that fits none is not resolved at all: W&I ``train`` holds thousands, and resolving one
    (spans, labels, the edit alignment) is the expensive part.
    """
    english = cast(WriteAndImprove, corpora.adapter("wi"))
    pointers = [
        (
            row["id"],
            {
                "id": f"en-{row['id']}",
                "language": "en",
                "kind": TextKind.ERRORS,
                "corpus": "wi",
                "pointer": _with_split({"level": level, "document": row["id"]}, split),
            },
        )
        for level, rows in english.rows(split).items()
        for row in rows
        if row["edits"] and rules.band_of(_words(row["text"])) is not None
    ]
    return _scan(pointers, corpora, rules, source_of=lambda item: item["pointer"]["level"])


def _scan(
    pointers: Iterable[tuple[str, dict]], corpora: testset.Corpora, rules: Rules, *, source_of
) -> list[Candidate]:
    """Resolve every pointer and keep the texts that fit a band and the filters.

    The resolver's "no labels" lines are swallowed: a text that is dropped is not a problem.
    """
    found: list[Candidate] = []
    with contextlib.redirect_stdout(io.StringIO()):
        for key, item in pointers:
            try:
                resolved = testset.resolve(item, corpora)
            except testset.RESOLVE_ERRORS:
                continue
            candidate = _candidate(key, item, resolved, source_of(item), rules)
            if candidate is not None:
                found.append(candidate)
    return found


def _candidate(key: str, item: dict, resolved, source: str, rules: Rules) -> Candidate | None:
    """Return the candidate of a resolved text, or ``None`` when it fails a filter."""
    words = _words(resolved.source)
    band = rules.band_of(words)
    if band is None or not resolved.sentence_spans or not resolved.labels:
        return None
    edits = len(ordered_edits(resolved.source, resolved.reference))
    if edits < rules.min_edits or edits * 100 > rules.max_edits_per_100_words * words:
        return None
    return Candidate(
        key=key,
        language=resolved.language,
        source=source,
        pointer=item["pointer"],
        band=band,
        words=words,
        chars=len(resolved.source),
        edits=edits,
        level=resolved.level,
    )


# ── the draw ─────────────────────────────────────────────────────────────────


def _draw(
    candidates: Sequence[Candidate],
    rules: Rules,
    slots: Sequence[Slot],
    seed: int,
    testset_id: str,
) -> list[Candidate]:
    """Return the chosen texts, in the order the ids run.

    Raises:
        ValueError: if a slot has fewer candidates than it needs; the message names the slot.
    """
    chosen: list[Candidate] = []
    for slot in slots:
        pool = sorted(
            (
                candidate
                for candidate in candidates
                if candidate.language == slot.language
                and candidate.band == slot.band
                and candidate.source in slot.source
                and _fits_chars(candidate, slot.chars, rules)
                and candidate not in chosen
            ),
            key=lambda candidate: candidate.key,
        )
        if len(pool) < slot.count:
            raise ValueError(f"{testset_id}: {slot.name}: {len(pool)} candidates for {slot.count}")
        rng = random.Random(
            f"{seed}/{slot.language}/{slot.band}/{'+'.join(slot.source)}/{slot.chars}"
        )
        chosen.extend(rng.sample(pool, slot.count))
    return chosen


def _fits_chars(candidate: Candidate, rule: str, rules: Rules) -> bool:
    """Return whether *candidate* meets the character rule of its slot."""
    if rule == "split":
        return candidate.chars >= rules.split_chars
    if rule == "whole":
        return candidate.chars <= rules.one_request_chars
    return True


# ── the items ────────────────────────────────────────────────────────────────


def _items(chosen: Sequence[Candidate], rules: Rules, with_level: bool) -> list[dict]:
    """Return the manifest items of the chosen texts, numbered per language in band order.

    *with_level* writes the corpus's CEFR level into an item that has one.  ``gec-2026-12`` leaves
    it out, so its manifest stays what it was.
    """
    items: list[dict] = []
    for language in ("de", "en"):
        ordered = sorted(
            (candidate for candidate in chosen if candidate.language == language),
            key=lambda candidate: (
                list(rules.bands).index(candidate.band),
                candidate.words,
                candidate.key,
            ),
        )
        for number, candidate in enumerate(ordered, start=1):
            item = {
                "id": f"{language}-{number:02d}",
                "language": language,
                "kind": TextKind.ERRORS,
                "corpus": "falko-merlin" if language == "de" else "wi",
                "band": candidate.band,
                "pointer": candidate.pointer,
            }
            if with_level and candidate.level:
                item["level"] = candidate.level
            items.append(item)
    return items


__all__ = ["Candidate", "Rules", "Slot", "select_texts"]
