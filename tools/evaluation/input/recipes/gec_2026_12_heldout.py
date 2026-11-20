"""The selection of ``gec-2026-12-heldout``: the texts nobody tuned on, for confirming a choice.

``gec-2026-12`` ranks the models and is the set a prompt or a setting is tuned on, so a variant
that wins there may only fit its 22 texts.  This set has the same shape (11 whole texts per
language: 4 short, 5 page and 2 pages) and the same filters, from splits the first never read:
Falko-MERLIN ``dev`` for German and W&I ``train`` for English.  A prompt variant, a temperature
or a text budget is adopted only when it wins on both.  French is not part of it: its texts are
invented and reported only.

**The "pages" texts and the long-text path.**  The first set holds one German and one English
text above ``SPLIT_CHARS`` (6,400 characters, the length the app split at in eval version 1), and
those are forced by the corpora: no Falko-MERLIN ``dev`` text and no W&I ``train`` essay reaches
it.  The two longest slots here are therefore texts of at most ``ONE_REQUEST_CHARS`` (5,800), nine
of them in German ``dev`` and seventeen of levels B and C in English ``train`` to draw from.  The
budget of the current eval version is far lower (about 2,800 characters at window 8192), so every
"pages" text of this set (3,272 to 4,444 characters) is split by the app as well; that is what the
two constants no longer say, and ``test_every_pages_text_is_split_at_the_current_budget`` checks it
against the budget instead.  The "page" texts stay below it and go out as one request.

**The level.**  W&I names the CEFR level of every essay (``B1.ii``); the item records its level
(``B1``) and the build copies it into the record.  **No run reads it yet**: every text is asked
about at the default level, as in ``gec-2026-12``, so a result of one set is comparable with the
other.  Asking each text at its own level changes the instructions every run hashes and is a
step for both sets at once.  Falko-MERLIN's files carry no level, so a German item has none.

**The counts.**  German ``dev`` holds 26 short MERLIN texts, 20 MERLIN and 13 Falko page texts and
9 Falko "pages" texts; W&I ``train`` holds 528 short level A essays, 383 and 392 page essays of
levels B and C and 20 "pages" essays of levels B and C, 17 of them at most 5,800 characters.  The
slots are those of ``gec-2026-12``, except that both "pages" slots take texts of at most 5,800
characters.

**The rules.**  The filters, the bands and the draw are those of :mod:`.gec_2026_12` and the code
is shared (:mod:`._bands`), but the numbers are written out below, not imported: this recipe is
frozen once its manifest is committed, and a change to the other one must not move it.  The seed
is the day this rule was fixed.
"""

from __future__ import annotations

from .. import testset
from . import Recipe
from ._bands import Rules, Slot, select_texts

TESTSET = "gec-2026-12-heldout"
SEED = 20261116
"""The day the rule was fixed; the seed decides only where a pool is larger than its slot."""

CORPORA = ("falko-merlin", "wi")
"""The corpora this selection reads; the builder refuses to start without them."""

RULES = Rules(
    bands={"short": (80, 150), "page": (200, 400), "pages": (600, 1200)},
    split_chars=6400,
    one_request_chars=5800,
    min_edits=3,
    max_edits_per_100_words=40,
)
"""The numbers of this recipe, the same as those of ``gec-2026-12`` and written out again."""

GERMAN_SPLIT = "dev"
ENGLISH_SPLIT = "train"
"""The splits ``gec-2026-12`` does not read (Falko-MERLIN ``test``, W&I ``dev``)."""

SLOTS = (
    Slot("de", "short", ("merlin",), 4),
    Slot("de", "page", ("merlin",), 2),
    Slot("de", "page", ("falko",), 3),
    Slot("de", "pages", ("falko",), 2, "whole"),
    Slot("en", "short", ("A",), 4),
    Slot("en", "page", ("B",), 3),
    Slot("en", "page", ("C",), 2),
    Slot("en", "pages", ("B", "C"), 2, "whole"),
)


def select(corpora: testset.Corpora) -> list[dict]:
    """Return the items of ``gec-2026-12-heldout``, in the order the records are written."""
    return select_texts(
        corpora,
        testset_id=TESTSET,
        rules=RULES,
        slots=SLOTS,
        seed=SEED,
        german_split=GERMAN_SPLIT,
        english_split=ENGLISH_SPLIT,
        with_level=True,
    )


__all__ = [
    "CORPORA",
    "ENGLISH_SPLIT",
    "GERMAN_SPLIT",
    "RULES",
    "SEED",
    "SLOTS",
    "TESTSET",
    "select",
]


RECIPE = Recipe(testset=TESTSET, seed=SEED, corpora=CORPORA, select=select)
