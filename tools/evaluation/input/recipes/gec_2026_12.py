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
the budget belongs to an eval version.  At eval version 1 the budget at window 8192 was 6,026
characters for German and 6,091 for English, so a text of 6,400 or more was split and one of 5,800
or less went out as one long request.  Today's budget is lower (about 2,800 characters), so every
"pages" text of the set is split now; a CI test checks the committed manifest against the current
budget instead of these two constants.

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

from typing import cast

from .. import testset
from ..corpora.invented_fr import InventedFrench
from . import Recipe
from ._bands import Candidate, Rules, Slot, select_texts

TESTSET = "gec-2026-12"
SEED = 20261110
"""The day the rule was fixed; the seed decides only where a pool is larger than its slot."""

CORPORA = ("falko-merlin", "wi", "invented-fr")
"""The corpora this selection reads; the builder refuses to start without them."""

BANDS = {"short": (80, 150), "page": (200, 400), "pages": (600, 1200)}
"""The bands in whitespace words, and the order the ids run in."""

SPLIT_CHARS = 6400
"""A "pages" text of at least this many characters was split by the app at window 8192."""

ONE_REQUEST_CHARS = 5800
"""A "pages" text of at most this many characters went out as one request at window 8192."""

MIN_EDITS = 3
"""Fewer reference changes than this and a text measures nothing."""

MAX_EDITS_PER_100_WORDS = 40
"""More changes than this per 100 words and the text is a rewrite, not a correction."""

RULES = Rules(
    bands=BANDS,
    split_chars=SPLIT_CHARS,
    one_request_chars=ONE_REQUEST_CHARS,
    min_edits=MIN_EDITS,
    max_edits_per_100_words=MAX_EDITS_PER_100_WORDS,
)
"""The numbers of this recipe.  They are written out here, not imported, so no other recipe's
change can move what this one selects (``test_recipe_gec_2026_12`` pins the draw)."""

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


def select(corpora: testset.Corpora) -> list[dict]:
    """Return the items of ``gec-2026-12``, in the order the records are written."""
    items = select_texts(corpora, testset_id=TESTSET, rules=RULES, slots=SLOTS, seed=SEED)
    return items + _french_items(corpora)


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
    "RULES",
    "SEED",
    "SLOTS",
    "SPLIT_CHARS",
    "TESTSET",
    "Candidate",
    "Rules",
    "Slot",
    "select",
]


RECIPE = Recipe(testset=TESTSET, seed=SEED, corpora=CORPORA, select=select)
