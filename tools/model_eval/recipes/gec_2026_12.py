"""The selection of ``gec-2026-12``: eleven whole texts per language, in three bands.

The set replaces the eight short texts of ``gec-2026-11`` with texts a teacher actually meets: a
short note, a page and several pages.  Each band is drawn from the default split of its corpus
only — Falko-MERLIN ``test`` for German, W&I ``dev`` for English — so no pointer carries a
``split``.  French is the invented set, unchanged and reported only.

Bands, in whitespace words (``testset.words``, what a teacher means by "a page of 300 words"):

===========  ==========  ==========================================================
band         words       what it is
===========  ==========  ==========================================================
``short``    80 to 150   a note or a short letter
``page``     200 to 400   one page
``pages``    600 to 1200  several pages; one text the app splits, one it sends whole
===========  ==========  ==========================================================

The slots (``SLOTS``) are 4 short, 5 page and 2 pages per language: German 4 MERLIN short, 2
MERLIN and 3 Falko page, 2 Falko pages; English 4 level A short, 3 B and 2 C page, 1 B or C pages
that goes out whole and 1 that is split.

**The two "pages" texts.**  ``SPLIT_CHARS`` and ``ONE_REQUEST_CHARS`` are constants of this
recipe, not read from ``protocol``: a recipe never changes once its manifest is committed, while
the budget belongs to an eval version.  At eval version 1 the budget at window 8192 is 6,026
characters for German and 6,091 for English, so a text of 6,400 or more is split and one of 5,800
or less goes out as one long request.  A CI test checks the committed manifest against the current
budget instead.

**Three of the four "pages" texts are forced by the corpora, not by the seed.**  In the default
splits there is exactly one German text above 6,400 characters (``sa009``) and exactly two English
texts in the band (``1-64631`` whole, ``1-104967`` split), so only the German "whole" text is a
draw.  A later set can draw more from the train splits if the gates need them.

**Filters** (a text that fails one is never chosen): at least ``MIN_EDITS`` reference changes, at
most ``MAX_EDITS_PER_100_WORDS`` of them per 100 words (a near-rewrite only measures agreement
with one annotator's rewrite), labels that fit, and sentence spans.  MERLIN's ``X`` anonymisation
marks stay: the app's own input carries placeholders after anonymisation.

**Randomness.**  One generator per slot, seeded with a string, so the draw does not depend on
``PYTHONHASHSEED`` and each slot is independent of the others.  Candidates are sorted by their key
before ``rng.sample``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import contextlib
from dataclasses import dataclass
import io
import random
from typing import cast

from .. import testset
from ..corpora.falko_merlin import FalkoMerlin
from ..corpora.invented_fr import InventedFrench
from ..corpora.wi import WriteAndImprove
from ..quality import ordered_edits
from . import Recipe

TESTSET = "gec-2026-12"
SEED = 20261110
"""The day the rule was fixed; the seed decides only where a pool is larger than its slot."""

CORPORA = ("falko-merlin", "wi", "invented-fr")
"""The corpora this selection reads; the builder refuses to start without them."""

BANDS = {"short": (80, 150), "page": (200, 400), "pages": (600, 1200)}
"""The bands in whitespace words, and the order the ids run in."""

SPLIT_CHARS = 6400
"""A "pages" text of at least this many characters is split by the app at window 8192."""

ONE_REQUEST_CHARS = 5800
"""A "pages" text of at most this many characters goes out as one request at window 8192."""

MIN_EDITS = 3
"""Fewer reference changes than this and a text measures nothing."""

MAX_EDITS_PER_100_WORDS = 40
"""More changes than this per 100 words and the text is a rewrite, not a correction."""

_words = testset.words


@dataclass(frozen=True)
class Slot:
    """One draw of the selection: how many texts of which band, from which origins."""

    language: str
    band: str
    source: tuple[str, ...]
    """The MERLIN / Falko origins, or the W&I levels, a text may come from."""

    count: int
    chars: str = "any"
    """``"any"``, ``"split"`` (at least :data:`SPLIT_CHARS`) or ``"whole"`` (at most
    :data:`ONE_REQUEST_CHARS`)."""

    @property
    def name(self) -> str:
        """Return the slot as the refusal names it: ``de page falko any``."""
        return f"{self.language} {self.band} {'+'.join(self.source)} {self.chars}"


SLOTS = (
    Slot("de", "short", ("merlin",), 4),
    Slot("de", "page", ("merlin",), 2),
    Slot("de", "page", ("falko",), 3),
    Slot("de", "pages", ("falko",), 1, "whole"),
    Slot("de", "pages", ("falko",), 1, "split"),
    Slot("en", "short", ("A",), 4),
    Slot("en", "page", ("B",), 3),
    Slot("en", "page", ("C",), 2),
    Slot("en", "pages", ("B", "C"), 1, "whole"),
    Slot("en", "pages", ("B", "C"), 1, "split"),
)


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


def select(corpora: testset.Corpora) -> list[dict]:
    """Return the items of ``gec-2026-12``, in the order the records are written."""
    candidates = _candidates(corpora)
    chosen = _draw(candidates)
    return _items(chosen) + _french_items(corpora)


# ── the candidates ───────────────────────────────────────────────────────────


def _candidates(corpora: testset.Corpora) -> list[Candidate]:
    """Return every text of the default splits that fits a band and the filters."""
    found = _german_candidates(corpora) + _english_candidates(corpora)
    return sorted(found, key=lambda candidate: candidate.key)


def _german_candidates(corpora: testset.Corpora) -> list[Candidate]:
    """Return the German texts of the Falko-MERLIN test split that could be chosen."""
    german = cast(FalkoMerlin, corpora.adapter("falko-merlin"))
    pointers = [
        (
            key,
            {
                "id": f"de-{key}",
                "language": "de",
                "kind": "errors",
                "corpus": "falko-merlin",
                "pointer": {
                    "origin": origin,
                    "text": key.split(":", 1)[1],
                    "sentences": [0, len(pairs)],
                },
            },
        )
        for origin, by_id in german.texts().items()
        for key, pairs in by_id.items()
    ]
    return _scan(pointers, corpora, source_of=lambda item: item["pointer"]["origin"])


def _english_candidates(corpora: testset.Corpora) -> list[Candidate]:
    """Return the English essays of the W&I dev split that could be chosen."""
    english = cast(WriteAndImprove, corpora.adapter("wi"))
    pointers = [
        (
            row["id"],
            {
                "id": f"en-{row['id']}",
                "language": "en",
                "kind": "errors",
                "corpus": "wi",
                "pointer": {"level": level, "document": row["id"]},
            },
        )
        for level, rows in english.rows().items()
        for row in rows
        if row["edits"]
    ]
    return _scan(pointers, corpora, source_of=lambda item: item["pointer"]["level"])


def _scan(
    pointers: Iterable[tuple[str, dict]], corpora: testset.Corpora, *, source_of
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
            candidate = _candidate(key, item, resolved, source_of(item))
            if candidate is not None:
                found.append(candidate)
    return found


def _candidate(key: str, item: dict, resolved, source: str) -> Candidate | None:
    """Return the candidate of a resolved text, or ``None`` when it fails a filter."""
    words = _words(resolved.source)
    band = _band_of(words)
    if band is None or not resolved.sentence_spans or not resolved.labels:
        return None
    edits = len(ordered_edits(resolved.source, resolved.reference))
    if edits < MIN_EDITS or edits * 100 > MAX_EDITS_PER_100_WORDS * words:
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
    )


def _band_of(words: int) -> str | None:
    """Return the band *words* falls in, or ``None`` when it falls in none."""
    for band, (low, high) in BANDS.items():
        if low <= words <= high:
            return band
    return None


# ── the draw ─────────────────────────────────────────────────────────────────


def _draw(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Return the chosen texts, in the order the ids run.

    Raises:
        ValueError: if a slot has fewer candidates than it needs; the message names the slot.
    """
    chosen: list[Candidate] = []
    for slot in SLOTS:
        pool = sorted(
            (
                candidate
                for candidate in candidates
                if candidate.language == slot.language
                and candidate.band == slot.band
                and candidate.source in slot.source
                and _fits_chars(candidate, slot.chars)
                and candidate not in chosen
            ),
            key=lambda candidate: candidate.key,
        )
        if len(pool) < slot.count:
            raise ValueError(f"{TESTSET}: {slot.name}: {len(pool)} candidates for {slot.count}")
        rng = random.Random(
            f"{SEED}/{slot.language}/{slot.band}/{'+'.join(slot.source)}/{slot.chars}"
        )
        chosen.extend(rng.sample(pool, slot.count))
    return chosen


def _fits_chars(candidate: Candidate, rule: str) -> bool:
    """Return whether *candidate* meets the character rule of its slot."""
    if rule == "split":
        return candidate.chars >= SPLIT_CHARS
    if rule == "whole":
        return candidate.chars <= ONE_REQUEST_CHARS
    return True


# ── the items ────────────────────────────────────────────────────────────────


def _items(chosen: Sequence[Candidate]) -> list[dict]:
    """Return the manifest items of the chosen texts, numbered per language in band order."""
    items: list[dict] = []
    for language in ("de", "en"):
        ordered = sorted(
            (candidate for candidate in chosen if candidate.language == language),
            key=lambda candidate: (
                list(BANDS).index(candidate.band),
                candidate.words,
                candidate.key,
            ),
        )
        for number, candidate in enumerate(ordered, start=1):
            items.append(
                {
                    "id": f"{language}-{number:02d}",
                    "language": language,
                    "kind": "errors",
                    "corpus": "falko-merlin" if language == "de" else "wi",
                    "band": candidate.band,
                    "pointer": candidate.pointer,
                }
            )
    return items


def _french_items(corpora: testset.Corpora) -> list[dict]:
    """Return the manifest items of the invented French texts, in the order of the file.

    Copied from the retired recipe of ``gec-2026-11``, so it does not depend on another module.
    """
    return [
        {
            "id": record["id"],
            "language": "fr",
            "kind": record["kind"],
            "corpus": "invented-fr",
            "pointer": {"text": record["id"]},
        }
        for record in cast(InventedFrench, corpora.adapter("invented-fr")).records().values()
    ]


__all__ = [
    "BANDS",
    "CORPORA",
    "MAX_EDITS_PER_100_WORDS",
    "MIN_EDITS",
    "ONE_REQUEST_CHARS",
    "SEED",
    "SLOTS",
    "SPLIT_CHARS",
    "TESTSET",
    "Candidate",
    "Slot",
    "select",
]


RECIPE = Recipe(testset=TESTSET, seed=SEED, corpora=CORPORA, select=select)
