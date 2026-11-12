"""Build the test set for ``tools/compare_writing_models.py`` from public learner corpora.

Developer tool, standard library only, not run in CI.  The corpora are *not* part of the
repository (licences, and real learner text): download them, point this script at them and keep
its output outside the repository too.

Sources (write them into the results you publish):

* German: Falko-MERLIN GEC corpus (Boyd 2018), test split.  Falko CC BY 3.0, MERLIN CC BY-SA 4.0.
  ``https://github.com/adrianeboyd/boyd-wnut2018/releases/download/wnut2018/data.tar.gz``
* English: W&I+LOCNESS v2.1 (BEA-2019 shared task), dev split.  Non-commercial use only; credit
  CECL (Université catholique de Louvain) and the University of Cambridge.
  ``https://www.cl.cam.ac.uk/research/nl/bea2019st/data/wi+locness_v2.1.bea19.tar.gz``
* French: no public learner corpus with corrections is available, so ``gec_fr_invented.jsonl``
  next to this script holds eight invented texts and ten correct sentences, written by the
  tool's author (an AI model, *not* a teacher): treat French results as indicative only.

Which set uses what: ``gec-2026-12`` draws on the Falko-MERLIN test split, the W&I dev split
(levels A to C) and the invented French set, and needs no LOCNESS, because its German and English
items are whole texts with their own sentences left alone inside them instead of a glued group of
correct sentences.  ``gec-2026-11``, the retired set, took those correct sentences from the
LOCNESS essays.

Output, one file per language (``de.jsonl``, ``en.jsonl``, ``fr.jsonl``), one ``TestRecord`` per
line (``tools.model_eval.results``, ``schema_version: 1``), so ``tools/compare_writing_models.py``
reads it as it reads any other file.  Every reference change of a German or English text also
carries the error label the annotators gave it (``tools.model_eval.error_labels``): the codes only
(``R:DET:FORM``), found again in the corpora's M2 files; the French texts have none.  ``kind`` is
``"errors"`` (``source`` has errors, ``reference`` is the corrected text) or ``"correct"``
(``sentences`` are already correct and ``source`` is their join).

The set is *selected* by the recipe of its id (``tools/model_eval/recipes/``) and *built* by
``tools.model_eval.testset.resolve``: the recipe only chooses pointers (which text, which
sentences) and hands them to the resolver, so a rebuild calls exactly the code that built the
texts.  ``--manifest`` writes the pointers and the hashes of what they resolve to, for a run to be
checked against later; ``--built`` pins the day, so two builds of one day are byte-identical.  The
manifest is then checked against the corpora, and the build exits 1 when a corpus file or an item
no longer matches what was pinned.

Usage::

    PYTHONPATH=. python3 tools/prepare_gec_testset.py --testset gec-2026-12 \\
        --corpora ~/gec-testset/corpora.json --out ~/gec-testset/set-2026-12 \\
        --manifest ~/gec-testset/manifest.json
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date
from pathlib import Path
import sys

from tools.model_eval import recipes, testset
from tools.model_eval.cli_paths import workspace_path
from tools.model_eval.results import TestRecord, is_language_code, write_records

_words = testset.words


def _day(text: str) -> str:
    """Return *text* as a ``YYYY-MM-DD`` date, refusing anything else."""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date (YYYY-MM-DD)") from error


def _corpus_help() -> str:
    """Return the ``--corpus`` help: every registered corpus with what its path expects."""
    return "a corpus and where it is, repeatable: " + ", ".join(
        f"{name}={testset.adapter_class(name).HINT}" for name in testset.names()
    )


def _bands(records: Sequence[TestRecord]) -> str:
    """Return the item count per band, for the line the builder prints; ``""`` without bands."""
    counts: dict[str, int] = {}
    for record in records:
        if record.kind == "errors" and record.band:
            counts[record.band] = counts.get(record.band, 0) + 1
    if not counts:
        return ""
    return "; " + testset.band_summary(counts)


def _left_alone(records: Sequence[TestRecord]) -> str:
    """Return how many sentences the reference leaves alone, for the line the builder prints."""
    sentences = sum(len(record.sentence_spans) for record in records)
    alone = sum(len(record.unchanged_sentences) for record in records)
    return f"{alone} of {sentences} sentences left alone by the reference"


def _remove_stale(out: Path, *, keep: set[str]) -> None:
    """Delete the ``<language>.jsonl`` files of an earlier build that this one does not write.

    A later run reads every language file of the set, so a leftover ``fr.jsonl`` would be run on
    texts that are no longer part of it.
    """
    for path in sorted(out.glob("*.jsonl")):
        if is_language_code(path.stem) and path.stem not in keep:
            path.unlink()
            print(f"{path}: removed, it is not part of this build")


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--testset",
        required=True,
        help=f"the test-set id, whose recipe selects the texts: {', '.join(recipes.names())}",
    )
    parser.add_argument(
        "--corpus", action="append", default=[], metavar="NAME=PATH", help=_corpus_help()
    )
    parser.add_argument(
        "--corpora",
        type=workspace_path,
        help="a JSON object {name: path}; a relative path in it is relative to the file",
    )
    parser.add_argument(
        "--out", type=workspace_path, required=True, help="output directory (outside the repo)"
    )
    parser.add_argument(
        "--manifest",
        type=workspace_path,
        help="write the manifest of the set here (pointers and hashes, no corpus text)",
    )
    parser.add_argument(
        "--built",
        type=_day,
        default=date.today().isoformat(),
        help="the day the set is built, YYYY-MM-DD (default: today); two builds of one day are "
        "byte-identical",
    )
    args = parser.parse_args()

    try:
        recipe = recipes.recipe(args.testset)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    try:
        corpora = testset.Corpora(testset.corpus_paths(args.corpus, args.corpora))
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if missing := corpora.missing(recipe.corpora):
        print(
            f"refused: the test set {recipe.testset!r} needs {testset.corpus_flags(missing)}",
            file=sys.stderr,
        )
        return 2

    try:
        items = recipe.select(corpora)
        by_language: dict[str, list[dict]] = {}
        for item in items:
            by_language.setdefault(item["language"], []).append(item)
        built = {
            language: [
                testset.to_record(testset.resolve(item, corpora), band=item.get("band", ""))
                for item in language_items
            ]
            for language, language_items in by_language.items()
        }
    except testset.RESOLVE_ERRORS as error:
        print(
            f"refused: the corpora do not fit the test set {recipe.testset!r}: {error}",
            file=sys.stderr,
        )
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    _remove_stale(args.out, keep=set(built))
    for language, records in built.items():
        path = args.out / f"{language}.jsonl"
        write_records(path, records)
        errors = sum(1 for record in records if record.kind == "errors")
        words = sum(_words(record.source) for record in records if record.kind == "errors")
        labels = [label for record in records for label in record.labels]
        print(
            f"{path}: {errors} texts with errors ({words} words{_bands(records)}), "
            f"{len(records) - errors} group of correct sentences; "
            f"{_left_alone(records)}; "
            f"{sum(1 for label in labels if label.code)} of {len(labels)} reference changes labelled"
        )

    if not args.manifest:
        return 0
    return _write_manifest(args, items, corpora, seed=recipe.seed)


def _write_manifest(args, items: Sequence[dict], corpora: testset.Corpora, *, seed: int) -> int:
    """Write the manifest of *items* and check it against the corpora; return the exit code.

    The manifest is written first, so a build that disagrees with its corpora still leaves the file
    a reader can look at.  Both checks then run: the corpus files must still be the ones the
    manifest pinned, and every item must rebuild to the hashes it recorded.  A dirty tree is a
    warning, not a refusal — a trial build is allowed, a set that is committed is not.
    """
    revision = _revision()
    manifest = testset.build_manifest(
        items,
        corpora,
        testset=args.testset,
        built=args.built,
        selection_seed=seed,
        builder_revision=revision,
    )
    testset.write_manifest(args.manifest, manifest)
    corpus = testset.corpus_problems(manifest, corpora)
    rebuilt = testset.check_items(manifest, corpora)
    print(
        f"{args.manifest}: {len(manifest['items'])} items, built {manifest['built']} at "
        f"{revision}, {len(corpus)} corpus problems, {len(rebuilt)} item problems"
    )
    for problem in corpus + rebuilt:
        print(f"  {problem}")
    if revision.endswith("+dirty"):
        print(
            "built from a dirty tree: fine for a trial, not for a set that is committed",
            file=sys.stderr,
        )
    return 1 if corpus or rebuilt else 0


def _revision() -> str:
    """Return the commit this script was run at (``"unknown"`` outside git)."""
    from tools.model_eval.measure import tool_revision  # noqa: PLC0415  # not needed for the set

    return tool_revision()


if __name__ == "__main__":
    raise SystemExit(main())
