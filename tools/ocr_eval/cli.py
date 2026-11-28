"""``ocr-eval``: read a page set with one engine, or read the runs back.

Four commands:

``run``      read every page of the set with one engine under one preset, and write the numbers
             to the store as one run;
``report``   one row per run and group, each with the difference to a chosen baseline run;
``validate`` check that the store's records hold numbers and names only;
``screen``   check the candidates on drawn pages and time what a drawn page costs, before any
             page of the set is spent on them.

A run needs the page set: a folder per group, ``s01.png`` beside ``s01.gt.txt``, and a
``pages.jsonl`` naming each page's writer and language.  It needs the machine id the store's
registry knows, because a page time belongs to one machine on one device.  The readings stay in a
local cache, so a second report reads nothing again.

Examples::

    ocr-eval run --store $EVALS --machine rx9070xt-server --pages ~/ocr-pages \\
        --model qwen3-vl:8b --preset R
    ocr-eval run --store $EVALS --machine rx9070xt-server --pages ~/ocr-pages \\
        --model reader --preset R-light --cpu-only
    ocr-eval report --store $EVALS --baseline qwen3-vl_8b--R--app--gpu--rx9070xt-server--1a2b3c

A run's name says what it is — model, preset, prompt, device, machine, and a hash of the rest — so
a second run of the same ask is refused rather than overwritten: ``--replace`` writes over it, and
the store is a git repository.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
import sys

from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain.data_location import ENGINE_PPOCR
from teachers_teammate.exceptions import ModelMissingError, OCRError, OllamaUnreachableError
from teachers_teammate.infrastructure.ollama_client import OllamaClient
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.messages import message_of
from tools.evaluation.core.formats import FormatError, read_jsonl
from tools.evaluation.core.machines import require_machine
from tools.evaluation.core.paths import store_argument, workspace_path
from tools.ocr_eval import candidates, prepare, presets, records, report, runner, runs, screen
from tools.ocr_eval.engine import DETECTOR, READER, Model, Reader
from tools.ocr_eval.readings import cache_root
from tools.ocr_eval.records import Totals, validate

BOTH = "both"
"""The screen's choice of timing a model with the app's prompt and with its card's."""

STORE_HELP = (
    "the store the records go to, a clone of teachers-teammate-evals: runs land in ocr/runs/ "
    "below it, and the machine registry is read from machines/ there"
)


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the tool."""
    parser = argparse.ArgumentParser(
        prog="ocr-eval",
        description="Read a page set with one reading engine, and compare runs of it.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _run_command(sub)
    _report_command(sub)
    _validate_command(sub)
    _screen_command(sub)
    return parser


def _run_command(sub) -> None:
    """Add the subcommand that reads a page set with one engine."""
    run = sub.add_parser("run", help="read the page set with one engine and record the numbers")
    run.add_argument("--store", type=store_argument, required=True, help=STORE_HELP)
    run.add_argument(
        "--pages",
        type=workspace_path,
        required=True,
        metavar="DIR",
        help="the page set: a folder per group with a transcription beside every page, and "
        "pages.jsonl naming every page's writer and language",
    )
    run.add_argument(
        "--model",
        required=True,
        metavar="TAG",
        help=f"an Ollama tag, or {READER!r} for the built-in reader",
    )
    run.add_argument(
        "--machine",
        required=True,
        metavar="ID",
        help="the machine this runs on, as the store's machines/ registry names it",
    )
    run.add_argument(
        "--preset",
        type=_preset,
        default=presets.AUTO,
        metavar="NAME",
        help=f"what the page is prepared as: {presets.RAW}, {presets.AUTO}, {presets.COLOUR}, or "
        f"{presets.AUTO}-<step> with one step of the automatic preparation left out "
        "(default %(default)s)",
    )
    run.add_argument(
        "--prompt",
        choices=records.PROMPTS,
        default=records.APP,
        help="ask with the app's own OCR prompt (default), or with the one the model's card "
        "names — where its card prompt is transcribed into candidates.py",
    )
    run.add_argument(
        "--detector",
        type=_detector,
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=f"one of the reader's detector numbers (repeatable): {', '.join(DETECTOR)}",
    )
    run.add_argument(
        "--cpu-only",
        action="store_true",
        help="keep the model off the graphics card (num_gpu 0), as the cpu machine id does",
    )
    run.add_argument(
        "--split",
        choices=records.SPLITS,
        default=records.ALL,
        help="read both sides of the split (default), or only the tune or the check side",
    )
    run.add_argument(
        "--cache",
        type=workspace_path,
        default=None,
        metavar="DIR",
        help="where the readings are kept (default: this tool's folder in the user's cache "
        "directory); a reading is the page's text, so it never goes into the store",
    )
    run.add_argument(
        "--language",
        default=runner.LANGUAGE,
        help="the language a page's prompt names when pages.jsonl names none (default %(default)s)",
    )
    run.add_argument("--url", default="", metavar="URL", help="the Ollama server's address")
    run.add_argument(
        "--timeout",
        type=float,
        default=runner.TIMEOUT,
        help="how long one page's whole answer may take (default %(default)s s)",
    )
    run.add_argument(
        "--folder",
        default="",
        metavar="NAME",
        help="group this run inside the store's ocr/runs/ folder, one folder per campaign",
    )
    run.add_argument(
        "--replace",
        action="store_true",
        help="write over the run of this name instead of refusing; the store is a git "
        "repository, so the replaced one can be recovered",
    )
    run.set_defaults(handler=_run)


def _report_command(sub) -> None:
    """Add the subcommand that reads the runs back as one table."""
    show = sub.add_parser("report", help="one row per run and group, against a baseline")
    show.add_argument("--store", type=store_argument, required=True, help=STORE_HELP)
    show.add_argument(
        "--runs",
        action="append",
        default=[],
        metavar="NAME",
        help="only these runs, by their file name (repeatable); without it, every run",
    )
    show.add_argument(
        "--baseline",
        default="",
        metavar="NAME",
        help="the run every other is compared with, page for page; without it the difference "
        "column is a dash",
    )
    show.add_argument("--group", default="", metavar="NAME", help="only the pages of this group")
    show.add_argument(
        "--split",
        choices=records.SPLITS,
        default=records.ALL,
        help="only the pages of the tune or the check side of the set; check is the side a "
        "choice is confirmed on",
    )
    show.add_argument(
        "--folder",
        default="",
        metavar="NAME",
        help="only the runs of one campaign folder inside ocr/runs/",
    )
    show.set_defaults(handler=_report)


def _validate_command(sub) -> None:
    """Add the subcommand that checks the store's records hold no text."""
    check = sub.add_parser("validate", help="check that every stored run holds numbers and names")
    check.add_argument("--store", type=store_argument, required=True, help=STORE_HELP)
    check.add_argument("--folder", default="", metavar="NAME", help="only one campaign folder")
    check.set_defaults(handler=_validate)


def _preset(text: str) -> presets.Preset:
    """Return the preset *text* names, as :mod:`argparse` wants it, once the app can build it."""
    try:
        preset = presets.parse(text)
        prepare.steps_of(preset, engine=ENGINE_PPOCR, store=ModelStore())
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return preset


def _detector(text: str) -> tuple[str, float]:
    """Return one ``KEY=VALUE`` detector override, as :mod:`argparse` wants it."""
    key, _, value = text.partition("=")
    if not value:
        raise argparse.ArgumentTypeError(f"{text}: name it as key=value")
    try:
        return key.strip(), float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text}: {value!r} is not a number") from None


def _refuse(message: str) -> int:
    """Print why the command may not run and return the exit code of a refusal."""
    print(message, file=sys.stderr)
    return 2


def _run(args: argparse.Namespace) -> int:
    """Read the page set with one engine and write the run's record."""
    try:
        machine = require_machine(args.store, args.machine)
    except (ValueError, FormatError) as error:
        return _refuse(str(error))
    problem = machine.device_problem(cpu_only=args.cpu_only)
    if problem:
        return _refuse(problem)
    if args.model != READER and args.detector:
        return _refuse("--detector sets the built-in reader's numbers; a model has none")
    if args.model == READER and args.prompt == records.CARD:
        return _refuse("the built-in reader has no card: it takes --prompt app only")
    plan = runner.Plan(
        model=args.model,
        preset=args.preset,
        pages=args.pages,
        store=args.store,
        machine=args.machine,
        cache=args.cache or cache_root(),
        prompt=args.prompt,
        language=args.language,
        cpu_only=args.cpu_only,
        url=args.url,
        timeout=args.timeout,
        detector=tuple(args.detector),
        split=args.split,
        folder=args.folder,
    )
    try:
        path, record = runner.run(plan, replace=args.replace)
    except (ValueError, FormatError, FileExistsError) as error:
        return _refuse(str(error))
    except OCRError as error:
        return _refuse(message_of(error).key)
    totals = Totals.of(record.pages)
    print(f"{path}")
    print(
        f"{totals.pages} page(s), CER {totals.cer:.3f}, WER {totals.wer:.3f}, "
        f"{totals.failures} with a failure flag, {totals.seconds:.1f} s"
    )
    return 0


def _report(args: argparse.Namespace) -> int:
    """Print the table of the store's runs."""
    try:
        found = runs.records_of(args.store, *args.runs, folder=args.folder or None)
    except (ValueError, FormatError, OSError) as error:
        return _refuse(str(error))
    if not found:
        return _refuse(f"{runs.runs_dir(args.store)}: no run of this tool is there")
    baseline = None
    if args.baseline:
        try:
            baseline = runs.pick(found, args.baseline)
        except ValueError as error:
            return _refuse(str(error))
    if baseline is not None:
        found = _one_set(found, baseline.header.set_version)
    elif len({one.header.set_version for one in found}) > 1:
        return _refuse(_versions_of(found))
    split = "" if args.split == records.ALL else args.split
    print(report.table(found, baseline=baseline, group=args.group, split=split))
    return 0


def _one_set(found: list[runs.Stored], version: str) -> list[runs.Stored]:
    """Return the runs read against *version*, naming the ones left out on stderr."""
    kept = [one for one in found if one.header.set_version == version]
    for one in found:
        if one.header.set_version != version:
            print(
                f"{one.name}: another page set ({_short(one.header.set_version)}), left out",
                file=sys.stderr,
            )
    return kept


def _versions_of(found: list[runs.Stored]) -> str:
    """Return what to say about a store whose runs hold more than one page set."""
    counts = Counter(one.header.set_version for one in found)
    listed = ", ".join(f"{_short(version)} ({count} run(s))" for version, count in counts.items())
    return (
        f"the store holds runs of more than one page set: {listed}; "
        "choose one with --baseline, or read one folder of them with --folder"
    )


def _short(version: str) -> str:
    """Return a page set's version as a row and a line name it: its first six hex digits."""
    return version[:6] or "unknown"


def _validate(args: argparse.Namespace) -> int:
    """Check every record of the store, and name the ones that hold more than numbers."""
    broken = 0
    files = runs.run_files(args.store, folder=args.folder or None)
    if not files:
        return _refuse(f"{runs.runs_dir(args.store)}: no run of this tool is there")
    for path in files:
        try:
            for record in read_jsonl(path):
                validate(record)
        except (FormatError, ValueError) as error:
            broken += 1
            print(f"{path.name}: {error}", file=sys.stderr)
    print(f"{len(files) - broken} of {len(files)} record(s) hold numbers and names only")
    return 1 if broken else 0


def _screen_command(sub) -> None:
    """Add the subcommand that checks the candidates and times a drawn page."""
    probe = sub.add_parser(
        "screen",
        help="check the candidates on a drawn page, and time what a drawn page costs",
    )
    probe.add_argument(
        "--models",
        nargs="+",
        default=[*candidates.screen(), READER],
        metavar="TAG",
        help=f"the engines to check and time: an Ollama tag, or {READER!r} for the built-in reader "
        "(default: every catalogued reading model, every candidate, and the reader)",
    )
    probe.add_argument(
        "--lines",
        nargs="+",
        type=int,
        default=list(screen.LINES),
        metavar="N",
        help="the ruled line counts of the drawn pages (default: %(default)s)",
    )
    probe.add_argument(
        "--prompt",
        choices=(*records.PROMPTS, BOTH),
        default=BOTH,
        help="ask a model with the app's OCR prompt, with the one its card names, or with both "
        "where its card names one (default %(default)s)",
    )
    probe.add_argument(
        "--cpu-only",
        action="store_true",
        help="keep the models off the graphics card (num_gpu 0), so they and the reader are "
        "timed on the same processor",
    )
    probe.add_argument("--url", default="", metavar="URL", help="the Ollama server's address")
    probe.add_argument("--language", default=runner.LANGUAGE, help="the language the prompt names")
    probe.add_argument(
        "--folder",
        type=workspace_path,
        default=None,
        metavar="DIR",
        help="keep the drawn pages here, to look at them; by default they are temporary",
    )
    probe.add_argument(
        "--timeout",
        type=float,
        default=runner.TIMEOUT,
        help="how long one page's whole answer may take (default %(default)s s)",
    )
    probe.set_defaults(handler=_screen)


def _screen(args: argparse.Namespace) -> int:
    """Check every engine on a drawn page, time it, and print the two tables."""
    lines = sorted(set(args.lines))
    if not lines or any(count < 1 for count in lines):
        return _refuse("--lines takes line counts of one and more")
    tags = [tag for tag in args.models if tag != READER]
    temporary, folder = screen.drawn_folder(args.folder)
    missing: list[str] = []
    rows: list[screen.Timed] = []
    failed: list[str] = []
    try:
        images = screen.drawn_pages(lines, folder)
        client = OllamaClient(args.url or str(DEFAULTS["ollama_url"]))
        wanted = tags
        if tags:
            try:
                found = screen.presences(client, tags)
            except OllamaUnreachableError as error:
                return _refuse(str(error))
            print(screen.presence_table(found))
            print()
            missing = [row.tag for row in found if not row.on_server]
            wanted = [row.tag for row in found if row.on_server]
        for tag in wanted:
            try:
                rows.extend(_timed_model(client, tag, images, args))
            except (OCRError, ValueError) as error:
                failed.append(f"{tag}: {error}")
        if READER in args.models:
            timed = _timed_reader(images, args)
            if timed is None:
                failed.append(f"{READER}: its models are not downloaded")
            else:
                rows.extend(timed)
    finally:
        if temporary is not None:
            temporary.cleanup()
    if rows:
        print(screen.speed_table(rows, lines, machine=screen.machine_name()))
    for tag in missing:
        print(f"{tag}: the server does not hold it", file=sys.stderr)
    for name in sorted({row.name for row in rows if row.answered < row.pages}):
        print(f"{name}: answered a drawn page with nothing", file=sys.stderr)
    for line in failed:
        print(line, file=sys.stderr)
    if not rows and not missing:
        return _refuse("nothing was timed: name a tag the catalog or the survey knows, or reader")
    silent = any(row.answered < row.pages for row in rows)
    return 1 if missing or silent or failed else 0


def _timed_model(
    client: OllamaClient, tag: str, images: Sequence[Path], args: argparse.Namespace
) -> list[screen.Timed]:
    """Return what *tag* costs on the drawn pages, asked with the prompts it is asked with."""
    rows: list[screen.Timed] = []
    for prompt in _prompts(tag, args.prompt):
        engine = Model(
            client,
            tag,
            prompt=prompt,
            cpu_only=args.cpu_only,
            total_timeout=args.timeout,
        )
        rows.append(screen.timed(engine, images, prompt=prompt, language=args.language))
    return rows


def _timed_reader(images: Sequence[Path], args: argparse.Namespace) -> list[screen.Timed] | None:
    """Return what the reader costs on the drawn pages, or ``None`` when its models are absent."""
    try:
        return [screen.timed(Reader(ModelStore()), images, prompt="", language=args.language)]
    except ModelMissingError:
        return None


def _prompts(tag: str, wanted: str) -> list[str]:
    """Return the prompts *tag* is timed with: the app's, and the card's where it names one."""
    if wanted == records.APP:
        return [records.APP]
    if not candidates.card_prompt(tag):
        return [records.APP]
    return [records.CARD] if wanted == records.CARD else [records.APP, records.CARD]


def main(argv: list[str] | None = None) -> int:
    """Run the tool; return the exit code."""
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
