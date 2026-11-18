"""Build the evaluation report: one static page with the texts, the variants and the numbers.

Developer tool, offline: it reads a test set and one or more local run caches and writes a folder
that opens from ``file://`` after unzipping, with no server and nothing from the network.  Every
number in it comes from the same functions the command line report prints from
(``tools/model_eval/viewer``), so the page cannot show a figure the report would print differently.

A relative path is relative to the repository, as in every command of the evaluation tools
(``cli_paths.workspace_path``), so ``bazel run`` does not write into its runfiles tree.  The
output must lie outside a git work tree: a report holds corpus text, and the repository holds
none (the same rule as the redacted export).

Usage::

    bazel run //tools:eval_report -- build \\
        --testset ~/gec-testset/set-2026-12 \\
        --cache ~/gec-testset/results-exchange --cache ~/gec-testset/results-text \\
        --out ~/gec-testset/reports/2026-11-15_exchange-vs-text \\
        [--zip] [--single-file] [--model gemma4:12b] [--languages de,en]
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import replace
import sys

from tools.model_eval import results
from tools.model_eval.cli_paths import workspace_path
from tools.model_eval.viewer import load, model, page


def main(argv: Sequence[str] | None = None) -> int:
    """Build the report the arguments ask for and print what was written."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="write the report folder")
    build.add_argument("--testset", type=workspace_path, required=True, help="the set of texts")
    build.add_argument(
        "--cache",
        type=workspace_path,
        action="append",
        required=True,
        help="a directory of run caches; repeat for several",
    )
    build.add_argument("--out", type=workspace_path, required=True, help="the folder to write")
    build.add_argument("--zip", action="store_true", help="also write <out>.zip")
    build.add_argument(
        "--single-file", action="store_true", help="write one report.html instead of a folder"
    )
    build.add_argument(
        "--model", action="append", default=[], help="only these models; repeat for several"
    )
    build.add_argument("--languages", default="", help="only these languages, comma separated")
    arguments = parser.parse_args(argv)
    return _build(arguments)


def _build(arguments: argparse.Namespace) -> int:
    """Write the report of the arguments and print what it holds."""
    testset = load.read_testset(arguments.testset)
    if arguments.languages:
        testset = _only_languages(testset, _codes(arguments.languages))
    caches = []
    for directory in arguments.cache:
        # Every row is scored again from its texts, which takes a while on a large cache.
        print(f"reading {directory} ...", file=sys.stderr, flush=True)
        caches.append(
            load.read_cache(
                directory,
                testset,
                progress=lambda name: print(f"  {name}", file=sys.stderr, flush=True),
            )
        )
    caches = tuple(caches)
    if arguments.model:
        caches = tuple(_only_models(cache, set(arguments.model)) for cache in caches)
    built = model.build(testset, caches)
    out = page.build(built, arguments.out, single_file=arguments.single_file, zip_it=arguments.zip)
    skipped = sum(len(entry.skipped) for entry in built.inputs)
    print(
        f"{out}: {len(built.texts)} texts, {len(built.variants)} variants, "
        f"{len(built.items)} versions, {skipped} skipped"
    )
    for variant in built.variants:
        done = ", ".join(f"r{repeat}: {count}" for repeat, count in variant.texts_done.items())
        print(f"  {variant.id} {variant.label} ({done})")
    return 0


def _codes(languages: str) -> tuple[str, ...]:
    """Return the language codes of a comma separated list."""
    return tuple(code.strip() for code in languages.split(",") if code.strip())


def _only_languages(testset: load.TestSet, codes: Sequence[str]) -> load.TestSet:
    """Return *testset* with only the texts of *codes*."""
    records = tuple(record for record in testset.records if record.language in codes)
    return replace(
        testset,
        records=records,
        by_key={results.record_key(record): record for record in records},
    )


def _only_models(cache: load.Cache, models: set[str]) -> load.Cache:
    """Return *cache* with only the runs of *models*."""
    return replace(
        cache,
        runs=tuple(run for run in cache.runs if str(run.header.get("model", "")) in models),
    )


if __name__ == "__main__":
    sys.exit(main())
