"""Keep proofreading runs in the repository: convert, validate, mark, accept, manifest.

Developer tool, not run in CI.  A local run writes a run file with the source, the reference and the
model's output in it; the corpora are licensed and are not part of this repository.  This tool turns
such a run into a *committed* one — through :mod:`tools.evaluation.publish` — and keeps the human
data beside it: the marks a teacher made, and the alternatives they accepted.

The work lives in :mod:`tools.evaluation.store` (where the files are, how they are read and checked,
and the marks), :mod:`tools.evaluation.publish` (the redacted run file, the index, and the check of
the committed tree), :mod:`tools.evaluation.input` (the test sets and the corpora) and
:mod:`tools.evaluation.convert` (the one-off converter of the old store); this file only parses
arguments and prints.

Usage::

    bazel run //tools:store -- convert --old-root ~/gec-testset --store ~/gec-testset/store \\
        --manifest gec-2026-12=evaluations/proofreading/testsets/gec-2026-12/manifest.json
    bazel run //tools:store -- validate --store ~/gec-testset/store
    bazel run //tools:store -- marks SHEET.md --marker jh --store ~/gec-testset/store
    bazel run //tools:store -- accept-marks --testset gec-2026-12 --marker jh \\
        --store ~/gec-testset/store
    bazel run //tools:store -- import-blind-key KEY.json SHEET.md --marker jh \\
        --testset gec-2026-12 --store ~/gec-testset/store
    bazel run //tools:store -- manifest --manifest evaluations/proofreading/testsets/.../manifest.json

``convert`` reads the old ``results-*`` folders and the old test sets once and writes the new store;
it never edits or deletes an old folder.  ``validate`` checks the store with the rules that need no
corpus (what the test suite runs), and with the corpora named it also applies the five-word rule and
the hashes against the real text.  ``marks`` turns a filled-in sheet into the marks file of that run,
and ``accept-marks`` turns one marker's ``valid`` verdicts into the set's accepted alternatives,
which are part of its key.  ``import-blind-key`` maps the old blind sheet's token positions once and
writes its verdicts as edit marks.  ``manifest`` prints what a manifest pins and, with the corpora
named, checks that they are still the ones it pinned and that every item rebuilds.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from tools.evaluation.convert import blind_key
from tools.evaluation.convert import v1 as convert_v1
from tools.evaluation.core.formats import FormatError
from tools.evaluation.core.paths import workspace_path
from tools.evaluation.input import testset as testset_module
from tools.evaluation.input.corpora import adapter_class, names
from tools.evaluation.input.testset import Corpora, corpus_paths
from tools.evaluation.publish import check as check_module
from tools.evaluation.report import spot_check
from tools.evaluation.report.load import LoadedRun, load_run
from tools.evaluation.run import protocol
from tools.evaluation.store import accept, layout, reader, writer
from tools.evaluation.store import marks as marks_module

DEFAULT_STORE = "store"
"""Where the local store is, below the repository, unless a command names another."""

DEFAULT_OUT = "evaluations/proofreading"
"""Where the committed runs are, below the repository."""


# ── the one-off conversion ───────────────────────────────────────────────────


def _convert(args) -> int:
    """Convert the old store into the new one; return the exit code."""
    try:
        report = convert_v1.convert(
            workspace_path(args.old_root),
            workspace_path(args.store),
            manifests=_manifests(args),
            folders=args.folder or convert_v1.IN_SCOPE,
            instructions=None if args.no_instructions else _instructions_hook(),
            assume_machine=args.assume_machine,
            skip=args.skip,
        )
    except (ValueError, FormatError, OSError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print(report.summary())
    return 0


def _manifests(args) -> dict[str, Path]:
    """Return the committed manifest of each test set to convert, keyed by its id.

    Raises:
        SystemExit: if a ``--manifest`` is not ``NAME=PATH``.
    """
    found: dict[str, Path] = {}
    for text in args.manifest:
        name, separator, path = text.partition("=")
        if not separator or not name or not path:
            print(f"refused: --manifest wants NAME=PATH, not {text!r}", file=sys.stderr)
            raise SystemExit(2)
        found[name] = workspace_path(path)
    return found


def _instructions_hook() -> convert_v1.InstructionsHash:
    """Return the converter's instructions hook, from the run layer's own hash function."""
    return convert_v1.instructions_hook(protocol.instructions_sha256)


# ── the check ────────────────────────────────────────────────────────────────


def _validate(args) -> int:
    """Check the store's files, or the run files named; return the exit code.

    ``0`` when everything passed, ``1`` when anything was refused, and ``2`` when there was nothing
    to check: a mistyped path is not a clean store.
    """
    corpora = _corpora(args)
    root = workspace_path(args.store)
    if args.runs:
        checked = check_module.check_files(
            [workspace_path(text) for text in args.runs], root, corpora
        )
    else:
        checked = check_module.check_store(root, out=workspace_path(args.out), corpora=corpora)
    if not checked:
        print(f"nothing to check in {root}", file=sys.stderr)
        return 2
    for one in checked:
        _say(one)
    return 0 if all(one.ok for one in checked) else 1


def _say(one: check_module.Checked) -> None:
    """Print what checking one file found: that it is fine, or what is wrong with it."""
    for note in one.notes:
        print(f"note {one.name}: {note}")
    if one.ok:
        print(f"ok {one.name} ({one.counted})")
        return
    print(f"refused {one.name}", file=sys.stderr)
    for problem in one.problems:
        print(f"  {problem}", file=sys.stderr)


# ── the marks ────────────────────────────────────────────────────────────────


def _marks(args) -> int:
    """Write the marks of a filled-in sheet next to the run; return the exit code."""
    root = workspace_path(args.store)
    sheet = workspace_path(args.sheet)
    try:
        text = sheet.read_text(encoding="utf-8")
        header = spot_check.header_of(text)
        found = _run_of_key(root, header.run_key)
    except (OSError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    if found is None:
        print(f"refused: no run of key {header.run_key!r} in {root}", file=sys.stderr)
        return 1
    drawn = spot_check.sample(found) if header.kind == spot_check.EXPLANATIONS else None
    try:
        if drawn is None:
            found_marks = spot_check.marks_from_extra_sheet(
                text, spot_check.extra_edits_sample(found)
            )
        else:
            found_marks = spot_check.marks_from_sheet(text, drawn)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    question = marks_module.question_of(root, found.header.input.testset)
    path = layout.marks_path(root, args.marker, question)
    change = writer.merge_marks(path, found_marks)
    for line in change.lines():
        print(line)
    if drawn is not None and (left := spot_check.unmarked(drawn, found_marks)):
        print(f"{len(left)} of the sample are not marked yet")
    return 0


def _run_of_key(root: Path, run_key: str) -> LoadedRun | None:
    """Return the run of the store whose run key is *run_key*, or ``None`` when there is none.

    A sheet names the run key, not the file name: a renamed file is the same run, and the name is
    only there for people to read.
    """
    path = reader.run_index(root).get(run_key)
    if path is None:
        return None
    return load_run(root, reader.StoredRun(path=path, run=reader.read_run(path)))


def _import_blind_key(args) -> int:
    """Write the old blind sheet's verdicts as edit marks; return the exit code."""
    root = workspace_path(args.store)
    try:
        imported = blind_key.import_blind_key(
            root,
            args.testset,
            workspace_path(args.key),
            workspace_path(args.sheet),
            args.marker,
        )
    except (OSError, ValueError, FormatError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    for line in imported.lines():
        print(line)
    return 0


def _accept_marks(args) -> int:
    """Write one marker's accepted alternatives into the key of a test set; return the exit code."""
    root = workspace_path(args.store)
    try:
        accepted = accept.accept_marks(root, args.testset, args.marker)
    except (KeyError, ValueError, FormatError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    for line in accepted.lines():
        print(line)
    return 0


# ── the manifest ─────────────────────────────────────────────────────────────


def _manifest(args) -> int:
    """Print what a manifest pins, and check it against the corpora; return the exit code."""
    path = workspace_path(args.manifest)
    try:
        pinned = testset_module.read_manifest(path)
    except (OSError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print(f"test set {pinned.testset}, built {pinned.built}")
    print(f"  {len(pinned.items)} items, selection seed {pinned.selection_seed}")
    print(f"  builder revision {pinned.builder_revision}, key version {pinned.key_version}")
    for name, record in sorted(pinned.corpora.items()):
        print(f"  corpus {name}: {record['licence']}, {len(record['files'])} files pinned")
    corpora = _corpora(args)
    if corpora is None:
        return 0
    problems = testset_module.corpus_problems(pinned, corpora)
    problems += testset_module.check_items(pinned, corpora)
    if not problems:
        print("ok: the corpora are the ones the manifest pinned, and every item rebuilds")
        return 0
    print("refused", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


# ── the argument parser ──────────────────────────────────────────────────────


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
        prog="store",
        description="Convert, check and mark the store of proofreading runs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _convert_command(sub)
    _validate_command(sub)
    _marks_command(sub)
    _accept_command(sub)
    _blind_key_command(sub)
    _manifest_command(sub)
    return parser


def _convert_command(sub) -> None:
    """Add the subcommand that converts the old store once."""
    converter = sub.add_parser(
        "convert", help="convert the old results-* folders and test sets into the new store"
    )
    converter.add_argument(
        "--old-root", required=True, help="the old store's directory, e.g. ~/gec-testset"
    )
    _store_arguments(converter)
    converter.add_argument(
        "--manifest",
        action="append",
        default=[],
        required=True,
        metavar="NAME=PATH",
        help="the committed manifest of a test set to convert, repeatable",
    )
    converter.add_argument(
        "--folder",
        action="append",
        default=[],
        help="an old run folder to convert, repeatable (default: every folder in scope)",
    )
    converter.add_argument(
        "--assume-machine",
        default="",
        help="a machine to give a run whose own header names none, when the evidence agrees",
    )
    converter.add_argument(
        "--skip",
        action="append",
        default=[],
        help="an old run file to leave out, by name or by a path below --old-root, repeatable",
    )
    converter.add_argument(
        "--no-instructions",
        action="store_true",
        help="do not split the stored instructions hash per language (it is then left empty)",
    )
    converter.set_defaults(handler=_convert)


def _validate_command(sub) -> None:
    """Add the subcommand that checks the store and the committed runs."""
    validator = sub.add_parser("validate", help="check the store's files and the committed runs")
    _store_arguments(validator)
    _out_arguments(validator)
    validator.add_argument("runs", nargs="*", help="single run files, instead of the whole store")
    _corpora_arguments(validator)
    validator.set_defaults(handler=_validate)


def _marks_command(sub) -> None:
    """Add the subcommand that writes the marks of a filled-in sheet."""
    marked = sub.add_parser("marks", help="write the marks of a filled-in sheet")
    marked.add_argument("sheet", help="the sheet, with its Mark or Value column filled in")
    marked.add_argument(
        "--marker",
        required=True,
        help="who marked the sheet: the name or initials that go into the repository",
    )
    _store_arguments(marked)
    marked.set_defaults(handler=_marks)


def _accept_command(sub) -> None:
    """Add the subcommand that accepts one marker's verdicts."""
    accepted = sub.add_parser(
        "accept-marks", help="turn one marker's valid verdicts into the set's accepted alternatives"
    )
    accepted.add_argument("--testset", required=True, help="the test set the marks are about")
    accepted.add_argument("--marker", required=True, help="the marker whose verdicts are accepted")
    _store_arguments(accepted)
    accepted.set_defaults(handler=_accept_marks)


def _blind_key_command(sub) -> None:
    """Add the subcommand that imports the old blind sheet's key."""
    importer = sub.add_parser(
        "import-blind-key",
        help="import the old blind sheet's key as edit marks, mapping its token positions once",
    )
    importer.add_argument("key", help="the sheet's key file, with token positions")
    importer.add_argument("sheet", help="the filled-in blind sheet")
    importer.add_argument("--marker", required=True, help="the marker the marks belong to")
    importer.add_argument("--testset", required=True, help="the test set the sheet was drawn from")
    _store_arguments(importer)
    importer.set_defaults(handler=_import_blind_key)


def _manifest_command(sub) -> None:
    """Add the subcommand that prints and checks a manifest."""
    manifests = sub.add_parser("manifest", help="print what a manifest pins, and check it")
    manifests.add_argument("--manifest", required=True, help="the manifest to read")
    _corpora_arguments(manifests)
    manifests.set_defaults(handler=_manifest)


def main(argv: list[str] | None = None) -> int:
    """Run the tool; return the exit code."""
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
