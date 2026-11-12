"""An invented corpus, small enough to read, for the tests of the storage format.

The real test set comes from Falko-MERLIN and W&I+LOCNESS, which are not part of the repository, so
every test here builds its own corpus in ``tmp_path``: three German sentences, one English essay,
one already-correct LOCNESS sentence and one invented French text.  The layout and the file formats
are the corpora's own (so :mod:`tools.model_eval.testset` reads them unchanged); the text is
invented, which is why the tests may store it and read it back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path

from tools.model_eval import testset
from tools.model_eval.testset import Corpora

GERMAN_SOURCE = """\
falko-1\tDer Hund laufen schnell .\tDer Hund läuft schnell .
falko-1\tDas ist ein Test .\tDas ist ein Test .
"""

GERMAN_MERLIN = """\
merlin-1\tIch habe ein Hund .\tIch habe einen Hund .
"""

GERMAN_M2 = """\
S Der Hund laufen schnell .
A 2 3|||R:VERB:FORM|||läuft|||REQUIRED|||-NONE-|||0

S Das ist ein Test .

S Ich habe ein Hund .
A 2 3|||R:DET:FORM|||einen|||REQUIRED|||-NONE-|||0

"""

ENGLISH_ESSAY = "I has a dog."
ENGLISH_EDITS = [[0, [[2, 5, "have"]]]]
ENGLISH_M2 = """\
S I has a dog .
A 1 2|||R:VERB:SVA|||have|||REQUIRED|||-NONE-|||0

"""

# A second essay of level A, after the first one, so the positions of the first stay.  Its second
# sentence carries an error the annotator marked but did not correct (``UNK``): the reference
# leaves the words alone, but the sentence is not one the reference leaves alone.
ENGLISH_UNK_ESSAY = "We was there. The dog bark loud. It is quiet."
ENGLISH_UNK_EDITS = [[0, [[1, 2, "were"]]]]
ENGLISH_UNK_M2 = """\
S We was there .
A 1 2|||R:VERB:SVA|||were|||REQUIRED|||-NONE-|||0

S The dog bark loud .
A 3 4|||UNK|||UNK|||REQUIRED|||-NONE-|||0

S It is quiet .
"""

LOCNESS_TEXT = "The river runs through the town. It is quiet there."

GERMAN_TRAIN_SOURCE = """\
falko-train-1\tDer Hund laufen schnell .\tDer Hund läuft schnell .
"""

GERMAN_TRAIN_M2 = """\
S Der Hund laufen schnell .
A 2 3|||R:VERB:FORM|||läuft|||REQUIRED|||-NONE-|||0

"""

ENGLISH_TRAIN_ESSAY = "She have a cat."
ENGLISH_TRAIN_EDITS = [[0, [[1, 2, "has"]]]]
ENGLISH_TRAIN_M2 = """\
S She have a cat .
A 1 2|||R:VERB:SVA|||has|||REQUIRED|||-NONE-|||0

"""

FRENCH = (
    '{"id": "fr-1", "kind": "errors", "origin": "invented", '
    '"source": "Elle ont un chat.", "reference": "Elle a un chat."}\n'
    '{"id": "fr-correct", "kind": "correct", "origin": "invented", '
    '"source": "Le chat dort. Il fait beau.", "reference": "Le chat dort. Il fait beau.", '
    '"sentences": ["Le chat dort.", "Il fait beau."]}\n'
)


def _essay(essay_id: str, cefr: str, text: str, edits: list) -> str:
    """Return one line of a BEA JSON file: an essay with its annotator edits."""
    row = {"id": essay_id, "cefr": cefr, "text": text, "edits": edits, "userid": "u-1"}
    return json.dumps(row) + "\n"


def build_corpus(root: Path, *, extra: Mapping[str, Path] | None = None) -> Corpora:
    """Write the invented corpus below *root* and return the :class:`Corpora` that reads it.

    *extra* adds corpora of a test's own (``{"toy": <path>}``), which is how a test proves that a
    new corpus needs nothing but its adapter.
    """
    falko = root / "fm" / "data"
    (falko / "source").mkdir(parents=True)
    (falko / "source" / "falko-id-ctok-zh1.txt.test").write_text(GERMAN_SOURCE, encoding="utf-8")
    (falko / "source" / "merlin-id-ctok-zh1.txt.test").write_text(GERMAN_MERLIN, encoding="utf-8")
    (falko / "fm-test.m2").write_text(GERMAN_M2, encoding="utf-8")
    # One text in the train split, so a pointer can name a split other than the default one.
    (falko / "source" / "falko-id-ctok-zh1.txt.train").write_text(
        GERMAN_TRAIN_SOURCE, encoding="utf-8"
    )
    (falko / "source" / "merlin-id-ctok-zh1.txt.train").write_text("", encoding="utf-8")
    (falko / "fm-train.m2").write_text(GERMAN_TRAIN_M2, encoding="utf-8")

    english = root / "bea" / "wi+locness"
    (english / "json").mkdir(parents=True)
    (english / "m2").mkdir(parents=True)
    for level in ("B", "C"):
        (english / "json" / f"{level}.dev.json").write_text("", encoding="utf-8")
        (english / "m2" / f"{level}.dev.gold.bea19.m2").write_text("", encoding="utf-8")
        (english / "json" / f"{level}.train.json").write_text("", encoding="utf-8")
        (english / "m2" / f"{level}.train.gold.bea19.m2").write_text("", encoding="utf-8")
    (english / "json" / "A.dev.json").write_text(
        _essay("a-1", "A2", ENGLISH_ESSAY, ENGLISH_EDITS)
        + _essay("a-2", "A2", ENGLISH_UNK_ESSAY, ENGLISH_UNK_EDITS),
        encoding="utf-8",
    )
    (english / "m2" / "A.dev.gold.bea19.m2").write_text(
        ENGLISH_M2 + ENGLISH_UNK_M2, encoding="utf-8"
    )
    (english / "json" / "A.train.json").write_text(
        _essay("a-train-1", "A2", ENGLISH_TRAIN_ESSAY, ENGLISH_TRAIN_EDITS), encoding="utf-8"
    )
    (english / "m2" / "A.train.gold.bea19.m2").write_text(ENGLISH_TRAIN_M2, encoding="utf-8")
    (english / "json" / "N.dev.json").write_text(
        _essay("n-1", "N", LOCNESS_TEXT, [[0, []]]), encoding="utf-8"
    )

    french = root / "gec_fr_invented.jsonl"
    french.write_text(FRENCH, encoding="utf-8")
    paths: dict[str, Path] = {
        "falko-merlin": falko,
        "wi": english,
        "locness": english,
        "invented-fr": french,
    }
    paths.update(extra or {})
    return Corpora(paths)


def item_pointers() -> list[dict]:
    """Return the pointers of a four-item test set: German, English, correct sentences, French."""
    return [
        {
            "id": "de-01",
            "language": "de",
            "kind": "errors",
            "corpus": "falko-merlin",
            "pointer": {"origin": "falko", "text": "falko-1", "sentences": [0, 1]},
        },
        {
            "id": "de-02",
            "language": "de",
            "kind": "errors",
            "corpus": "falko-merlin",
            "pointer": {"origin": "merlin", "text": "merlin-1", "sentences": [0, 1]},
        },
        {
            "id": "de-correct",
            "language": "de",
            "kind": "correct",
            "corpus": "falko-merlin",
            "pointer": {"origin": "falko", "text": "falko-1", "index": 1},
        },
        {
            "id": "en-01",
            "language": "en",
            "kind": "errors",
            "corpus": "wi",
            "pointer": {"level": "A", "document": "a-1"},
        },
        {
            "id": "en-correct",
            "language": "en",
            "kind": "correct",
            "corpus": "locness",
            "pointer": {"level": "N", "document": "n-1", "span": [0, len(LOCNESS_TEXT)]},
        },
        {
            "id": "fr-01",
            "language": "fr",
            "kind": "errors",
            "corpus": "invented-fr",
            "pointer": {"text": "fr-1"},
        },
        {
            "id": "fr-correct",
            "language": "fr",
            "kind": "correct",
            "corpus": "invented-fr",
            "pointer": {"text": "fr-correct"},
        },
    ]


def manifest(
    root: Path,
    corpora: Corpora,
    *,
    testset_id: str = "test-2026-11",
    pointers: Sequence[dict] | None = None,
) -> dict:
    """Return a manifest of the invented corpus, pinned by the hashes of *corpora*.

    *pointers* replaces the four-item set, for a test that needs another one.
    """
    return testset.build_manifest(
        item_pointers() if pointers is None else pointers,
        corpora,
        testset=testset_id,
        built="2026-11-09",
        selection_seed=20261107,
        builder_revision="abc1234",
    )


def write_corpus(
    root: Path,
    *,
    extra: Mapping[str, Path] | None = None,
    pointers: Sequence[dict] | None = None,
) -> tuple[Corpora, dict]:
    """Write the corpus and a manifest of it below *root*; return both."""
    corpora = build_corpus(root / "corpora", extra=extra)
    pinned = manifest(root, corpora, pointers=pointers)
    testset.write_manifest(root / "manifest.json", pinned)
    return corpora, pinned
