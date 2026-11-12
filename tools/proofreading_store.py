"""Keep proofreading runs in the repository: export, validate, index.

Developer tool, not run in CI.  A local run writes result rows with the
source, the reference and the model's output in them (`~/.local/share/...`, or wherever
``compare_writing_models run --out`` wrote them); the corpora are licensed and are not part of this
repository.  This tool turns such a run into a *committed* one: the model's edits as positions plus
short replacements, the notes as categories plus capped explanations, the outcomes as numbers, and
the facts that let someone check and re-score it later (manifest hash, instructions hash, model
digest, seed, machine).

The work lives in :mod:`tools.model_eval.export` (a cache becomes a run file),
:mod:`tools.model_eval.validation` (the two modes of checking one), :mod:`tools.model_eval.index`
(``INDEX.md``), :mod:`tools.model_eval.layout` (where the files go) and
:mod:`tools.model_eval.testset`; this file only parses arguments and prints.

Usage::

    bazel run //tools:proofreading_store -- export \\
        --cache ~/gec-testset/results --testset gec-2026-12 \\
        --manifest evaluations/proofreading/testsets/gec-2026-12/manifest.json \\
        --corpora ~/gec-testset/corpora.json \\
        --machine rtx3070-laptop --out evaluations/proofreading

    bazel run //tools:proofreading_store -- validate --out evaluations/proofreading
    bazel run //tools:proofreading_store -- index --out evaluations/proofreading
    bazel run //tools:proofreading_store -- report --out evaluations/proofreading --performance
    bazel run //tools:proofreading_store -- report --cache ~/gec-testset/results \\
        --testset ~/gec-testset/set --by-error category
    bazel run //tools:proofreading_store -- sheet --run RUN_ID --cache ~/gec-testset/results

``validate`` checks the committed files with the caps that need no corpus (what the test suite
runs); give it the corpora as well (``--corpora FILE`` or ``--corpus NAME=PATH``) and it also
applies the five-word rule and the hashes against the real text.  ``index`` regenerates
``INDEX.md``.  ``report`` prints the results table of the committed runs, or with ``--cache`` of
the rows a ``compare_writing_models run`` wrote locally (``--by-error`` groups the reference
errors by kind and needs ``--testset``, a test set built with the corpus labels).  ``sheet``
prints the explanation sheet of one committed run: the sample is the run's, so every machine draws
the same one, and the source sentences come from the local cache.  A teacher marks each
explanation, and ``marks`` turns the filled-in sheet into the marks file of that run.  ``manifest`` prints what a manifest
pins and, with the corpora named, checks that they are still the ones it pinned and that every
item rebuilds; a manifest built from a dirty tree is a problem there too, so accepting a
committed set is one command.
"""

from __future__ import annotations

from pathlib import Path
import sys

from teachers_teammate.domain import model_catalog
from tools.model_eval import (
    export,
    gate_two,
    grouping,
    index,
    layout,
    report,
    results,
    run_format,
    selection,
    spot_check,
    validation,
)
from tools.model_eval.answer_mode import AnswerMode, parse_mode
from tools.model_eval.cli_paths import workspace_path
from tools.model_eval.testset import (
    Corpora,
    adapter_class,
    band_summary,
    check_items,
    corpus_paths,
    corpus_problems,
    names,
    read_manifest,
)


def _corpora(args) -> Corpora | None:
    """Return the corpora named on the command line, or ``None`` when none was given.

    ``None`` still means format mode for ``validate``: no corpus is read, so no strict check runs.
    """
    if not (args.corpus or args.corpora):
        return None
    try:
        pairs = [
            f"{name}={workspace_path(path)}" if separator else pair
            for pair in args.corpus
            for name, separator, path in [pair.partition("=")]
        ]
        paths = corpus_paths(pairs, workspace_path(args.corpora) if args.corpora else None)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    return Corpora(paths)


def _export(args) -> int:
    """Turn every run of the local cache into a committed run file; return the exit code."""
    corpora = _corpora(args)
    if corpora is None:
        raise SystemExit(
            "export needs the corpora the manifest names (--corpora FILE or --corpus NAME=PATH): "
            "the caps are checked against the real text, and the items are resolved to see they "
            "rebuild"
        )
    try:
        written = export.export(
            workspace_path(args.cache),
            testset=args.testset,
            manifest=workspace_path(args.manifest),
            corpora=corpora,
            machine=args.machine,
            out=workspace_path(args.out),
            label=args.label,
        )
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    if not written:
        print(f"no run in {args.cache} (rows and a .header.json per run are needed)")
        return 0
    for run in written:
        cut = f", {run.cut_fields} fields cut" if run.cut_fields else ""
        print(f"wrote {run.path} ({run.items} items{cut}, eval v{run.eval_version})")
    print(f"index: {workspace_path(args.out) / layout.INDEX_NAME}")
    return 0


def _validate(args) -> int:
    """Check the committed files; return the exit code (1 when a file is refused)."""
    out = workspace_path(args.out)
    corpora = _corpora(args)
    paths = [workspace_path(path) for path in args.runs] or layout.run_files(out)
    if not paths:
        print(f"no run files below {out}", file=sys.stderr)
        return 1
    refused = 0
    for path in paths:
        problems = validation.validate_run(path, corpora)
        if not problems:
            print(f"ok {path.name}")
            continue
        refused += 1
        print(f"refused {path.name}", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
    mode = "strict" if corpora is not None else "format"
    print(f"{mode} mode: {len(paths) - refused} of {len(paths)} run files pass")
    refused += _validate_marks(out)
    if corpora is not None:
        for path in paths:
            try:
                header = run_format.read_run(path).header
            except ValueError:
                continue  # unreadable: validate_run refused it above, with the reason
            pin = layout.manifest_for(path, header)
            if pin.exists():
                problems = corpus_problems(read_manifest(pin), corpora)
                if problems:
                    print(f"the corpora differ from {pin}: " + "; ".join(problems), file=sys.stderr)
                    refused += 1
    return 1 if refused else 0


def _index(args) -> int:
    """Regenerate ``INDEX.md``; return the exit code."""
    out = workspace_path(args.out)
    if args.check:
        expected = index.index_markdown(out)
        target = out / layout.INDEX_NAME
        if not target.exists():
            print(f"{target} is missing; run `proofreading_store index`", file=sys.stderr)
            return 1
        if target.read_text(encoding="utf-8") != expected:
            print(f"{target} is out of date; run `proofreading_store index`", file=sys.stderr)
            return 1
        print(f"ok {target.name}")
        return 0
    path = index.write_index(out)
    print(f"wrote {path} ({len(layout.run_files(out))} runs)")
    return 0


def _report_cache(args) -> int:
    """Print the results table of a local cache; return the exit code.

    The same table as the committed runs give, from the rows a run wrote (see :func:`_report`).
    """
    # The test set is optional for the table, but with it the band and the new numbers are
    # recomputed from the record's spans instead of being taken from the row.
    testset = workspace_path(args.testset) if args.testset else None
    records = results.read_records_by_id(testset) if testset else {}
    if args.count_marks and not args.testset:
        print(
            "--count-marks without --testset: GLEU and the recall by half are not shown, "
            "because the stored ones were computed without the marks",
            file=sys.stderr,
        )
    rows = [
        results.rescore(row, count_marks=args.count_marks, record=records.get(row.id))
        for row in results.read_all(workspace_path(args.cache))
    ]
    summaries = report.summarise(rows)
    print(report.table(summaries))
    if bands := report.band_table(summaries):
        print()
        print(bands)
    if args.performance:
        print()
        print(report.performance_table(report.summarise_performance(rows)))
    if args.by_error:
        if testset is None:
            print("\n--by-error needs --testset (the labels live in the test set)", file=sys.stderr)
            return 2
        print()
        print(report.error_table(rows, results.read_labels(testset), by=args.by_error))
    return 0


def _sheet(args) -> int:
    """Print the explanation sheet of one committed run, with the source sentences from a cache.

    The sample is the run's, so the sheet is the same on every machine; the source sentences come
    from the local cache, because the repository holds no corpus text.  The cache may hold several
    runs — the repeats of the model, and other models — so the rows are picked by the run's model,
    temperature and mode and by the output it stored.  A row whose output or explanation is not the
    one the run stored is refused: the sheet would show another text.
    """
    out = workspace_path(args.out)
    found = sorted((out / layout.RUNS_DIR).glob(f"*/{args.run}.jsonl"))
    if len(found) != 1:
        print(f"refused: {len(found)} runs are called {args.run!r} below {out}", file=sys.stderr)
        return 1
    run = run_format.read_run(found[0])
    pin = layout.manifest_for(found[0], run.header)
    if not pin.exists():
        print(f"refused: no manifest at {pin}", file=sys.stderr)
        return 1
    pinned = read_manifest(pin)
    items = run.items(pinned)
    drawn = spot_check.sample(run, items)
    rows = spot_check.rows_of_run(results.read_all(workspace_path(args.cache)), items)
    entries = {str(entry["id"]): entry for entry in pinned["items"]}
    try:
        print(spot_check.sheet(drawn, rows, {item.item: item for item in items}, entries))
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    return 0


def _report(args) -> int:
    """Print the results table of the given runs; return the exit code.

    The table is built from the typed items of the run files; with ``--cache`` it is built from the
    rows of a local cache instead (:func:`_report_cache`): the two must agree, which is what
    ``test_report_sources.py`` checks.  A file that fails format mode is named and skipped.
    """
    if getattr(args, "cache", None):
        return _report_cache(args)
    out = workspace_path(args.out)
    paths = [workspace_path(path) for path in args.runs] or layout.run_files(out)
    if not paths:
        print(f"no run files below {out}", file=sys.stderr)
        return 1
    items: list[run_format.StoredItem] = []
    measured: dict[str, tuple[int, str, str]] = {}
    refused = 0
    for path in paths:
        problems = validation.validate_run(path)
        if problems:
            refused += 1
            print(f"refused {path.name}", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            continue
        run = run_format.read_run(path)
        pin = layout.manifest_for(path, run.header)
        if not pin.exists():
            refused += 1
            print(f"refused {path.name}: no manifest at {pin}", file=sys.stderr)
            continue
        items.extend(run.items(read_manifest(pin)))
        header = run.header
        measured[path.name] = (
            header["eval_version"],
            header["testset"],
            header["manifest_sha256"],
        )
    if len(set(measured.values())) > 1:
        print(
            "refused: these runs were not measured the same way, so their scores are never "
            "compared; give runs of one eval version and one test set:",
            file=sys.stderr,
        )
        for name, (version, testset, _manifest) in sorted(measured.items()):
            print(f"  {name}: eval v{version}, test set {testset}", file=sys.stderr)
        return 1
    if items:
        summaries = report.summarise(items)
        print(report.table(summaries))
        if bands := report.band_table(summaries):
            print()
            print(bands)
        if args.performance:
            print()
            print(report.performance_table(report.summarise_performance(items)))
    return 1 if refused else 0


def _note_licences(manifest: dict, corpora: Corpora) -> None:
    """Print a note where a manifest's licence text differs from its adapter's.

    A note, not a problem: manifests are immutable, and a corrected licence wording must not make
    the old ones fail.
    """
    for name, entry in manifest["corpora"].items():
        try:
            adapter = corpora.adapter(name)
        except ValueError:
            continue
        if entry.get("licence") != adapter.LICENCE:
            print(f"  {name}: the licence text differs from the adapter's")


def _band_lines(manifest: dict) -> list[str]:
    """Return one line per language with its item count per band; none when no item has a band."""
    counts: dict[str, dict[str, int]] = {}
    for item in manifest["items"]:
        band = item.get("band")
        if band:
            counts.setdefault(item["language"], {})
            counts[item["language"]][band] = counts[item["language"]].get(band, 0) + 1
    lines = []
    for language, bands in counts.items():
        lines.append(f"  {language}: " + band_summary(bands))
    return lines


def _manifest(args) -> int:
    """Print what a manifest pins and whether the corpora still agree; return the exit code.

    With the corpora named, both checks run: the corpus files must still be the ones the manifest
    pinned, and every item must rebuild to the hashes it recorded.  A manifest built from a dirty
    tree is a problem too, because a set that is committed has to be reproducible from a commit.
    """
    manifest = read_manifest(workspace_path(args.manifest))
    revision = str(manifest.get("builder_revision", "?"))
    print(
        f"test set {manifest['testset']!r}, built {manifest.get('built', '?')}, "
        f"{len(manifest['items'])} items, builder {revision}"
    )
    for name, entry in manifest["corpora"].items():
        restricted = "restricted" if entry.get("restricted") else "common rule"
        print(f"  {name}: {restricted} — {entry.get('licence', '?')}")
    for line in _band_lines(manifest):
        print(line)
    corpora = _corpora(args)
    if corpora is not None:
        _note_licences(manifest, corpora)
    problems: list[str] = []
    if revision.endswith("+dirty"):
        problems.append(f"built from a dirty tree ({revision}): a committed set needs a clean one")
    if corpora is not None:
        problems.extend(corpus_problems(manifest, corpora))
        problems.extend(check_items(manifest, corpora))
    if problems:
        print(f"{args.manifest}: {len(problems)} problems", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    if corpora is not None:
        print("the corpora are the ones the manifest pinned")
    return 0


# ── gate 2: the floors, the comparison and the marks ──────────────────────────


def _selection(args) -> selection.Selection:
    """Return what a gate-2 command asks for, from its arguments."""
    return selection.Selection(
        model=args.model,
        against=getattr(args, "against", None),
        mode=parse_mode(args.mode),
        temperature=args.temperature,
        machine=args.machine,
        testset=args.testset,
        digest=args.digest,
        compact_json=None if args.compact_json is None else args.compact_json == "on",
    )


def _group_line(group: grouping.RunGroup) -> str:
    """Return the key of *group* as one line, so a record says what was measured."""
    key = group.key
    profile = ", ".join(f"{name} {value}" for name, value in key.request_profile) or "default"
    repeats = ", ".join(str(number) for number in group.repeats)
    return (
        f"`{key.model}` {key.digest[:12]}, {key.testset}, {key.machine}, {key.mode.value}, "
        f"temperature {key.temperature:g}, window {key.num_ctx}, profile {profile}, "
        f"eval v{key.eval_version}, repeats {repeats}"
    )


def _catalog_note(group: grouping.RunGroup) -> str | None:
    """Return a note when the catalog does not record the model's structured-output flag.

    A note, not a refusal: the gate-1 records are prose, so the flag of a model that passed gate 1
    is set by hand, and a model the catalog does not know has none at all.
    """
    if group.key.mode is not AnswerMode.REVIEW:
        return None
    spec = model_catalog.find_spec(group.key.model)
    if spec is not None and spec.structured_output:
        return None
    return (
        f"note: the catalog does not record `structured_output` for `{group.key.model}`; "
        "gate 1's verdict is not machine-readable, so the flag is set by hand"
    )


def _marks_of(out: Path, group: grouping.RunGroup) -> spot_check.Marks | None:
    """Return the marks of the group's first repeat, or ``None`` when nobody has marked them.

    The file is checked the way ``validate`` checks it, against the run it belongs to: a mark of
    another sample, or of an explanation that changed since, would judge the model on something it
    did not do, so a refused file counts as unmarked and its problems are printed.
    """
    run, _items = group.first()
    path = spot_check.marks_path(out, run.run_id)
    if not path.exists():
        return None
    problems = validation.validate_marks(path, out)
    if problems:
        print(f"note: {path.name} is refused; the explanations stay unmarked", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return None
    return spot_check.read_marks(path)


def _gate_code(outcome: gate_two.Outcome) -> int:
    """Return the exit code of a judgement: 0 for a pass, 1 for a fail, 2 for incomplete."""
    if outcome is gate_two.Outcome.INCOMPLETE:
        return 2
    return 0 if outcome is not gate_two.Outcome.FAIL else 1


def _compare_code(comparison: gate_two.Comparison) -> int:
    """Return the exit code of a comparison: 0 for better, 1 for a tie or worse, 2 otherwise."""
    if comparison is gate_two.Comparison.NOT_COMPARABLE:
        return 2
    return 0 if comparison is gate_two.Comparison.BETTER else 1


def _gate(args) -> int:
    """Judge one model against the floors of gate 2; return the exit code.

    The floors are the model's own numbers, so no other model is read: a model passes or fails on
    what it did, not on what another did.
    """
    out = workspace_path(args.out)
    try:
        (group,) = selection.select(out, _selection(args))  # pylint: disable=unbalanced-tuple-unpacking  # one group per model
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(f"# Gate 2: `{group.key.model}`")
    print()
    print(_group_line(group))
    if note := _catalog_note(group):
        print(note)
    judgement = gate_two.GateTwoRule().judge(group, _marks_of(out, group))
    print()
    for line in judgement.lines():
        print(line)
    return _gate_code(judgement.outcome)


def _compare(args) -> int:
    """Compare two models beyond the noise; return the exit code.

    The comparison is not a gate: it is used once, at the end of the screen, to fill each tier's
    places.  A model that is not better than the incumbent keeps its place.
    """
    out = workspace_path(args.out)
    try:
        first, second = selection.select(out, _selection(args))  # pylint: disable=unbalanced-tuple-unpacking  # one group per model
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(f"# Gate 2: `{first.key.model}` against `{second.key.model}`")
    print()
    print(_group_line(first))
    print(_group_line(second))
    result = gate_two.compare(first, second)
    print()
    for line in result.lines():
        print(line)
    return _compare_code(result.comparison)


def _run_of(out: Path, run_id: str) -> run_format.StoredRun | None:
    """Return the committed run called *run_id* below *out*, or ``None`` when there is none."""
    found = sorted((out / layout.RUNS_DIR).glob(f"*/{run_id}.jsonl"))
    return run_format.read_run(found[0]) if len(found) == 1 else None


def _marks(args) -> int:
    """Write the marks of a filled-in sheet next to the run; return the exit code.

    The sheet is checked against the run before anything is written: a row that names a note the
    run's sample does not hold, or a sheet of another run, is refused.
    """
    out = workspace_path(args.out)
    sheet = workspace_path(args.sheet)
    try:
        text = sheet.read_text(encoding="utf-8")
        run_id, _rows = spot_check.read_sheet(text)
    except (OSError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    run = _run_of(out, run_id)
    if run is None:
        print(f"refused: no run called {run_id!r} below {out}", file=sys.stderr)
        return 1
    pin = layout.manifest_for(run.path, run.header)
    if not pin.exists():
        print(f"refused: no manifest at {pin}", file=sys.stderr)
        return 1
    drawn = spot_check.sample(run, run.items(read_manifest(pin)))
    try:
        marks = spot_check.marks_from_sheet(text, drawn, marker=args.marker)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    path = spot_check.marks_path(out, run_id)
    spot_check.write_marks(path, marks)
    print(f"wrote {path} ({len(marks.marks)} marks)")
    if left := spot_check.unmarked(drawn, marks):
        print(f"{len(left)} of the sample are not marked yet")
    return 0


def _validate_marks(out: Path) -> int:
    """Check every marks file below *out* against its run; return the number refused."""
    refused = 0
    for path in sorted((out / spot_check.MARKS_DIR).glob("*.json")):
        problems = validation.validate_marks(path, out)
        if not problems:
            print(f"ok {path.name}")
            continue
        refused += 1
        print(f"refused {path.name}", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
    return refused


def _gate_commands(sub) -> None:
    """Add the gate-2 subcommands, which share the options that select the runs of a model."""

    def gate_arguments(target) -> None:
        """Add the options that select the runs of one model to *target*."""
        target.add_argument("--model", required=True, help="the model tag to judge")
        target.add_argument(
            "--mode",
            default=AnswerMode.REVIEW.value,
            help="the answer mode of the runs (default: %(default)s)",
        )
        target.add_argument(
            "--temperature",
            type=float,
            default=None,
            help="the temperature of the runs (default: the one each model was started with: its "
            "vendor's recommendation, else the app's 0.2)",
        )
        target.add_argument(
            "--machine", help="the machine of the runs (default: the newest run of --model)"
        )
        target.add_argument(
            "--testset", help="the test set of the runs (default: the newest run of --model)"
        )
        target.add_argument("--digest", help="the model digest, when two are committed")
        target.add_argument(
            "--compact-json",
            choices=["on", "off"],
            default=None,
            help="the compact-JSON rule of the request profile, when two are committed",
        )
        target.add_argument(
            "--out",
            default="evaluations/proofreading",
            help="the repository directory (default: %(default)s)",
        )

    gate = sub.add_parser("gate", help="judge one model against the floors of gate 2")
    gate_arguments(gate)
    gate.set_defaults(handler=_gate)

    compared = sub.add_parser("compare", help="compare two models beyond the noise")
    gate_arguments(compared)
    compared.add_argument("--against", required=True, help="the model to compare against")
    compared.set_defaults(handler=_compare)

    marked = sub.add_parser("marks", help="write the marks of a filled-in spot-check sheet")
    marked.add_argument("sheet", help="the sheet, with the Mark column filled in")
    marked.add_argument(
        "--out",
        default="evaluations/proofreading",
        help="the repository directory (default: %(default)s)",
    )
    marked.add_argument(
        "--marker",
        required=True,
        help="who marked the explanations: the name or initials that go into the repository",
    )
    marked.set_defaults(handler=_marks)


def build_parser():
    """Return the argument parser of the tool."""
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="proofreading_store",
        description="Export proofreading runs into the repository, check them, list them.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def corpora_arguments(target) -> None:
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
            type=Path,
            help="a JSON object {name: path}; a relative path in it is relative to the file",
        )

    exporter = sub.add_parser("export", help="write local runs as committed run files")
    exporter.add_argument("--cache", required=True, help="the local results directory")
    exporter.add_argument("--testset", required=True, help="the test-set id, e.g. gec-2026-12")
    exporter.add_argument("--manifest", required=True, help="the manifest of that test set")
    exporter.add_argument("--machine", required=True, help="id of the machine file `machines/`")
    exporter.add_argument(
        "--out",
        default="evaluations/proofreading",
        help="the repository directory (default: %(default)s)",
    )
    exporter.add_argument(
        "--label",
        help="replaces the r<n> suffix of the run id, for two runs of one configuration",
    )
    corpora_arguments(exporter)
    exporter.set_defaults(handler=_export)

    validator = sub.add_parser("validate", help="check committed run files")
    validator.add_argument(
        "--out",
        default="evaluations/proofreading",
        help="the repository directory (default: %(default)s)",
    )
    validator.add_argument("runs", nargs="*", help="single run files, instead of all of --out")
    corpora_arguments(validator)
    validator.set_defaults(handler=_validate)

    indexer = sub.add_parser("index", help="regenerate INDEX.md")
    indexer.add_argument(
        "--out",
        default="evaluations/proofreading",
        help="the repository directory (default: %(default)s)",
    )
    indexer.add_argument(
        "--check",
        action="store_true",
        help="do not write: fail when INDEX.md is missing or out of date",
    )
    indexer.set_defaults(handler=_index)

    reporter = sub.add_parser(
        "report", help="print the results table of committed runs, or of a local cache (--cache)"
    )
    reporter.add_argument(
        "--out",
        default="evaluations/proofreading",
        help="the repository directory (default: %(default)s)",
    )
    reporter.add_argument("runs", nargs="*", help="single run files, instead of all of --out")
    reporter.add_argument(
        "--performance",
        action="store_true",
        help="print the speed and memory table as well",
    )
    reporter.add_argument(
        "--cache",
        help="read the rows of this local results directory (what `compare_writing_models run` "
        "wrote) instead of the committed runs",
    )
    reporter.add_argument(
        "--testset",
        help="with --cache: the test set the results were made from; recomputes the band and the "
        "sentence figures from its spans and gives --by-error the corpus labels",
    )
    reporter.add_argument(
        "--by-error",
        choices=["category", "type"],
        default=None,
        help="with --cache: also print how many errors of each kind every model found, by the "
        "app's categories or by the corpus label's word type",
    )
    reporter.add_argument(
        "--count-marks",
        action="store_true",
        help="with --cache: count Markdown marks a model added (such as **bold**) as errors; by "
        "default they are ignored",
    )
    reporter.set_defaults(handler=_report)

    sheeter = sub.add_parser(
        "sheet", help="print the explanation sheet of a committed run, for a teacher to mark"
    )
    sheeter.add_argument("--run", required=True, help="the run id, as the file is named")
    sheeter.add_argument("--cache", required=True, help="the local results directory of that run")
    sheeter.add_argument(
        "--out",
        default="evaluations/proofreading",
        help="the repository directory (default: %(default)s)",
    )
    sheeter.set_defaults(handler=_sheet)

    _gate_commands(sub)

    manifests = sub.add_parser("manifest", help="print what a manifest pins, and check it")
    manifests.add_argument("--manifest", required=True, help="the manifest to read")
    corpora_arguments(manifests)
    manifests.set_defaults(handler=_manifest)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the tool; return the exit code."""
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
