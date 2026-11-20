"""Publish a local store into the repository: the redacted run files, and the index that lists them.

Developer tool, offline.  A local run holds the corpus text; a committed one holds the model's edits
as positions plus short replacements, the notes as categories plus capped explanations, the outcomes
as numbers, and the facts that let someone check and re-score it later (question hash, key hash,
scorer version, digest, seed, machine).  The work lives in :mod:`tools.evaluation.publish`; this file
only parses arguments and prints.

Usage::

    bazel run //tools:publish -- export --store ~/gec-testset/store --out evaluations/proofreading
    bazel run //tools:publish -- index --out evaluations/proofreading
    bazel run //tools:publish -- index --out evaluations/proofreading --check

``export`` plans every target before it writes anything: an identical run is ``unchanged`` and
skipped, a run that grew is replaced, a run whose key or scorer moved needs ``--rescore``, and a run
that does not hold every text of its set is skipped unless ``--partial`` is given.  A refusal happens
before the first write, so an aborted export never leaves a run file without an index entry.
``index`` regenerates ``INDEX.md``, or with ``--check`` says whether it is current.
"""

from __future__ import annotations

import argparse
import sys

from tools.evaluation.core.formats import FormatError
from tools.evaluation.core.paths import workspace_path
from tools.evaluation.input.corpora import adapter_class, names
from tools.evaluation.input.testset import Corpora, corpus_paths
from tools.evaluation.publish.export import export as export_runs
from tools.evaluation.publish.index import INDEX_NAME, index_markdown, write_index

DEFAULT_STORE = "store"
"""Where the local store is, below the repository, unless a command names another."""

DEFAULT_OUT = "evaluations/proofreading"
"""Where the committed runs are, below the repository."""


def _export(args) -> int:
    """Write the local store's runs as committed run files; return the exit code."""
    root = workspace_path(args.store)
    out = workspace_path(args.out)
    try:
        written = export_runs(
            root,
            out=out,
            testsets=args.testset or None,
            folders=args.folder or None,
            cache=workspace_path(args.cache) if args.cache else None,
            corpora=_corpora(args),
            label=args.label or "",
            rescore=args.rescore,
            partial=args.partial,
            progress=lambda name: print(f"scoring {name}", file=sys.stderr),
        )
    except (ValueError, FormatError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    for line in written.lines():
        print(line)
    print(f"wrote {out / INDEX_NAME}")
    return 0


def _index(args) -> int:
    """Regenerate ``INDEX.md``, or check that it is current; return the exit code."""
    out = workspace_path(args.out)
    target = out / INDEX_NAME
    try:
        wanted = index_markdown(out)
        if args.check:
            if not target.is_file():
                print(f"refused: {target} is missing; run `index` without --check", file=sys.stderr)
                return 1
            if target.read_text(encoding="utf-8") != wanted:
                print(f"refused: {target} is out of date; run `index`", file=sys.stderr)
                return 1
            print(f"ok {target}")
            return 0
        write_index(out)
    except (ValueError, FormatError, OSError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print(f"wrote {target}")
    return 0


def _corpora(args) -> Corpora | None:
    """Return the corpora a command was given, or ``None`` when it named none."""
    if not args.corpus and not args.corpora:
        return None
    try:
        return Corpora(
            corpus_paths(args.corpus, workspace_path(args.corpora) if args.corpora else None)
        )
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        raise SystemExit(2) from error


def _corpora_arguments(target) -> None:
    """Add the corpus options to *target*."""
    target.add_argument(
        "--corpus",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="a corpus and where it is, repeatable: "
        + ", ".join(f"{name}={adapter_class(name).HINT}" for name in names()),
    )
    target.add_argument(
        "--corpora",
        help="a JSON object {name: path}; a relative path in it is relative to the file",
    )


def _store_arguments(target, *, default: str = DEFAULT_STORE) -> None:
    """Add the option that names the local store to *target*."""
    target.add_argument(
        "--store",
        default=default,
        help="the local store's directory (default: %(default)s)",
    )


def _out_arguments(target) -> None:
    """Add the option that names the committed runs to *target*."""
    target.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="the repository directory (default: %(default)s)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the tool."""
    parser = argparse.ArgumentParser(
        prog="publish",
        description="Write a local store's runs into the repository, and list them.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _export_command(sub)
    _index_command(sub)
    return parser


def _export_command(sub) -> None:
    """Add the subcommand that writes the committed run files."""
    exporter = sub.add_parser("export", help="write local runs as committed run files")
    _store_arguments(exporter)
    _out_arguments(exporter)
    exporter.add_argument(
        "--testset", action="append", default=[], help="a test set to read, repeatable"
    )
    exporter.add_argument(
        "--folder", action="append", default=[], help="a run folder to read, repeatable"
    )
    exporter.add_argument(
        "--cache", help="where the score cache lives, so a second export need not score again"
    )
    exporter.add_argument(
        "--label", help="appended to the file name, for two runs of one configuration"
    )
    exporter.add_argument(
        "--rescore",
        action="store_true",
        help="replace a committed run whose numbers were measured against another key or scorer",
    )
    exporter.add_argument(
        "--partial",
        action="store_true",
        help="export a run that holds fewer answers than its test set has texts",
    )
    _corpora_arguments(exporter)
    exporter.set_defaults(handler=_export)


def _index_command(sub) -> None:
    """Add the subcommand that regenerates the index."""
    indexer = sub.add_parser("index", help="regenerate INDEX.md")
    _out_arguments(indexer)
    indexer.add_argument(
        "--check", action="store_true", help="do not write: fail when INDEX.md is missing or stale"
    )
    indexer.set_defaults(handler=_index)


def main(argv: list[str] | None = None) -> int:
    """Run the tool; return the exit code."""
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
