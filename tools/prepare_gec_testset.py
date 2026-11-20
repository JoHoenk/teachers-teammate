"""Build the test set for ``tools/evaluate.py`` from public learner corpora.

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

Output: a test set of the store, ``<store>/testsets/<id>/`` — one ``<language>.jsonl`` of
:class:`~tools.evaluation.core.records.TestRecord` per line, and the ``manifest.json`` that pins
the pointers and the hashes of what they resolve to.  Every reference change of a German or
English text also carries the error label the annotators gave it
(:mod:`tools.evaluation.core.labels`): the codes only (``R:DET:FORM``), found again in the
corpora's M2 files; the French texts have none.  ``kind`` is ``"errors"`` (``source`` has errors,
``reference`` is the corrected text) or ``"correct"`` (``sentences`` are already correct and
``source`` is their join).

The set is *selected* by the recipe of its id (:mod:`tools.evaluation.input.recipes`) and *built*
by :func:`~tools.evaluation.input.testset.resolve`: the recipe only chooses pointers (which text,
which sentences) and hands them to the resolver, so a rebuild calls exactly the code that built
the texts.  ``--built`` pins the day, so two builds of one day are byte-identical.  The manifest is
then checked against the corpora, and the build exits 1 when a corpus file or an item no longer
matches what was pinned.

Usage::

    PYTHONPATH=. python3 tools/prepare_gec_testset.py --testset gec-2026-12 \\
        --corpora ~/gec-testset/corpora.json --store ~/gec-testset/store
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
import sys

from tools.evaluation.core.constants import TextKind
from tools.evaluation.core.languages import is_language_code
from tools.evaluation.core.manifest import Manifest, read_manifest
from tools.evaluation.core.paths import workspace_path
from tools.evaluation.core.records import TestRecord
from tools.evaluation.input import recipes
from tools.evaluation.input import testset as testset_module
from tools.evaluation.input.corpora import adapter_class, names
from tools.evaluation.input.testset import Corpora, corpus_paths, words
from tools.evaluation.store import layout, writer


def _day(text: str) -> str:
    """Return *text* as a ``YYYY-MM-DD`` date, refusing anything else."""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date (YYYY-MM-DD)") from error


def _corpus_help() -> str:
    """Return the ``--corpus`` help: every registered corpus with what its path expects."""
    return "a corpus and where it is, repeatable: " + ", ".join(
        f"{name}={adapter_class(name).HINT}" for name in names()
    )


def _bands(records: Sequence[TestRecord]) -> str:
    """Return the item count per band, for the line the builder prints; ``""`` without bands."""
    counts: dict[str, int] = {}
    for record in records:
        if record.kind == TextKind.ERRORS and record.band:
            counts[record.band] = counts.get(record.band, 0) + 1
    if not counts:
        return ""
    return "; " + testset_module.band_summary(counts)


def _left_alone(records: Sequence[TestRecord]) -> str:
    """Return how many sentences the reference leaves alone, for the line the builder prints."""
    sentences = sum(len(record.sentence_spans) for record in records)
    alone = sum(len(record.unchanged_sentences) for record in records)
    return f"{alone} of {sentences} sentences left alone by the reference"


def _remove_stale(directory: Path, *, keep: set[str]) -> None:
    """Delete the ``<language>.jsonl`` files of an earlier build that this one does not write.

    A later run reads every language file of the set, so a leftover ``fr.jsonl`` would be run on
    texts that are no longer part of it.
    """
    for path in sorted(directory.glob("*.jsonl")):
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
        "--store", type=workspace_path, required=True, help="the store to write the set into"
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
        corpora = Corpora(corpus_paths(args.corpus, args.corpora))
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if missing := corpora.missing(recipe.corpora):
        print(
            f"refused: the test set {recipe.testset!r} needs {testset_module.corpus_flags(missing)}",
            file=sys.stderr,
        )
        return 2

    try:
        items = recipe.select(corpora)
        records = [
            testset_module.to_record(
                testset_module.resolve(item, corpora),
                band=item.get("band", ""),
                pointer=item.get("pointer"),
            )
            for item in items
        ]
    except testset_module.RESOLVE_ERRORS as error:
        print(
            f"refused: the corpora do not fit the test set {recipe.testset!r}: {error}",
            file=sys.stderr,
        )
        return 2

    revision = _revision()
    manifest = testset_module.build_manifest(
        items,
        corpora,
        testset=args.testset,
        built=args.built,
        selection_seed=recipe.seed,
        builder_revision=revision,
    )
    previous = _committed(args.store, args.testset)
    if previous is not None:
        if previous.question_sha256 != manifest.question_sha256:
            print(
                f"refused: {args.testset} is committed with another question; a committed set is "
                "never edited, give the rebuilt set a new id",
                file=sys.stderr,
            )
            return 2
        if previous.key_sha256 != manifest.key_sha256:
            manifest = replace(manifest, key_version=previous.key_version + 1)
            print(
                f"{args.testset}: the key changed, so key_version is {manifest.key_version}",
                flush=True,
            )
        elif previous.items == manifest.items:
            print(f"{args.testset}: unchanged, nothing written", flush=True)
            return _check(args.store, args.testset, previous, corpora, revision)

    directory = layout.testset_dir(args.store, args.testset)
    directory.mkdir(parents=True, exist_ok=True)
    _remove_stale(directory, keep={record.language for record in records})
    writer.write_testset(args.store, args.testset, records, manifest)
    _report(directory, records)
    return _check(args.store, args.testset, manifest, corpora, revision)


def _committed(root: Path, testset: str) -> Manifest | None:
    """Return the manifest already stored under *testset*, or ``None`` when the set is new."""
    path = layout.manifest_path(root, testset)
    return read_manifest(path) if path.exists() else None


def _report(directory: Path, records: Sequence[TestRecord]) -> None:
    """Print what the build wrote, one line per language."""
    for language in sorted({record.language for record in records}):
        group = [record for record in records if record.language == language]
        errors = [record for record in group if record.kind == TextKind.ERRORS]
        labels = [label for record in group for label in record.labels]
        print(
            f"{directory / f'{language}.jsonl'}: {len(errors)} texts with errors "
            f"({sum(words(record.source) for record in errors)} words{_bands(group)}), "
            f"{len(group) - len(errors)} group of correct sentences; "
            f"{_left_alone(group)}; "
            f"{sum(1 for label in labels if label.code)} of {len(labels)} reference changes labelled"
        )


def _check(root: Path, testset: str, manifest: Manifest, corpora: Corpora, revision: str) -> int:
    """Check the written manifest against the corpora; return the exit code.

    Both checks run: the corpus files must still be the ones the manifest pinned, and every item
    must rebuild to the hashes it recorded.  A dirty tree is a warning, not a refusal — a trial
    build is allowed, a set that is committed is not.
    """
    path = layout.manifest_path(root, testset)
    corpus = testset_module.corpus_problems(manifest, corpora)
    rebuilt = testset_module.check_items(manifest, corpora)
    print(
        f"{path}: {len(manifest.items)} items, built {manifest.built} at "
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
    from tools.evaluation.core.revision import (  # noqa: PLC0415  # not needed for the set
        tool_revision,
    )

    return tool_revision()


if __name__ == "__main__":
    raise SystemExit(main())
