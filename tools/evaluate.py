"""Run writing models over a test set (gate 2 of the writing-model evaluation).

Developer tool, needs a running Ollama server, not run in CI.  Every text goes through the app's own
corrector, so the request is the one a teacher's run sends — including the app's long-text path: a
text longer than one request is proofread in pieces, and the answer records how many.  The test set
is the one the store holds under ``testsets/``, built by ``tools/prepare_gec_testset.py`` from
corpora that stay outside the repository (real learner text, licences).

This command only *runs*.  What happens to the answers afterwards — the table, the explanation
sheet, the export into the repository, the gate and the comparison — is ``tools/report.py`` and
``tools/publish.py``.

Per language and model a run writes one answer per text, and the report derives from it

* the edits of the model against the reference, for edit precision / recall / F0.5 (a change
  counts when it covers the same words and has the same replacement);
* how many already-correct sentences came back unchanged;
* how often the review failed or fell back to corrections only;
* the changes that carry an explanation, notes that explain no change (orphans), and seconds.

Run (answers are appended, so an interrupted run resumes):::

    bazel run //tools:evaluate -- run --store ~/gec-eval --machine rx9070xt-server \\
        --testset gec-2026-12 --model qwen3.5:9b --model gemma4:e4b
    bazel run //tools:report -- table --store ~/gec-eval

Each model starts at the temperature its maker recommends (``--temperature`` overrides it).
``--exchange`` says how the text travels and ``--steps`` which steps of it run: the default is the
app's ``text-first`` exchange with all three steps, ``--exchange one-request-review`` asks for
corrections with explanations in one JSON answer, ``--exchange one-request-text`` for the corrected
text only, and ``--steps correct`` sends step ① alone.  Every setup writes files of its own.
``--machine`` is required: a run of a server of ours names the machine it ran on, and a report joins
the runs and the size measurements of one machine by that name.  ``run`` refuses to start when the
prompts, the answer format or the window changed since the current ``eval_version``
(``tools/evaluation/core/protocol.json``), and refuses to resume a run whose sessions would not be
one run.  The work itself lives in :mod:`tools.evaluation`; this file only parses arguments and
prints.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC, datetime
import math
from pathlib import Path
import shlex
import sys
from typing import cast

from teachers_teammate.domain.correction_prompts import CORRECT_VARIANTS
from teachers_teammate.domain.model_catalog import SAMPLING_OPTIONS, RequestProfile
from teachers_teammate.infrastructure.ollama_utils import OllamaClient
from tools.evaluation.checks.smoke import (
    SMOKE_DEADLINE,
    SMOKE_TIMEOUT,
    STEP_ONE_SMOKE_FIXTURES,
    Smoke,
    SmokeCheck,
    TextSmokeCheck,
    smoke_label,
)
from tools.evaluation.checks.structured_check import StructuredOutputCheck, step_one_request
from tools.evaluation.core.formats import FormatError
from tools.evaluation.core.languages import LANGUAGE_NAMES
from tools.evaluation.core.machines import require_machine
from tools.evaluation.core.modes import (
    DEFAULT_EXCHANGE,
    Exchange,
    Setup,
    Step,
    default_steps,
    exchange_of,
    setup_label,
    steps_of,
)
from tools.evaluation.core.paths import workspace_path
from tools.evaluation.core.performance import performance_of
from tools.evaluation.core.records import Answer, question_sha256
from tools.evaluation.run import defaults, protocol, run_log, runner
from tools.evaluation.run.app_corrector import (
    BUILT_EXCHANGES,
    CorrectorFactory,
    app_corrector_factory,
)
from tools.evaluation.run.ports import OllamaChatPort, RecordingPort
from tools.evaluation.run.progress import STYLES, Progress
from tools.evaluation.store import layout, reader


def _sampling_option(text: str) -> tuple[str, float]:
    """Return ``(name, value)`` of a ``--option NAME=VALUE``, or refuse it for argparse."""
    name, separator, value = text.partition("=")
    if not separator or name not in SAMPLING_OPTIONS:
        allowed = ", ".join(sorted(SAMPLING_OPTIONS))
        raise argparse.ArgumentTypeError(f"{text!r}: use NAME=VALUE with NAME one of {allowed}")
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r}: {value!r} is not a number") from None
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(f"{text!r}: {value!r} is not a finite number")
    if name == "top_k" and not number.is_integer():
        raise argparse.ArgumentTypeError(f"{text!r}: top_k is a whole number")
    return name, number


def _steps(text: str) -> tuple[Step, ...]:
    """Return the steps of a ``--steps correct,explain`` list, or refuse it for argparse."""
    try:
        return steps_of([part.strip() for part in text.split(",") if part.strip()])
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _setups(args: argparse.Namespace) -> list[Setup]:
    """Return the setups the command line asks for: each exchange with the steps it runs."""
    exchanges = [exchange_of(value) for value in (args.exchange or [DEFAULT_EXCHANGE])]
    return [(exchange, args.steps or default_steps(exchange)) for exchange in exchanges]


def _option_problem(args: argparse.Namespace) -> str | None:
    """Return why the ``--option`` values cannot be used with this run, or ``None``.

    Only the proofreading exchange sends sampling values; the one-request exchanges would ignore
    them and still write files that say they were asked with them.  A name given twice would leave
    the value to chance.
    """
    names = [name for name, _ in args.option]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        return f"--option {repeated[0]} is given more than once"
    elsewhere = sorted(
        {setup_label(setup) for setup in args.setup if setup[0] is not Exchange.TEXT_FIRST}
    )
    if args.prompt and elsewhere:
        return (
            "--prompt reaches the proofreading exchange only; it would be ignored in "
            f"{', '.join(elsewhere)}.  Ask with --exchange text-first."
        )
    if names and elsewhere:
        return (
            "--option reaches the proofreading exchange only; it would be ignored in "
            f"{', '.join(elsewhere)}.  Ask with --exchange text-first."
        )
    if args.steps and elsewhere:
        return (
            "--steps reaches the proofreading exchange only; a one-request answer carries what "
            f"its one request asked for.  It would be ignored in {', '.join(elsewhere)}."
        )
    return None


def _refuse(message: str) -> int:
    """Print why the run may not start and return the exit code of a refused run."""
    print(message, file=sys.stderr)
    return 2


def _matrix(args: argparse.Namespace) -> runner.Matrix:
    """Return what the parsed command line asks a run for."""
    return runner.Matrix(
        root=args.store,
        testset=args.testset,
        languages=tuple(args.language),
        models=tuple(args.model),
        temperatures=tuple(args.temperature),
        setups=tuple(args.setup),
        repeats=args.repeat,
        base_seed=args.seed,
        timeout=args.timeout,
        isolate=args.isolate,
        gpu_sampler=args.gpu_agent_record or args.gpu_sampler,
        machine=args.machine,
        compact_json=args.compact_json,
        options=tuple(args.option),
        prompt=args.prompt,
        question_sha256=args.question_sha256,
    )


def _seams(args: argparse.Namespace) -> runner.Seams:
    """Return everything of a run that reaches a server, a card or the clock."""
    from tools.evaluation.core.revision import tool_revision  # noqa: PLC0415
    from tools.evaluation.run import measure  # noqa: PLC0415

    def new_sampler() -> measure.VramSampler | None:
        if args.gpu_agent_client is not None:
            return measure.AgentSampler(args.gpu_agent_client)
        if not args.gpu_sampler:
            return None
        return measure.GpuSampler(args.gpu_sampler, shlex.split(args.gpu_ssh))

    def new_factory(
        client: RecordingPort,
        exchange: Exchange,
        steps: tuple[Step, ...],
        profile: RequestProfile,
    ) -> CorrectorFactory:
        # The seam promises a recording client; the app's factory wants the app's client type,
        # which is what the recording client is.
        return app_corrector_factory(
            cast(OllamaClient, client),
            timeout=args.timeout,
            exchange=exchange,
            steps=steps,
            num_ctx=protocol.NUM_CTX,
            profile=profile,
        )

    def smoke(
        model: str, exchange: Exchange, _steps: tuple[Step, ...], temperature: float
    ) -> Smoke:
        # The steps do not change the screen: step ① decides whether a text-first file fails, and
        # a one-request answer is one request whatever steps it carries.
        variant = defaults.request_profile_for(model, prompt=args.prompt).correct.prompt
        # The setup, not the session: one pass covers every repeat and every language of it.
        port = OllamaChatPort(args.ollama_url, num_ctx=protocol.NUM_CTX)
        if exchange is Exchange.ONE_REQUEST_REVIEW:
            check = StructuredOutputCheck(
                port, timeout=SMOKE_TIMEOUT, compact_json=args.compact_json, deadline=SMOKE_DEADLINE
            )
            return SmokeCheck(check, probe=measure.ServerProbe(args.ollama_url)).run(
                model, temperature
            )
        if exchange is Exchange.TEXT_FIRST:
            # Step ① decides whether a file fails (the later steps are small JSON answers that
            # never do), so it is screened with its own request on the plain fixtures the run
            # sends, not with text mode's prompt and its Markdown fixture.
            return TextSmokeCheck(
                port,
                timeout=SMOKE_TIMEOUT,
                request=lambda language, text_format, text: step_one_request(
                    language, text_format, text, variant
                ),
                fixtures=STEP_ONE_SMOKE_FIXTURES,
            ).run(model, temperature)
        return TextSmokeCheck(port, timeout=SMOKE_TIMEOUT).run(model, temperature)

    return runner.Seams(
        probe=measure.ServerProbe(args.ollama_url),
        new_client=lambda seed: measure.RecordingClient(args.ollama_url, seed=seed),
        new_factory=new_factory,
        new_sampler=new_sampler,
        smoke=None if args.no_smoke else smoke,
        now=lambda: datetime.now(UTC).isoformat(timespec="seconds"),
        revision=tool_revision(),
    )


def _announce_complete(path: Path) -> None:
    """Say that a run file already holds every text, so the run skips it."""
    print(f"{path.name}: already complete", flush=True)


def _announce(item: runner.PlannedRun) -> None:
    """Say which session starts."""
    print(f"== {runner.session_label(item)}", flush=True)


def _announce_smoke(item: runner.PlannedRun, smoke: Smoke, compact_json: str) -> None:
    """Say what the smoke check found for the setup of *item*, before its first session."""
    profile = defaults.request_profile_for(
        item.model, compact_json=compact_json, temperature=item.temperature
    )
    print(
        f"  {smoke.line(smoke_label(item.model, profile, protocol.NUM_CTX))}",
        flush=True,
    )
    for warning in smoke.warnings:
        print(f"  WARNING: {warning}", flush=True)
    if not smoke.passed:
        print(
            f"  → {setup_label((item.exchange, item.steps))} sessions of {item.model} are skipped",
            flush=True,
        )


def _print_row(_item: runner.PlannedRun, answer: Answer) -> None:
    """Print one answer as it is written."""
    print(f"  {answer.text:<12} {_detail(answer)}", flush=True)


def _languages(args: argparse.Namespace) -> list[str] | None:
    """Return the languages to run, or ``None`` after saying why they cannot be run.

    The set's own files decide what it holds, so a set without ``fr.jsonl`` runs German and
    English instead of failing on a missing file.  A language the tool cannot ask in is refused
    here, before the first request, because its prompt would silently be the English one.
    """
    available = layout.languages_of(args.store, args.testset)
    if not available:
        _refuse(f"{args.testset}: no <language>.jsonl in the set")
        return None
    chosen = args.language or available
    if unknown := [code for code in chosen if code not in available]:
        _refuse(f"{args.testset}: the set has {', '.join(available)}, not {', '.join(unknown)}")
        return None
    if unnamed := [code for code in chosen if code not in LANGUAGE_NAMES]:
        _refuse(
            f"no prompt language for {', '.join(repr(code) for code in unnamed)}: add it to "
            "core.languages.LANGUAGE_NAMES, which needs a new eval version"
        )
        return None
    return chosen


def _use_gpu_agent(args: argparse.Namespace) -> str | None:
    """Ask the memory agent named by ``--gpu-agent`` who it is; ``None`` when all is well.

    Sets ``args.gpu_agent_client`` and ``args.gpu_agent_record`` (what the header records), both
    ``None`` without ``--gpu-agent``.  Returns the reason when the run may not start, after
    saying it.
    """
    args.gpu_agent_client = None
    args.gpu_agent_record = None
    if not args.gpu_agent:
        return None
    if args.gpu_sampler:
        _refuse("--gpu-agent and --gpu-sampler are two ways to read the card: pass one")
        return "both"
    from tools.evaluation.run.agent_client import AgentClient, AgentError  # noqa: PLC0415

    token = args.gpu_token_file.read_text(encoding="utf-8").strip() if args.gpu_token_file else None
    client = AgentClient(args.gpu_agent, token=token)
    try:
        info = client.info()
    except AgentError as error:
        _refuse(f"the memory agent at {args.gpu_agent} cannot be used: {error}")
        return "unreachable"
    args.gpu_agent_client = client
    args.gpu_agent_record = {
        "kind": "agent",
        "vendor": info.vendor,
        "source": info.source,
        "agent_sha256": info.agent_sha256,
    }
    return None


def _run(args: argparse.Namespace) -> int:
    """Run the matrix *args* describes, or say why it does not start."""
    # The request side is checked before anything is sent: a prompt, answer format or window that
    # changed since the current version needs a new version in protocol.json, not a new run.
    if problem := _option_problem(args):
        return _refuse(problem)
    # The machine is an id of the registry, so a typo is refused here and not found later as a
    # second machine that no report can compare with the first.
    try:
        require_machine(workspace_path("."), args.machine)
    except (ValueError, FormatError) as error:
        return _refuse(str(error))
    try:
        stamp = protocol.current_stamp()
    except protocol.ProtocolError as error:
        return _refuse(str(error))

    if missing := layout.missing(args.store, [args.testset]):
        return _refuse(f"{args.store}: the store has no test set {', '.join(missing)}")
    languages = _languages(args)
    if languages is None:
        return 2
    args.language = languages
    # The store reads every language of the set; a run sends the ones it was asked for, in the
    # set's own order, so a `--language fr` run writes the same rows as a full run's French ones.
    # The header still records the whole set's question hash: a subset run answers the same
    # question, and a hash of the subset would make it look like another set.
    whole = reader.read_testset(args.store, args.testset)
    args.question_sha256 = question_sha256(whole)
    records = [record for record in whole if record.language in languages]
    if args.dry_run:
        # A dry run sends nothing but the server probe: the memory agent is not asked.
        args.gpu_agent_client = None
        args.gpu_agent_record = None
        return _dry_run(args, records, stamp)
    if _use_gpu_agent(args) is not None:
        return 2
    log = None if args.log is None else run_log.RunLog(args.log)
    outcome = "interrupted"
    result: runner.RunResult | None = None
    try:
        result = runner.run_matrix(
            _matrix(args),
            records,
            stamp,
            _seams(args),
            on_complete=_logged(log, "complete", _announce_complete, _complete_fields),
            on_session=_logged(log, "session", _announce, _session_fields),
            on_row=_logged(log, "row", _print_row, _row_fields),
            on_smoke=_logged(
                log,
                "smoke",
                lambda item, smoke: _announce_smoke(item, smoke, args.compact_json),
                _smoke_fields,
            ),
            on_dropped=_logged(log, "dropped", _announce_dropped, _dropped_fields),
            new_progress=lambda total: Progress(total, style=args.progress),
        )
        outcome = "refused" if result.refused else "finished"
    finally:
        if log is not None:
            log.write(outcome, **_result_fields(result))
    _announce_skipped(result)
    if result.refused:
        return _refuse("\n\n".join(result.refused))
    return 0


def _dry_run(args: argparse.Namespace, records: list, stamp: protocol.Stamp) -> int:
    """Print what the run would do, without sending a request.

    The plan is the same one the run makes, so the sessions, the files already complete and the
    refusals are the ones the run would meet — only the server probe is sent, which reads the
    version and the model's details and nothing else.
    """
    decided = runner.plan(
        _matrix(args),
        records,
        stamp,
        _seams(args).probe,
        date=datetime.now(UTC).date().isoformat(),
    )
    for path in decided.complete:
        _announce_complete(path)
    for message in decided.dropped:
        _announce_dropped(message)
    for item in decided.sessions:
        missing = len(records) - len(item.done)
        print(
            f"  {runner.session_label(item)}: {missing} of {len(records)} texts to send",
            flush=True,
        )
    if decided.refused:
        return _refuse("\n\n".join(decided.refused))
    requests = sum(len(records) - len(item.done) for item in decided.sessions)
    print(
        f"{len(decided.sessions)} sessions, {requests} requests, "
        f"{len(decided.complete)} files already complete",
        flush=True,
    )
    return 0


def _announce_skipped(result: runner.RunResult) -> None:
    """Say which planned sessions did not run, and why, after the run.

    The exit code stays 0: a script that chains runs decides for itself what a skipped setup
    means, and a run that wrote most of its rows is not a failure.
    """
    if not result.skipped:
        return
    print(
        f"{len(result.skipped)} of {result.planned} planned sessions did not run:",
        file=sys.stderr,
        flush=True,
    )
    for line in result.skipped:
        print(f"  {line}", file=sys.stderr, flush=True)


def _announce_dropped(message: str) -> None:
    """Say that a torn answer was dropped from a run file, so the text is sent again."""
    print(f"  {message}", file=sys.stderr, flush=True)


def _logged(
    log: run_log.RunLog | None,
    event: str,
    announce: Callable[..., None],
    *builders: Callable[..., dict],
) -> Callable[..., None]:
    """Return a callback that prints as before and, with a log, writes one line for it.

    Each of *builders* takes the callback's own arguments and returns the fields it adds, so the
    log says what the screen says and nothing has to be worked out twice.
    """
    if log is None:
        return announce

    def both(*args) -> None:
        announce(*args)
        fields: dict = {}
        for build in builders:
            fields.update(build(*args))
        log.write(event, **fields)

    return both


def _complete_fields(path: Path) -> dict:
    """Return the fields of a run file that already holds every text."""
    return {"file": path.name}


def _dropped_fields(message: str) -> dict:
    """Return the fields of a dropped torn answer."""
    return {"message": message}


def _session_fields(item: runner.PlannedRun) -> dict:
    """Return the fields of a session that starts."""
    return {
        "model": item.model,
        "temperature": item.temperature,
        "setup": setup_label((item.exchange, item.steps)),
        "repeat": item.repeat,
        "texts": len(item.done),
    }


def _smoke_fields(item: runner.PlannedRun, smoke: Smoke) -> dict:
    """Return the fields of a smoke check: the setup, whether it passed and why."""
    return {
        "model": item.model,
        "temperature": item.temperature,
        "setup": setup_label((item.exchange, item.steps)),
        "passed": smoke.passed,
        "reason": smoke.reason,
    }


def _row_fields(item: runner.PlannedRun, answer: Answer) -> dict:
    """Return the fields of one written answer: what it cost and whether it fell back.

    Text loss is not here: it is measured against the reference at report time, from the answer
    and the test set, so the run records the facts and the report derives the rest.
    """
    return {
        "model": item.model,
        "temperature": item.temperature,
        "setup": setup_label((item.exchange, item.steps)),
        "repeat": item.repeat,
        "item": answer.text,
        "seconds": answer.seconds,
        "pieces": answer.pieces,
        "failed": answer.failed,
        "fallback": answer.fallback,
    }


def _result_fields(result: runner.RunResult | None) -> dict:
    """Return the fields of the closing line: what the run finished, or nothing when it was cut."""
    if result is None:
        return {}
    return {
        "sessions": result.sessions,
        "rows": result.rows,
        "planned": result.planned,
        "skipped": len(result.skipped),
    }


def _detail(answer: Answer) -> str:
    """Return the one-line detail of an answer: its seconds, its pieces and what it cost."""
    detail = f"{answer.seconds:5.0f}s"
    if answer.pieces > 1:
        detail += f"  {answer.pieces} pieces"
    if answer.failed:
        detail += "  FAILED"
    if answer.fallback:
        detail += "  FALLBACK"
    perf = performance_of(answer)
    if perf.generation_s:
        detail += f"  {perf.answer_tokens / perf.generation_s:.0f} tok/s"
    if perf.vram_peak_mb is not None:
        detail += f"  {perf.vram_peak_mb} MB"
    if perf.shared:
        detail += "  SHARED"
    return detail


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the tool."""
    parser = argparse.ArgumentParser(
        prog="evaluate",
        description="Run writing models over a test set, through the app's own corrector.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _run_command(sub)
    return parser


def _run_command(sub) -> None:
    """Add the subcommand that sends every test text to the models."""
    run = sub.add_parser("run", help="send every test text to the models")
    # Filled in once the store is read: the whole set's hash, which a subset run still records.
    run.set_defaults(question_sha256="")
    run.add_argument(
        "--store",
        type=workspace_path,
        required=True,
        help="the store the run writes into: its answers go to runs/ below it, and the test set "
        "is read from testsets/<name>/",
    )
    run.add_argument(
        "--testset",
        required=True,
        metavar="NAME",
        help="the test set to run, as the store names it (e.g. gec-2026-12)",
    )
    run.add_argument("--model", action="append", required=True)
    run.add_argument(
        "--temperature",
        type=float,
        action="append",
        default=None,
        help="Run at temperature X (repeatable; default: each model's request profile, which is "
        "the temperature its vendor recommends, else the app's 0.2).",
    )
    run.add_argument(
        "--option",
        action="append",
        default=[],
        type=_sampling_option,
        metavar="NAME=VALUE",
        help="Send a sampling value with step ① besides the temperature, e.g. "
        "presence_penalty=0 or top_p=0.9 (repeatable).  The names are those of Ollama in "
        f"{', '.join(sorted(SAMPLING_OPTIONS))}.  Without it the model's own tag decides, as in "
        "the app.  Runs with other values write to files of their own.",
    )
    run.add_argument(
        "--prompt",
        choices=[defaults.STANDARD_PROMPT, *sorted(CORRECT_VARIANTS)],
        default="",
        help="Ask step ① with this variant of its system prompt (the proofreading exchange "
        "only).  Without it a model is asked with its own: the standard prompt unless a variant "
        "was adopted for it.  'standard' sends the standard prompt to such a model too.  A run "
        "with a variant writes files of its own.",
    )
    run.add_argument(
        "--compact-json",
        choices=defaults.COMPACT_JSON_CHOICES,
        default="catalog",
        help="Ask with the compact-answer rule: 'catalog' (each model's profile, default), 'on' "
        "or 'off' for every model.  Runs with different settings never pool.",
    )
    run.add_argument(
        "--language",
        action="append",
        default=None,
        help="a language of the set to run, repeatable (default: every language the set holds)",
    )
    run.add_argument(
        "--exchange",
        action="append",
        choices=[exchange.value for exchange in Exchange if exchange in BUILT_EXCHANGES],
        default=None,
        help="how the text travels: 'text-first' (the app's proofreading exchange, default), "
        "'one-request-review' (corrections with explanations in one JSON answer) or "
        "'one-request-text' (the corrected text only).  Repeatable.",
    )
    run.add_argument(
        "--steps",
        type=_steps,
        default=None,
        metavar="LIST",
        help="the steps of the exchange that run, comma-separated: correct, explain, unclear "
        "(default: every step the exchange has).  A step that is not asked for is not sent.",
    )
    run.add_argument("--timeout", type=float, default=900.0)
    run.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="run every text N times, each repeat with the next seed and a file of its own",
    )
    run.add_argument(
        "--seed",
        type=int,
        default=20261109,
        help="seed of the first repeat (default %(default)s); repeat k uses seed + k - 1",
    )
    run.add_argument(
        "--isolate",
        action="store_true",
        help="unload every model first and refuse a server that will not become idle; unload "
        "the model afterwards.  Use it for speed runs, and only on a server you own.",
    )
    run.add_argument(
        "--gpu-sampler",
        choices=["amd", "nvidia"],
        default=None,
        help="sample the graphics memory in use during the run (needs amd-smi or nvidia-smi on "
        "the machine with the card)",
    )
    run.add_argument(
        "--gpu-ssh",
        default="",
        metavar="COMMAND",
        help="reach the machine with the card through this command, e.g. 'ssh.exe user@host' "
        "(from WSL the Windows client holds the key); empty: this machine",
    )
    run.add_argument(
        "--gpu-agent",
        default=None,
        metavar="URL",
        help="sample the graphics memory through the memory agent on the machine with the card "
        "(tools/evaluation/run/gpu_agent.py), e.g. http://HOST:11436; no shell access needed",
    )
    run.add_argument(
        "--gpu-token-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="file holding the agent's token, sent as X-Token",
    )
    run.add_argument(
        "--machine",
        required=True,
        help="the id of the machine the server runs on, from evaluations/machines/, written into "
        "the run header; a report joins the runs and the size measurements of one machine by it",
    )
    run.add_argument(
        "--progress",
        choices=STYLES,
        default="auto",
        help="How to show how far the run is, on stderr: 'auto' (a bar on a terminal, one line "
        "per text otherwise, default), 'lines' or 'off'.  The per-text lines stay on stdout.",
    )
    run.add_argument(
        "--no-smoke",
        action="store_true",
        help="Skip the smoke check that screens each setup before its first session.",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the sessions the run would write, the files already complete and the "
        "refusals, and send nothing but the server probe.  Exit code 2 on a refusal.",
    )
    run.add_argument(
        "--log",
        type=workspace_path,
        default=None,
        metavar="FILE",
        help="Append one JSON line per event to FILE: each session, each smoke check, each answer "
        "with its seconds and its pieces, and how the run ended (finished, refused or "
        "interrupted).  A run that is paused with Ctrl-C still says where it stopped.",
    )
    run.add_argument("--ollama-url", default=defaults.default_ollama_url())
    run.set_defaults(handler=_run)


def main(argv: list[str] | None = None) -> int:
    """Run the tool; return the exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "run":
        args.temperature = args.temperature or [None]
        args.setup = _setups(args)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
