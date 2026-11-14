"""Run writing models over a test set (gate 2 of the writing-model evaluation).

Developer tool, needs a running Ollama server, not run in CI.  Every text goes through the app's
own corrector (review mode), so the request is the one a teacher's run sends — including the
app's long-text path: a text longer than one request is proofread in pieces, and the row records
how many.  The comparison uses the app's tokeniser and word diff.  The test set comes from
``tools/prepare_gec_testset.py`` and stays outside the repository (real learner text, licences).

This command only *runs*.  What happens to the results afterwards — the table, the explanation
sheet, the export into the repository, the gate and the comparison — is ``proofreading_store``.

Per language and model a run records

* the edits of the model against the reference, for edit precision / recall / F0.5 (a change
  counts when it covers the same words and has the same replacement);
* how many already-correct sentences came back unchanged;
* how often the review failed or fell back to corrections only;
* the changes that carry an explanation, notes that explain no change (orphans), and seconds.

Run (results are appended, so an interrupted run resumes)::

    bazel run //tools:compare_writing_models -- run --testset ~/gec-testset/set \\
        --out ~/gec-testset/results --model qwen3.5:9b --model gemma4:e4b
    bazel run //tools:proofreading_store -- report --cache ~/gec-testset/results

Each model starts at the temperature its maker recommends (``--temperature`` overrides it).
``run --mode text`` asks for the corrected text only (no JSON, no explanations) instead of the
app's review answer, so the same texts show what the JSON format costs in correction quality;
results of a mode other than ``review`` go to files of their own.  ``run`` refuses to start when
the prompts, the answer format or the window changed since the current ``eval_version``
(``tools/model_eval/protocol.json``), and refuses to resume a run whose sessions would not be one
run.  The work itself lives in :mod:`tools.model_eval`; this file only parses arguments and prints.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import shlex
import sys
from typing import cast

from teachers_teammate.domain.model_catalog import RequestProfile
from teachers_teammate.infrastructure.ollama_utils import OllamaClient
from tools.model_eval import defaults, protocol, results, runner
from tools.model_eval.answer_mode import DEFAULT_MODE, RUNNABLE_MODES, AnswerMode, parse_mode
from tools.model_eval.app_corrector import (
    CorrectorFactory,
    app_corrector_factory,
    exchange_corrector_factory,
)
from tools.model_eval.cli_paths import workspace_path
from tools.model_eval.languages import LANGUAGE_NAMES
from tools.model_eval.ports import OllamaChatPort, RecordingPort
from tools.model_eval.progress import STYLES, Progress
from tools.model_eval.smoke import (
    SMOKE_DEADLINE,
    SMOKE_TIMEOUT,
    Smoke,
    SmokeCheck,
    TextSmokeCheck,
    setup_label,
)
from tools.model_eval.structured_check import StructuredOutputCheck


def _refuse(message: str) -> int:
    """Print why the run may not start and return the exit code of a refused run."""
    print(message, file=sys.stderr)
    return 2


def _matrix(args: argparse.Namespace) -> runner.Matrix:
    """Return what the parsed command line asks a run for."""
    return runner.Matrix(
        out=args.out,
        testset=args.testset,
        languages=tuple(args.language),
        models=tuple(args.model),
        temperatures=tuple(args.temperature),
        modes=tuple(args.mode),
        repeats=args.repeat,
        base_seed=None if args.no_seed else args.seed,
        timeout=args.timeout,
        isolate=args.isolate,
        gpu_sampler=args.gpu_agent_record or args.gpu_sampler,
        machine=args.machine,
        compact_json=args.compact_json,
    )


def _seams(args: argparse.Namespace) -> runner.Seams:
    """Return everything of a run that reaches a server, a card or the clock."""
    from tools.model_eval import measure  # noqa: PLC0415

    def new_sampler() -> measure.VramSampler | None:
        if args.gpu_agent_client is not None:
            return measure.AgentSampler(args.gpu_agent_client)
        if not args.gpu_sampler:
            return None
        return measure.GpuSampler(args.gpu_sampler, shlex.split(args.gpu_ssh))

    def new_factory(
        client: RecordingPort, mode: AnswerMode, profile: RequestProfile
    ) -> CorrectorFactory:
        # The seam promises a recording client; the app's factory wants the app's client type,
        # which is what the recording client is.
        if mode is AnswerMode.EXCHANGE:
            return exchange_corrector_factory(
                cast(OllamaClient, client),
                timeout=args.timeout,
                num_ctx=protocol.NUM_CTX,
                profile=profile,
            )
        return app_corrector_factory(
            cast(OllamaClient, client),
            timeout=args.timeout,
            mode=mode,
            num_ctx=protocol.NUM_CTX,
            profile=profile,
        )

    def smoke(model: str, mode: AnswerMode, temperature: float) -> Smoke:
        # The setup, not the session: one pass covers every repeat and every language of it.
        port = OllamaChatPort(args.ollama_url, num_ctx=protocol.NUM_CTX)
        if mode is AnswerMode.REVIEW:
            check = StructuredOutputCheck(
                port, timeout=SMOKE_TIMEOUT, compact_json=args.compact_json, deadline=SMOKE_DEADLINE
            )
            return SmokeCheck(check, probe=measure.ServerProbe(args.ollama_url)).run(
                model, temperature
            )
        # The exchange's step ① is a plain-text request like text mode's, so the text check
        # screens it: the later steps are small JSON answers that never fail the file.
        return TextSmokeCheck(port, timeout=SMOKE_TIMEOUT).run(model, temperature)

    return runner.Seams(
        probe=measure.ServerProbe(args.ollama_url),
        new_client=lambda seed: measure.RecordingClient(args.ollama_url, seed=seed),
        new_factory=new_factory,
        new_sampler=new_sampler,
        smoke=None if args.no_smoke else smoke,
        now=lambda: datetime.now(UTC).isoformat(timespec="seconds"),
        revision=measure.tool_revision(),
    )


def _announce_complete(path: Path) -> None:
    """Say that a results file already holds every text, so the run skips it."""
    print(f"{path.name}: already complete", flush=True)


def _announce(item: runner.Session) -> None:
    """Say which session starts."""
    print(f"== {item.model} @ {item.temperature:g} ({item.mode}), repeat {item.repeat}", flush=True)


def _announce_smoke(item: runner.Session, smoke: Smoke, compact_json: str) -> None:
    """Say what the smoke check found for the setup of *item*, before its first session."""
    profile = defaults.request_profile_for(
        item.model, compact_json=compact_json, temperature=item.temperature
    )
    print(
        f"  {smoke.line(setup_label(item.model, profile, protocol.NUM_CTX))}",
        flush=True,
    )
    for warning in smoke.warnings:
        print(f"  WARNING: {warning}", flush=True)
    if not smoke.passed:
        print(f"  → {item.mode} sessions of {item.model} are skipped", flush=True)


def _print_row(_item: runner.Session, row: results.ResultRow) -> None:
    """Print one row as it is written."""
    print(f"  {row.id:<12} {row.seconds:5.0f}s  {_detail(row)}", flush=True)


def _languages(args: argparse.Namespace) -> list[str] | None:
    """Return the languages to run, or ``None`` after saying why they cannot be run.

    The set's own files decide what it holds, so a set without ``fr.jsonl`` runs German and
    English instead of failing on a missing file.  A language the tool cannot ask in is refused
    here, before the first request, because its prompt would silently be the English one.
    """
    available = results.testset_languages(args.testset)
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
            "languages.LANGUAGE_NAMES, which needs a new eval version"
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
    from tools.model_eval.agent_client import AgentClient, AgentError  # noqa: PLC0415

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
    try:
        stamp = protocol.current_stamp()
    except protocol.ProtocolError as error:
        return _refuse(str(error))

    languages = _languages(args)
    if languages is None:
        return 2
    args.language = languages
    if _use_gpu_agent(args) is not None:
        return 2
    records = results.read_testset(args.testset, languages)
    result = runner.run_matrix(
        _matrix(args),
        records,
        stamp,
        _seams(args),
        on_complete=_announce_complete,
        on_session=_announce,
        on_row=_print_row,
        on_smoke=lambda item, smoke: _announce_smoke(item, smoke, args.compact_json),
        new_progress=lambda total: Progress(total, style=args.progress),
    )
    if result.refused:
        return _refuse("\n\n".join(result.refused))
    return 0


def _detail(row: results.ResultRow) -> str:
    """Return the one-line detail of a row: its score or its unchanged sentences."""
    if row.kind == "errors":
        detail = f"tp/fp/fn {row.true_positives}/{row.false_positives}/{row.false_negatives}"
    else:
        detail = f"unchanged {row.unchanged}/{len(row.sentences)}"
    if row.failed:
        detail += "  FAILED"
    if row.fallback:
        detail += "  FALLBACK"
    perf = row.performance
    if perf is not None:
        if perf.generation_s:
            detail += f"  {perf.answer_tokens / perf.generation_s:.0f} tok/s"
        if perf.vram_used_mb is not None:
            detail += f"  +{perf.vram_used_mb} MB"
        if perf.shared:
            detail += "  SHARED"
    return detail


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="send every test text to the models")
    run.add_argument("--testset", type=workspace_path, required=True)
    run.add_argument("--out", type=workspace_path, required=True)
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
        "--mode",
        action="append",
        choices=[mode.value for mode in RUNNABLE_MODES],
        default=None,
        help="how to ask: 'review' (the app's JSON answer with explanations, default) or 'text' "
        "(corrected text only) or 'exchange' (the three-step proofreading exchange: corrected text, "
        "explanations, unclear check).  Repeatable.",
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
        "--no-seed",
        action="store_true",
        help="send no seed, as the app does (the run cannot be repeated exactly)",
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
        "(tools/model_eval/gpu_agent.py), e.g. http://HOST:11436; no shell access needed",
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
        default="",
        help="a name for the machine the server runs on, written into the run header",
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
    run.add_argument("--ollama-url", default=defaults.default_ollama_url())

    args = parser.parse_args()
    if args.command == "run":
        args.temperature = args.temperature or [None]
        args.mode = [parse_mode(mode) for mode in (args.mode or [DEFAULT_MODE])]
        return _run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
