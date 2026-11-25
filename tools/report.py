"""Read the store of evaluation records: the tables, the comparisons, the gates, the sheets, the page.

Developer tool, offline.  Every number it prints comes from :mod:`tools.evaluation.report`, and the
page it writes comes from :mod:`tools.evaluation.report.viewer`; this file only parses arguments and
prints.  Every command reads the store, which holds the model's output, the texts and the marks.

Usage (``$EVALS`` is the clone of ``teachers-teammate-evals``; every command needs ``--store``)::

    bazel run //tools:report -- table --store $EVALS --performance
    bazel run //tools:report -- table --store $EVALS --tiers
    bazel run //tools:report -- table --store $EVALS --by-error category
    bazel run //tools:report -- compare --store $EVALS --along temperature
    bazel run //tools:report -- compare --store $EVALS --along model \\
        --where exchange=text-first --baseline gemma4:12b
    bazel run //tools:report -- gate --store $EVALS --model gemma4:12b
    bazel run //tools:report -- passes --store $EVALS
    bazel run //tools:report -- sheet --store $EVALS --run RUN_NAME
    bazel run //tools:report -- page --store $EVALS --report-out ~/gec-testset/reports/x

``table`` prints the results table; ``--performance`` adds the speed and memory table, ``--tiers``
the fit and page time per tier (the first two criteria of ``evaluations/README.md``),
``--catalog-lines`` the ``answer_length=`` lines the catalog wants, ``--by-error`` how many errors of
each kind every model found, and ``--false-alarms`` what kind of change each change the reference
does not make is.  ``--count-marks`` counts the Markdown marks a model added as changes, and
``--no-accepted`` scores without the key's accepted alternatives.

``compare`` is the one comparison: ``--along`` names the axes that vary, ``--where`` fixes the rest,
and ``--baseline`` names the value the differences are measured against.  A sweep by temperature is
``--along temperature``; a comparison of models is ``--along model``.

``gate`` judges one model against the floors of gate 2, ``passes`` says what a second and a third
pass of each configuration would add, and ``sheet`` prints the sheet of one run for a teacher to
mark: the sample is the run's, so every machine draws the same one, and ``--extra-edits`` prints the
blind sheet of the changes the reference does not make instead of the explanations.

``page`` writes the static report folder: one page with the texts, the variants and the numbers.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
import sys

from tools.evaluation.core.axes import AXIS_NAMES
from tools.evaluation.core.formats import FormatError
from tools.evaluation.core.machines import known_machines
from tools.evaluation.core.modes import Exchange, Step, exchange_of, steps_of
from tools.evaluation.core.paths import store_argument, workspace_path
from tools.evaluation.core.sizes import SIZES_PATH, FitRecord, FitStore
from tools.evaluation.report import compare as compare_module
from tools.evaluation.report import gates, groups, load, select, spot_check
from tools.evaluation.report.load import Loaded, LoadedRun
from tools.evaluation.report.score import ScoreOptions
from tools.evaluation.report.summary import summarise
from tools.evaluation.report.tables import by_error, false_alarms, fit, models, passes, performance
from tools.evaluation.report.viewer import model as viewer_model
from tools.evaluation.report.viewer import page as viewer_page
from tools.evaluation.run.defaults import temperature_for
from tools.evaluation.store import layout, reader
from tools.evaluation.store import marks as marks_module

# ── the results table ────────────────────────────────────────────────────────


def _table(args) -> int:
    """Print the results table of the store; return the exit code."""
    try:
        items, skipped = _items_of(args)
    except (ValueError, FormatError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    _say_skipped(skipped)
    if not items:
        print("no run to report", file=sys.stderr)
        return 1
    summaries = summarise(items)
    print(models.model_table(summaries))
    print()
    print(models.band_table(summaries))
    print()
    print(models.loss_lines(items))
    timings = (
        performance.summarise_performance(items)
        if args.performance or args.tiers or args.catalog_lines
        else []
    )
    if args.performance:
        print()
        print(performance.performance_table(timings))
    if args.tiers:
        print()
        machines = known_machines(args.store)
        print(fit.tier_table(fit.summarise_tiers(_sizes(args), timings, machines)))
    if args.catalog_lines:
        print()
        print(performance.catalog_lines(timings))
    if args.by_error:
        print()
        print(by_error.error_table(items, by=args.by_error))
    if args.false_alarms:
        print()
        print(false_alarms.false_alarm_table(items))
    return 0


def _items_of(args) -> tuple[tuple, tuple]:
    """Return the scored items a report reads, and what was skipped."""
    loaded = load.load(
        args.store,
        testsets=args.testset or None,
        folders=args.folder or None,
        cache=_cache_of(args),
        options=ScoreOptions(count_marks=args.count_marks, accepted=not args.no_accepted),
        progress=_say_scoring,
    )
    return loaded.items, loaded.skipped


def _sizes(args) -> tuple[FitRecord, ...]:
    """Return the model-fit records of the store, and none when the store has no such file."""
    return FitStore(args.store / SIZES_PATH).records()


def _cache_of(args) -> Path:
    """Return where the score cache lives: the one named, else the store's own ``cache/``."""
    return args.cache if args.cache is not None else layout.cache_dir(args.store)


# ── the comparison ───────────────────────────────────────────────────────────


def _compare(args) -> int:
    """Compare the groups along an axis, beyond the noise; return the exit code."""
    chosen = _chosen(args)
    if chosen is None:
        return 2
    try:
        along = compare_module.Along(
            axes=compare_module.axes_of(args.along),
            where=compare_module.where_of(args.where or ()),
            baseline=args.baseline,
        )
        comparison = compare_module.compare(chosen, along=along, corrections=args.corrections)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(compare_module.comparison_table(comparison))
    return 0


# ── the gate and the passes ──────────────────────────────────────────────────


def _gate(args) -> int:
    """Judge one model against the floors of gate 2; return the exit code."""
    args.temperature = _temperature_of(args)
    chosen = _chosen(args)
    if chosen is None:
        return 2
    if len(chosen) != 1:
        print(
            f"refused: {len(chosen)} groups of {args.model!r} match; name the exchange, the "
            "steps or the temperature",
            file=sys.stderr,
        )
        return 2
    group = chosen[0]
    judgement = gates.GateTwoRule().judge(group, _marks_of_group(args, group))
    print(f"# Gate 2: `{group.key.model}` at temperature {group.key.temperature:g}")
    print()
    print(f"{len(group.runs)} repeats, {len(group.items[0])} texts")
    print()
    for line in judgement.lines():
        print(line)
    return 0 if judgement.outcome in {gates.Outcome.PASS, gates.Outcome.PASS_PENDING} else 1


def _temperature_of(args) -> float:
    """Return the temperature a gate reads: the one given, or the model's own recommendation.

    A model is run at the temperature its maker recommends, which differs between models, so a gate
    that assumed one value would miss every model run at its own.  The recommendation is the app's
    knowledge and lives in :mod:`tools.evaluation.run.defaults`, which this layer may not import, so
    the command line resolves it and passes the value in.
    """
    if args.temperature is not None:
        return args.temperature
    return temperature_for(args.model)


def _marks_of_group(args, group: groups.RunGroup) -> list:
    """Return every marker's marks on the group's test set.

    Gate 2 has a floor on the spot check, so it cannot be judged without the marks.
    """
    root = args.store
    return marks_module.marks_on(root, marks_module.question_of(root, group.key.testset))


def _passes(args) -> int:
    """Print what a second and a third pass of each configuration would add; return the exit code."""
    chosen = _chosen(args)
    if chosen is None:
        return 2
    try:
        rows = passes.pass_rows(chosen)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if not rows:
        print("no configuration has two repeats: there is no vote to take", file=sys.stderr)
        return 1
    print(passes.passes_table(rows))
    return 0


# ── the sheet ────────────────────────────────────────────────────────────────


def _sheet(args) -> int:
    """Print the sheet of one run of the store; return the exit code."""
    root = args.store
    try:
        found = _run_of(root, args.run)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    if found is None:
        print(f"refused: no run called {args.run!r} in {root}", file=sys.stderr)
        return 1
    if args.extra_edits:
        drawn_edits = spot_check.extra_edits_sample(found)
        if not drawn_edits.edits:
            print(f"refused: {args.run} makes no change the reference does not", file=sys.stderr)
            return 1
        print(spot_check.extra_edits_sheet(found))
        return 0
    drawn = spot_check.sample(found)
    if not drawn.notes:
        print(f"refused: {args.run} has no explained page-band text to sample", file=sys.stderr)
        return 1
    print(spot_check.sheet(drawn, {item.text: item for item in found.items}))
    return 0


def _run_of(root: Path, name: str) -> LoadedRun | None:
    """Return the run of the store whose file is called *name*, or ``None`` when there is none.

    The name is what a person types; the run is found through the store's index, which reads one
    line of each run file, and only that run is scored.
    """
    for path in reader.run_index(root).values():
        if path.name == name:
            return load.load_run(root, reader.StoredRun(path=path, run=reader.read_run(path)))
    return None


# ── the page ─────────────────────────────────────────────────────────────────


def _page(args) -> int:
    """Write the report folder the arguments ask for and print what it holds; return the exit code."""
    # Every answer is scored again from its text, which takes a while on a large store.
    print(f"reading {args.store} ...", file=sys.stderr, flush=True)
    loaded = load.load(
        args.store,
        testsets=args.testset or None,
        folders=args.folder or None,
        cache=_cache_of(args),
        progress=lambda name: print(f"  {name}", file=sys.stderr, flush=True),
    )
    if args.languages:
        loaded = _only_languages(loaded, _codes(args.languages))
    if args.model:
        loaded = _only_models(loaded, set(args.model))
    built = viewer_model.build(loaded)
    out = viewer_page.build(built, args.report_out, single_file=args.single_file, zip_it=args.zip)
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


def _only_languages(loaded: Loaded, codes: Sequence[str]) -> Loaded:
    """Return *loaded* with only the texts of *codes*, and the runs that answered one."""
    wanted = set(codes)
    testsets = {
        name: tuple(record for record in records if record.language in wanted)
        for name, records in loaded.testsets.items()
    }
    runs: list[LoadedRun] = []
    for run in loaded.runs:
        items = tuple(item for item in run.items if item.language in wanted)
        if items:
            runs.append(replace(run, items=items))
    return replace(
        loaded,
        runs=tuple(runs),
        testsets={name: records for name, records in testsets.items() if records},
    )


def _only_models(loaded: Loaded, wanted: set[str]) -> Loaded:
    """Return *loaded* with only the runs of the models in *wanted*."""
    return replace(
        loaded, runs=tuple(run for run in loaded.runs if run.header.protocol.model in wanted)
    )


# ── what every gate-2 command reads ──────────────────────────────────────────


def _chosen(args) -> list[groups.RunGroup] | None:
    """Return the groups a gate-2 command reads, or ``None`` after a refusal."""
    try:
        loaded = _loaded(args)
    except (ValueError, FormatError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return None
    _say_skipped(loaded.skipped)
    try:
        series, chosen = select.series_of(
            loaded.runs,
            model=getattr(args, "model", None),
            machine=getattr(args, "machine", None),
            testset=getattr(args, "testset", None),
        )
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return None
    if not getattr(args, "machine", None) or not getattr(args, "testset", None):
        print(series.describe(), file=sys.stderr)
    found = select.narrow(chosen, _narrowing(args))
    if not found:
        print("refused: no group matches the selection", file=sys.stderr)
        return None
    return found


def _narrowing(args) -> select.Narrowing:
    """Return what the command line narrows the store's groups to."""
    return select.Narrowing(
        model=getattr(args, "model", None),
        temperature=getattr(args, "temperature", None),
        exchange=getattr(args, "exchange", None),
        steps=getattr(args, "steps", None),
        digest=getattr(args, "digest", None),
        compact_json=getattr(args, "compact_json", None),
    )


def _loaded(args) -> Loaded:
    """Return the store *args* names, as a report reads it."""
    return load.load(
        args.store,
        testsets=[args.testset] if args.testset else None,
        cache=_cache_of(args),
        progress=_say_scoring,
    )


def _say_scoring(name: str) -> None:
    """Say which run is being scored, which takes a while."""
    print(f"scoring {name}", file=sys.stderr)


def _say_skipped(skipped: Sequence) -> None:
    """Say which files a report could not use, and why."""
    for skip in skipped:
        print(f"skipped {skip.file}: {skip.reason}", file=sys.stderr)


# ── the argument parser ──────────────────────────────────────────────────────


def _store_arguments(target) -> None:
    """Add the option that names the store to *target*."""
    target.add_argument(
        "--store",
        type=store_argument,
        required=True,
        help="the store of evaluation records, a clone of teachers-teammate-evals",
    )


def _score_arguments(target) -> None:
    """Add the options that change how an answer is scored."""
    target.add_argument(
        "--count-marks",
        action="store_true",
        help="count the Markdown marks a model added as changes (default: ignore them)",
    )
    target.add_argument(
        "--no-accepted",
        action="store_true",
        help="score without the key's accepted alternatives, to see the figures without them",
    )


def _filter_arguments(target) -> None:
    """Add the options that narrow a report to some sets or folders."""
    target.add_argument(
        "--testset", action="append", default=[], help="a test set to read, repeatable"
    )
    target.add_argument(
        "--folder", action="append", default=[], help="a run folder to read, repeatable"
    )


def _selection_arguments(target, *, model_required: bool, temperature_help: str) -> None:
    """Add the options that select the runs of one model to *target*."""
    target.add_argument(
        "--model",
        required=model_required,
        help="the model tag to judge" if model_required else "the model tag to read",
    )
    target.add_argument("--temperature", type=float, default=None, help=temperature_help)
    target.add_argument(
        "--exchange",
        type=exchange_of,
        default=None,
        choices=list(Exchange),
        help="the exchange of the runs, when the store holds more than one",
    )
    target.add_argument(
        "--steps",
        type=_steps,
        default=None,
        metavar="LIST",
        help="the steps of the exchange, comma-separated, when the store holds more than one",
    )
    target.add_argument(
        "--machine",
        default=None,
        help="the machine of the runs, or the provider of an online service "
        "(default: the machine of the newest run, which is printed)",
    )
    target.add_argument(
        "--testset",
        default=None,
        help="the test set of the runs (default: the set of the newest run, which is printed)",
    )
    target.add_argument("--digest", help="the model digest, when two are committed")
    target.add_argument(
        "--compact-json",
        choices=["on", "off"],
        default=None,
        help="the compact-JSON rule of the request profile, when two are committed",
    )
    target.add_argument(
        "--cache",
        type=workspace_path,
        help="where the score cache lives (default: the store's cache/)",
    )
    _store_arguments(target)


def _steps(text: str) -> tuple[Step, ...]:
    """Return the steps of a ``--steps correct,explain`` list, or refuse it for argparse."""
    try:
        return steps_of([part.strip() for part in text.split(",") if part.strip()])
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the tool."""
    parser = argparse.ArgumentParser(
        prog="report",
        description="Read a store of proofreading runs: tables, comparisons, gates, sheets, page.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _table_command(sub)
    _compare_command(sub)
    _gate_command(sub)
    _passes_command(sub)
    _sheet_command(sub)
    _page_command(sub)
    return parser


def _table_command(sub) -> None:
    """Add the subcommand that prints the results table."""
    reporter = sub.add_parser("table", help="print the results table of the store")
    _store_arguments(reporter)
    _filter_arguments(reporter)
    _score_arguments(reporter)
    reporter.add_argument(
        "--cache",
        type=workspace_path,
        help="where the score cache lives (default: the store's cache/)",
    )
    reporter.add_argument(
        "--performance", action="store_true", help="print the speed and memory table as well"
    )
    reporter.add_argument(
        "--tiers",
        action="store_true",
        help="print fit and page time per tier, from the measured sizes and the runs",
    )
    reporter.add_argument(
        "--catalog-lines",
        action="store_true",
        help="print the answer_length= lines the catalog wants, one per model, ready to paste",
    )
    reporter.add_argument(
        "--by-error",
        choices=list(by_error.BY_CHOICES),
        default=None,
        help="also print how many errors of each kind every model found",
    )
    reporter.add_argument(
        "--false-alarms",
        action="store_true",
        help="also print what kind of change each false alarm is",
    )
    reporter.set_defaults(handler=_table)


def _compare_command(sub) -> None:
    """Add the subcommand that compares the groups along an axis."""
    compared = sub.add_parser("compare", help="compare the groups along an axis, beyond the noise")
    _selection_arguments(
        compared,
        model_required=False,
        temperature_help="the temperature of the runs (default: every temperature stored)",
    )
    compared.add_argument(
        "--along",
        default="model",
        help="the axes that vary, comma-separated (default: %(default)s): "
        + ", ".join([*AXIS_NAMES, *sorted(groups.ENVIRONMENT_AXES)]),
    )
    compared.add_argument(
        "--where",
        action="append",
        default=[],
        metavar="AXIS=VALUE",
        help="a value the comparison fixes, repeatable",
    )
    compared.add_argument(
        "--baseline", default=None, help="the value of the first axis the differences are against"
    )
    compared.add_argument(
        "--corrections",
        action="store_true",
        help="compare the corrections only: the exchange and correct-only modes count as one",
    )
    compared.set_defaults(handler=_compare)


def _gate_command(sub) -> None:
    """Add the subcommand that judges one model against gate 2."""
    gate = sub.add_parser("gate", help="judge one model against the floors of gate 2")
    _selection_arguments(
        gate,
        model_required=True,
        temperature_help="the temperature of the runs "
        "(default: the one the model's maker recommends, as the app sends it)",
    )
    gate.set_defaults(handler=_gate)


def _passes_command(sub) -> None:
    """Add the subcommand that says what a second and a third pass would add."""
    passer = sub.add_parser(
        "passes",
        help="print what a second and a third pass of each configuration would add",
    )
    _store_arguments(passer)
    passer.add_argument("--testset", default=None, help="the test set of the runs")
    passer.add_argument(
        "--cache",
        type=workspace_path,
        help="where the score cache lives (default: the store's cache/)",
    )
    passer.set_defaults(handler=_passes)


def _sheet_command(sub) -> None:
    """Add the subcommand that prints the sheet of one run."""
    sheeter = sub.add_parser("sheet", help="print the sheet of a run, for a teacher to mark")
    sheeter.add_argument("--run", required=True, help="the run's file name")
    sheeter.add_argument(
        "--extra-edits",
        action="store_true",
        help="the blind sheet of the changes the reference does not make, instead of the "
        "explanations",
    )
    _store_arguments(sheeter)
    sheeter.set_defaults(handler=_sheet)


def _page_command(sub) -> None:
    """Add the subcommand that writes the static report folder."""
    build = sub.add_parser("page", help="write the report folder: texts, variants and numbers")
    build.add_argument(
        "--store",
        type=store_argument,
        required=True,
        help="the store of evaluation records, a clone of teachers-teammate-evals",
    )
    build.add_argument(
        "--report-out", type=workspace_path, required=True, help="the folder to write"
    )
    build.add_argument("--zip", action="store_true", help="also write <report-out>.zip")
    build.add_argument(
        "--single-file", action="store_true", help="write one report.html instead of a folder"
    )
    build.add_argument(
        "--model", action="append", default=[], help="only these models; repeat for several"
    )
    build.add_argument("--languages", default="", help="only these languages, comma separated")
    build.add_argument(
        "--testset", action="append", default=[], help="only these test sets; repeat for several"
    )
    build.add_argument(
        "--folder", action="append", default=[], help="only these run folders; repeat for several"
    )
    build.add_argument(
        "--cache",
        type=workspace_path,
        help="where the score cache lives, so a rebuild is quick (default: the store's cache/)",
    )
    build.set_defaults(handler=_page)


def main(argv: list[str] | None = None) -> int:
    """Run the tool; return the exit code."""
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
