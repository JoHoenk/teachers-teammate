"""Developer tool: verify the Ollama model catalog against the real registry and server.

NOT run in CI — it needs network access to the Ollama registry and, for most of the jobs, a
local Ollama server.  Run it after editing ``teachers_teammate/domain/model_catalog.py``.

Six jobs, six subcommands::

    bazel run //tools:verify_model_catalog -- sizes
    bazel run //tools:verify_model_catalog -- capabilities
    bazel run //tools:verify_model_catalog -- smoke --answers DIR --tag TAG
    bazel run //tools:verify_model_catalog -- structured-output
    bazel run //tools:verify_model_catalog -- fit --server LABEL --gpu-agent URL --tag TAG
    bazel run //tools:verify_model_catalog -- fit --report
    bazel run //tools:verify_model_catalog -- recheck FILE...

``sizes`` (the default) reads every catalog tag's manifest from the registry (or, for
``hf.co/...`` tags, the Hugging Face file list) and compares the real download size with the
catalog's ``download_gb`` estimate (tolerance: ±25 % + 0.5 GB).

``capabilities`` asks a local Ollama server for each model's capabilities and prints whether it
can read images — this is how the ``gemma4:12b`` open question can be settled.

``fit`` measures what each model needs, which is what ``OllamaModelSpec.memory_gb`` and
``context_gb_per_8k`` need.  It loads the model at every step of ``CONTEXT_STEPS`` with the app's
own proofreading options and stores two readings per step: what Ollama reports (``/api/ps``) and
what the card really holds (the memory agent, ``--gpu-agent``).  Nothing derived is stored, so a
new size limit costs no measuring.  The records go to ``evaluations/model-fit/sizes.jsonl``,
append-only; ``--report`` reads them back without a server, ``--compare A B`` puts two machines
side by side, and ``--catalog`` prints the figures to enter by hand.  ``--fit-limit`` (default
``8gb`` and ``16gb``) says which windows still fit a card of that size, by the app's own rule.

``smoke`` screens every setup — tag, request profile, temperature, window — with gate 1's own
requests, cheap to expensive, one run each and a second try on a failure: at most ten requests,
a few seconds to a few minutes.  A setup fails only when the same fixture fails twice in a row,
or at once for a failure a second try cannot cure (the request failed, the model collapsed to one
sentence on the full page).  It is gate 1's first stage, not a verdict: a pass means "no sign of a
systematic fault".  Its answers are gate 1's answers, so ``structured-output --continue FILE``
finishes a setup from them and sends only the runs still missing.

Before the first fixture of a setup it applies the size limit (``--fit-limit``, default ``16gb``;
``off`` skips the check): a model whose need at the checked window is above it fails the setup
without a request, and a model with no record in ``--fit-store`` is measured first, exactly as
``fit`` does, so the screen never decides on a guess.  The header of the run and of the answers
file names the limit, so a gate-1 record says what was checked.

``structured-output`` sends the review answer format to every writing model and checks that the
answer is valid JSON-schema output, that at least 90 % of the returned sentences can be spliced
back into the text, that a long text is answered to its end, that the changes carry explanations,
and that Ollama did not stop at the context limit.  Six fixtures (English, German, French,
Markdown, one full page of German, and one German page that fills the app's own review budget),
three runs each above temperature 0, and a per-run report of placed / explained / orphans /
covered / tokens / margin / ``done_reason`` / seconds.  A model with exactly two soft failures in
its first eighteen runs gets a second round of eighteen.  The verdict is what
``OllamaModelSpec.structured_output`` records; enter it in the catalog by hand, as the download
sizes are.  Needs a pulled model.  The smoke check is its first stage (``--no-smoke`` skips it),
and a setup that fails it is not sent the eighteen runs.

The header says what the verdicts are valid for: the eval version and fingerprint of the request,
``ANSWER_FORMAT_VERSION``, the window and the temperature.  ``--answers DIR`` saves every answer
to a JSON-lines file, and ``recheck FILE`` judges those answers again with the current rule,
without a server — a rule change then costs no evening on the server.  The files stay local.

``--tag TAG`` (repeatable) restricts any subcommand to the models named.  A tag the catalog knows
is checked against its flag and counted in the tally; the others are candidates that only get a
pass or fail, so a mixed list checks both in one invocation.

The work lives in :mod:`tools.model_eval`; this file parses arguments and prints.  The older
invocations (``--ollama``, ``--structured-output``, ``--tag``, ``--temperature``) still work.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse

from teachers_teammate.domain.answer_format import ANSWER_FORMAT_VERSION
from teachers_teammate.domain.context_budget import (
    BASE_NUM_CTX,
    CONTEXT_STEPS,
    PROOFREADING_NUM_CTX_CAP,
    automatic_num_ctx,
)
from teachers_teammate.domain.model_catalog import (
    FULL_SPEED_SHARE,
    NOMINAL_CARD_GB,
    OLLAMA_CATALOG,
    OllamaModelSpec,
    RequestProfile,
)
from teachers_teammate.exceptions import OCRError, OllamaUnreachableError
from teachers_teammate.infrastructure.ollama_utils import OllamaClient
from tools.model_eval import defaults, gate1_answers, gate_one, protocol
from tools.model_eval.agent_client import AgentClient, AgentError
from tools.model_eval.check_rule import CheckRule, CheckRun, Decision
from tools.model_eval.cli_paths import workspace_path
from tools.model_eval.download_sizes import check_size, report_real_size
from tools.model_eval.measure import ServerBusyError, ServerProbe, tool_revision
from tools.model_eval.model_fit import (
    DEFAULT_LIMITS,
    DEFAULT_STORE,
    FitLimit,
    FitMeasureError,
    FitRecord,
    FitStore,
    catalog_lines,
    compare_lines,
    fit_failure,
    fit_line,
    measure_tag,
    record_line,
    size_for_decision,
    within,
)
from tools.model_eval.model_fit import report_lines as fit_report_lines
from tools.model_eval.ports import OllamaChatPort, ServerUnavailableError
from tools.model_eval.progress import STYLES, Progress
from tools.model_eval.smoke import (
    SMOKE_DEADLINE,
    SMOKE_FIXTURES,
    SMOKE_TIMEOUT,
    Smoke,
    SmokeCheck,
    setup_label,
)
from tools.model_eval.structured_check import (
    FIXTURES,
    RUNS_AT_TEMPERATURE,
    StructuredOutputCheck,
    margin_lines,
    report_lines,
    seed_settings,
)

SUBCOMMANDS = ("sizes", "capabilities", "structured-output", "smoke", "fit", "recheck")

_WRITING_CATEGORY = "general"
"""Catalog category of the models the app offers for writing."""


def _default_window(window: int | None) -> int:
    """Return the context window to check at.

    Without ``--num-ctx`` the check runs at :data:`BASE_NUM_CTX`, the window the app uses when
    the automatic rule has no measurement to go on.  A larger window is checked on request, and
    the header says when the app would use one.
    """
    return BASE_NUM_CTX if window is None else window


def _split_tags(tags: list[str]) -> tuple[list[OllamaModelSpec], list[str]]:
    """Return the catalog's writing models named by *tags*, and the tags that are not in it.

    A mixed list is what a re-run needs: the catalog models are checked against their flag and
    counted in the tally, the others are candidates that only get a pass or fail.  Without
    ``--tag`` every writing model is checked.
    """
    specs = [spec for spec in OLLAMA_CATALOG if spec.category == _WRITING_CATEGORY]
    if not tags:
        return specs, []
    chosen = [spec for spec in specs if spec.tag in tags]
    candidates = [tag for tag in tags if all(spec.tag != tag for spec in specs)]
    return chosen, candidates


def _sizes(tags: list[str]) -> int:
    """Compare the catalog's download estimates with the registry and return the exit code."""
    specs = [spec for spec in OLLAMA_CATALOG if not tags or spec.tag in tags]
    failures = 0
    for spec in specs:
        check = check_size(spec)
        print(check.report())
        if not check.matches:
            failures += 1
    for tag in tags:
        if all(spec.tag != tag for spec in OLLAMA_CATALOG):
            print(report_real_size(tag))
    print(f"\n{len(specs) - failures}/{len(specs)} tags verified")
    return 1 if failures else 0


def _capabilities(tags: list[str], url: str) -> int:
    """Print each model's capabilities and whether it reads images; return the exit code."""
    port = OllamaChatPort(url)
    catalog = {spec.tag for spec in OLLAMA_CATALOG}
    failures = 0
    for tag in [spec.tag for spec in OLLAMA_CATALOG if not tags or spec.tag in tags] + [
        tag for tag in tags if tag not in catalog
    ]:
        capabilities = port.client.model_capabilities(tag)
        if capabilities is None:
            print(f"{tag}: capabilities could not be read (model not pulled?)")
            failures += 1
            continue
        vision = "vision" in capabilities
        print(
            f"{tag}: capabilities={sorted(capabilities)} → {'READS images' if vision else 'text only'}"
        )
    return 1 if failures else 0


@dataclass
class Checked:
    """What checking one model produced: its runs, or the failed smoke check that kept them back."""

    runs: list[CheckRun]
    skipped: Smoke | None = None
    """The failed smoke check, when the eighteen runs were not sent."""


@dataclass
class _Session:
    """What one invocation of the check needs: the server, the rule and the saved answers."""

    port: OllamaChatPort
    probe: ServerProbe
    check: StructuredOutputCheck
    temperature: float | None
    """The temperature every model is checked at, or ``None`` for each model's profile value."""
    answers_file: Path | None
    progress: Progress
    smoke: bool = True
    """Whether the smoke check screens each setup before the eighteen runs."""
    fit: _FitGate | None = None
    """The size limit every setup is screened against, or ``None`` when the check is off."""

    def temperature_of(self, tag: str) -> float:
        """Return the temperature *tag* is checked at."""
        return defaults.temperature_for(tag, self.temperature)

    def smoke_check(self, tag: str, profile: RequestProfile) -> SmokeCheck:
        """Return the smoke check of *tag*: gate 1's requests, its rule and its answers file."""
        return SmokeCheck(
            self.check,
            probe=self.probe,
            on_run=_print_smoke_run(self.check.rule, self.progress),
            on_sent=gate_one.answer_saver(tag, self.probe, self.answers_file, profile),
            on_request=_report_request(self.progress),
        )

    def verdict(self, checked: Checked) -> bool | None:
        """Return whether *checked* passes, or ``None`` when the smoke check made no verdict.

        A hard smoke failure is a gate-1 FAIL as it stands, because gate 1 fails on any hard
        failure.  A soft one is not a verdict: gate 1 allows soft failures, so the setup is
        neither passed nor failed until ``--continue --force`` finishes it.
        """
        if checked.skipped is None:
            return self.check.passes(checked.runs)
        return False if checked.skipped.hard else None

    def check_model(self, tag: str) -> Checked:
        """Check *tag*, print its report and return its runs.

        The smoke check runs first, and its requests are gate 1's: a setup that passes it is
        continued from its own answers, so nothing is asked twice.  A setup that fails it is not
        sent the eighteen runs — the smoke check is the first stage, not a verdict.
        """
        temperature = self.temperature_of(tag)
        profile = self.check.profile_of(tag, temperature)
        _print_model_block(self.port, self.probe, tag, profile)
        note = _window_note(tag, self.check.window)
        if note:
            print(note)
        if self.fit is not None:
            failure, fit_lines = self.fit.check(tag, self.check.window)
            _print_lines([f"  {line}" for line in fit_lines])
            if failure is not None:
                print(f"  {failure.line(setup_label(tag, profile, self.check.window))}")
                self.progress.skip(self._expected(tag))
                print("  → skipped: the model does not fit the limit, so no request is sent")
                return Checked([], failure)
        previous: list[CheckRun] = []
        done: set[tuple[str, int, int]] = set()
        if self.smoke:
            smoke = self.smoke_check(tag, profile).run(tag, temperature)
            print(f"  {smoke.line(setup_label(tag, profile, self.check.window))}")
            _print_lines([f"  WARNING: {warning}" for warning in smoke.warnings])
            if not smoke.passed:
                self.progress.skip(self._expected(tag) - smoke.requests)
                print("  → skipped: the smoke check failed, so the eighteen runs are not sent")
                if not smoke.hard:
                    print("  → no verdict: a soft failure; `--continue FILE --force` finishes it")
                return Checked([], smoke)
            previous, done = list(smoke.runs), set(smoke.done)
        runs = self.check.run(
            tag,
            temperature,
            on_run=_print_run(self.check.rule, self.progress),
            on_retry=_print_retry(self.progress, temperature),
            on_sent=gate_one.answer_saver(tag, self.probe, self.answers_file, profile),
            on_request=_report_request(self.progress),
            previous=previous,
            done=done,
        )
        all_runs = previous + runs
        _print_lines(margin_lines(all_runs))
        return Checked(all_runs)

    def _expected(self, tag: str) -> int:
        """Return how many requests the first round of *tag* is expected to make."""
        return len(FIXTURES) * (RUNS_AT_TEMPERATURE if self.temperature_of(tag) > 0 else 1)


@dataclass
class _FitGate:
    """The size limit a screen applies before it sends the first request of a model.

    A model with no record at the checked window is measured now, exactly as ``fit`` does, so the
    screen never decides on a guess: unload, settle, one short request, ``/api/ps``, close.  The
    smoke check's first request then finds the model loaded at the same window, so the measurement
    costs no extra load.
    """

    store: FitStore
    limit: FitLimit
    probe: ServerProbe
    port: OllamaChatPort
    server: str
    agent_url: str | None = None
    token_file: Path | None = None
    timeout: float = 600.0
    _agent: Any = None
    _info: Any = None
    _connected: bool = False

    def check(self, tag: str, window: int) -> tuple[Smoke | None, list[str]]:
        """Return the failure that stops a setup, and the lines to print for it.

        A model within the limit gets one line saying what it needs.  A model above it fails the
        setup before any request, because a screen that cannot fit the card says nothing about the
        model.  A server that cannot be measured is a warning: the screen is worth more than the
        figure.
        """
        if self.limit.max_size_gb is None:
            return None, []
        digest = str(self.probe.model_info(tag).get("digest", ""))
        record = size_for_decision(self.store.records(), tag=tag, digest=digest, num_ctx=window)
        if record is None:
            record, problem = self._measure(tag, window)
            if problem is not None:
                return None, [problem]
        if record is None:
            return None, []
        if not within(record, self.limit):
            reason = fit_failure(record, self.limit)
            return Smoke(passed=False, hard=True, requests=0, reason=reason), []
        lines = [fit_line(record, self.limit)]
        if record.card is None:
            lines.append("no card reading: Ollama's size only")
        return None, lines

    def _measure(self, tag: str, window: int) -> tuple[FitRecord | None, str | None]:
        """Measure *tag* at *window* and append the record; the second value is why it failed."""
        agent, info, problem = self._memory_agent()
        if problem is not None:
            return None, problem
        try:
            made = measure_tag(
                probe=self.probe,
                client=self.port,
                agent=agent,
                agent_info=info,
                store=self.store,
                server=self.server,
                tag=tag,
                temperature=defaults.temperature_for(tag, None),
                steps=(window,),
                timeout=self.timeout,
            )
        except ServerBusyError as error:
            return None, f"could not measure the fit: the server is busy ({error})"
        except (OCRError, FitMeasureError) as error:
            return None, f"could not measure the fit: {error}"
        except OSError as error:
            return None, f"could not measure the fit: {error}"
        return (made[-1] if made else None), None

    def _memory_agent(self) -> tuple[Any, Any, str | None]:
        """Return the memory agent and its info, connecting once; the third value is a problem."""
        if self.agent_url is None:
            return None, None, None
        if not self._connected:
            self._connected = True
            try:
                token = _read_token(self.token_file)
                self._agent = AgentClient(self.agent_url, token=token)
                self._info = self._agent.info()
            except OSError as error:
                return (
                    None,
                    None,
                    f"could not measure the fit: the memory agent at {self.agent_url} "
                    f"cannot be used ({error})",
                )
        return self._agent, self._info, None


def _fit_gate(
    args: argparse.Namespace, port: OllamaChatPort, probe: ServerProbe, limit: FitLimit
) -> _FitGate:
    """Return the size gate of this invocation: the store, the limit and the agent to ask."""
    return _FitGate(
        store=FitStore(args.fit_store),
        limit=limit,
        probe=probe,
        port=port,
        server=args.server or _host_of(args.ollama_url),
        agent_url=args.gpu_agent,
        token_file=args.gpu_token_file,
        timeout=args.timeout,
    )


def _host_of(url: str) -> str:
    """Return the host of *url*, which is the label a quick screen records."""
    return urlparse(url).hostname or url


def _limit_line(limit: FitLimit) -> str:
    """Return the size limit as the header writes it."""
    if limit.max_size_gb is None:
        return "off"
    return f"{limit.label} ({limit.max_size_gb:.2f} GB)"


def _fit_limit_of(args: argparse.Namespace) -> FitLimit:
    """Return the size limit of this invocation.

    Raises:
        ValueError: the text is not a limit.
    """
    return FitLimit.parse(args.fit_limit)


def _open_answers_file(
    args: argparse.Namespace, stamp: protocol.Stamp, probe: ServerProbe, window: int
) -> Path | None:
    """Create the ``--answers`` file with its header; ``None`` without the flag."""
    return gate_one.open_answers(
        args.answers,
        stamp=stamp,
        probe=probe,
        window=window,
        temperature=args.temperature,
        compact_json=args.compact_json,
        fit_limit=args.fit_limit,
    )


def _open_check(args: argparse.Namespace, window: int):
    """Return the port, the probe and the check of this invocation."""
    return gate_one.open_check(
        args.ollama_url,
        num_ctx=window,
        timeout=args.timeout,
        compact_json=args.compact_json,
        deadline=_deadline_of(args),
    )


def _print_model_block(port: OllamaChatPort, probe: ServerProbe, tag: str, profile: RequestProfile):
    """Print what the record says about a model before its requests: settings, weights, profile."""
    print(f"{tag}:")
    print(f"  {seed_settings(port, tag)}")
    print(f"  {_model_line(probe, tag)}")
    print(f"  {_profile_line(profile)}")


def _structured_output(args: argparse.Namespace) -> int:
    """Check the review answer format and return the exit code.

    The header says what the verdicts are valid for: the protocol the request was built from,
    the answer format, the window and the temperature.  A model in the catalog is checked
    against its flag and counted in the tally; a tag that is not in the catalog is a candidate
    that only gets a pass or fail, because no catalog entry has gone wrong.

    The smoke check is the first stage of every setup (``--no-smoke`` skips it), and
    ``--continue FILE`` finishes the setups of an answers file instead of starting them.
    """
    if args.continue_from:
        return _continue(args)
    try:
        stamp = protocol.current_stamp()
    except protocol.ProtocolError as error:
        print(error)
        return 2
    try:
        limit = _fit_limit_of(args)
    except ValueError as error:
        print(error)
        return 2
    window = _default_window(args.num_ctx)
    specs, candidates = _split_tags(args.tag)
    port, probe, check = _open_check(args, window)
    print("\n".join(_header_lines(args, stamp, probe, window, limit)))
    answers_file = _open_answers_file(args, stamp, probe, window)
    session = _Session(
        port=port,
        probe=probe,
        check=check,
        temperature=args.temperature,
        answers_file=answers_file,
        progress=Progress(0, style=args.progress),
        smoke=not args.no_smoke,
        fit=_fit_gate(args, port, probe, limit) if not args.no_smoke else None,
    )
    session.progress.grow(
        gate_one.expected_requests(
            [session.temperature_of(tag) for tag in [spec.tag for spec in specs] + candidates]
        )
    )
    failures = 0
    for spec in specs:
        passed = session.verdict(session.check_model(spec.tag))
        if passed is None:
            print(f"  → catalog: no verdict (currently {spec.structured_output}); not confirmed")
            failures += 1
            continue
        print(f"  → catalog: structured_output={passed} (currently {spec.structured_output})")
        if passed != spec.structured_output:
            failures += 1
    if specs:
        print(f"\n{len(specs) - failures}/{len(specs)} writing models verified")
    if candidates:
        failures += _candidates(candidates, session, window)
    if answers_file is not None:
        print(f"\nanswers saved to {answers_file}")
    return 1 if failures else 0


def _smoke(args: argparse.Namespace) -> int:
    """Screen every setup named on the command line and return the exit code.

    One block per setup in gate 1's line format, then the setup's line and its warnings, and a
    table at the end: the setup, its result, the requests it cost, the seconds and the reason.
    The answers go to gate 1's answers file, so ``structured-output --continue`` finishes a
    setup that passed without asking anything twice.
    """
    try:
        stamp = protocol.current_stamp()
    except protocol.ProtocolError as error:
        print(error)
        return 2
    try:
        limit = _fit_limit_of(args)
    except ValueError as error:
        print(error)
        return 2
    window = _default_window(args.num_ctx)
    specs, candidates = _split_tags(args.tag)
    tags = [spec.tag for spec in specs] + candidates
    if not tags:
        print("no model named: pass --tag, or a tag the catalog knows")
        return 2
    port, probe, check = _open_check(args, window)
    print("\n".join(_header_lines(args, stamp, probe, window, limit)))
    answers_file = _open_answers_file(args, stamp, probe, window)
    gate = _fit_gate(args, port, probe, limit)
    progress = Progress(0, style=args.progress)
    progress.grow(len(SMOKE_FIXTURES) * len(tags))
    rows: list[tuple[str, Smoke]] = []
    for tag in tags:
        temperature = defaults.temperature_for(tag, args.temperature)
        profile = check.profile_of(tag, temperature)
        _print_model_block(port, probe, tag, profile)
        failure, fit_lines = gate.check(tag, window)
        _print_lines([f"  {line}" for line in fit_lines])
        if failure is not None:
            label = setup_label(tag, profile, window)
            print(f"  {failure.line(label)}")
            rows.append((label, failure))
            progress.skip(len(SMOKE_FIXTURES))
            continue
        smoke = SmokeCheck(
            check,
            probe=probe,
            on_run=_print_smoke_run(check.rule, progress),
            on_sent=gate_one.answer_saver(tag, probe, answers_file, profile),
            on_request=_report_request(progress),
        ).run(tag, temperature)
        label = setup_label(tag, profile, window)
        print(f"  {smoke.line(label)}")
        _print_lines([f"  WARNING: {warning}" for warning in smoke.warnings])
        rows.append((label, smoke))
        if smoke.requests < len(SMOKE_FIXTURES):
            progress.skip(len(SMOKE_FIXTURES) - smoke.requests)
        else:
            progress.grow(smoke.requests - len(SMOKE_FIXTURES))
    print(_smoke_table(rows))
    if answers_file is not None:
        print(f"\nanswers saved to {answers_file}")
    return 0 if all(smoke.passed for _label, smoke in rows) else 1


def _deadline_of(args: argparse.Namespace) -> float | None:
    """Return the seconds a whole short answer may take, or ``None`` when ``--deadline 0``."""
    return args.deadline if args.deadline > 0 else None


def _fit(args: argparse.Namespace) -> int:
    """Measure the models' sizes, or report what the store holds, and return the exit code."""
    try:
        limits = [FitLimit.parse(text) for text in (args.fit_limit or DEFAULT_LIMITS)]
    except ValueError as error:
        print(error)
        return 2
    store = FitStore(args.store)
    if args.report:
        return _fit_report(args, store, limits)
    return _fit_measure(args, store)


def _fit_header(args: argparse.Namespace, store: FitStore, probe: Any, info: Any) -> None:
    """Print what a measuring run is valid for: the windows, the date, the server and the card."""
    print(f"── model sizes at {', '.join(f'{step:,}' for step in CONTEXT_STEPS)} tokens ──")
    print(f"date: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"command: {_command_line()}")
    print(f"tool commit: {tool_revision()}")
    print(f"ollama: {probe.version()}")
    print(f"server: {args.server}")
    print(f"store: {store.path}")
    if info is None:
        print("card: no agent, so Ollama's figures only")
        return
    cards = ", ".join(f"{card.name} ({card.total_bytes / 1e9:.1f} GB)" for card in info.cards)
    print(
        f"card: {info.platform} {info.vendor}/{info.source}, {cards or 'none'}, "
        f"agent {info.agent_sha256[:12]}"
    )


def _fit_measure(args: argparse.Namespace, store: FitStore) -> int:
    """Measure every named model at every window and append what is new to the store."""
    if not args.server:
        print("--server LABEL is required when measuring: a record without a server is worthless")
        return 2
    if args.gpu_agent is None and not args.no_agent:
        print("no --gpu-agent: pass the agent's URL, or --no-agent to store Ollama's figures only")
        return 2
    specs, candidates = _split_tags(args.tag)
    tags = [spec.tag for spec in specs] + candidates
    if not tags:
        print("no model named: pass --tag, or a tag the catalog knows")
        return 2
    agent = None
    info = None
    if args.gpu_agent is not None:
        try:
            token = _read_token(args.gpu_token_file)
            agent = AgentClient(args.gpu_agent, token=token)
            info = agent.info()
        except (AgentError, OSError) as error:
            print(f"the memory agent at {args.gpu_agent} cannot be used ({error})")
            return 2
    probe = ServerProbe(args.ollama_url)
    client = OllamaClient(args.ollama_url)
    catalog = {spec.tag: spec for spec in OLLAMA_CATALOG}
    _fit_header(args, store, probe, info)
    flagged = 0
    for tag in tags:
        temperature = defaults.temperature_for(tag, args.temperature)
        try:
            made = measure_tag(
                probe=probe,
                client=client,
                agent=agent,
                agent_info=info,
                store=store,
                server=args.server,
                tag=tag,
                temperature=temperature,
                again=args.again,
            )
        except AgentError as error:
            print(f"{tag}: the memory agent stopped answering ({error}); nothing more measured")
            return 2
        except (OllamaUnreachableError, OSError) as error:
            # An outage says nothing about the remaining models either.
            print(
                f"the server at {args.ollama_url} cannot be reached ({error}); "
                "run again when it is up"
            )
            return 2
        except (OCRError, FitMeasureError) as error:
            print(f"{tag}: {error}")
            flagged += 1
            continue
        if not made:
            print(f"{tag}: already measured at every window")
        for record in made:
            print(record_line(record))
        spec = catalog.get(tag)
        if made and spec is not None:
            print(
                "\n".join(
                    fit_report_lines(
                        tag,
                        [
                            record
                            for record in store.records()
                            if record.tag == tag and record.server == args.server
                        ],
                        catalog_memory_gb=spec.memory_gb,
                    )
                )
            )
    print(f"\n{len(tags) - flagged}/{len(tags)} models measured")
    return 1 if flagged else 0


def _fit_report(args: argparse.Namespace, store: FitStore, limits: Sequence[FitLimit]) -> int:
    """Report what the store holds: per model and server, the tiers, the cross-check."""
    records = store.records()
    if not records:
        print(f"no measurement in {store.path}: run `fit` on the machine with the card")
        return 1
    catalog = {spec.tag: spec for spec in OLLAMA_CATALOG}
    by_tag: dict[str, dict[str, list]] = {}
    for record in records:
        by_tag.setdefault(record.tag, {}).setdefault(record.server, []).append(record)
    print(f"── model sizes from {store.path} ──")
    for tag in sorted(by_tag):
        spec = catalog.get(tag)
        memory = spec.memory_gb if spec is not None else None
        for server in sorted(by_tag[tag]):
            print(
                "\n".join(
                    fit_report_lines(
                        tag, by_tag[tag][server], catalog_memory_gb=memory, limits=limits
                    )
                )
            )
        if args.compare:
            first, second = args.compare
            if first in by_tag[tag] and second in by_tag[tag]:
                print("\n".join(compare_lines(tag, by_tag[tag][first], by_tag[tag][second])))
            else:
                print(f"{tag}: {first} and {second} were not both measured")
        if args.catalog:
            tag_records = [record for servers in by_tag[tag].values() for record in servers]
            print("\n".join(catalog_lines(tag, tag_records, memory)))
    return 0


def _read_token(path: Path | None) -> str | None:
    """Return the agent's token, read from *path* when one is named."""
    if path is None:
        return None
    return path.read_text(encoding="utf-8").strip()


def _smoke_table(rows: Sequence[tuple[str, Smoke]]) -> str:
    """Return the table of the smoke check: setup, result, requests, seconds and reason."""
    width = max((len(label) for label, _smoke in rows), default=len("setup"))
    lines = [f"\n{'setup':<{width}}  result  requests  seconds  reason"]
    for label, smoke in rows:
        result = "PASS" if smoke.passed else "FAIL"
        lines.append(
            f"{label:<{width}}  {result:<6}  {smoke.requests:>8}  {smoke.seconds:>7.1f}  "
            f"{smoke.reason}"
        )
    return "\n".join(lines)


def _continue(args: argparse.Namespace) -> int:
    """Finish the setups of an answers file: judge what is there and send what is missing.

    The file is the smoke check's, or that of a run that was interrupted.  Its answers are
    judged by the current rule and count towards the decision; only the runs still missing are
    sent, and they are appended to the same file.  A setup whose smoke check failed is not
    finished unless ``--force`` is given, and never when a second try could not have cured it.
    """
    path = Path(args.continue_from)
    try:
        header, answers = gate1_answers.read_answers(path)
    except (OSError, ValueError) as error:
        print(f"{path}: {error}")
        return 2
    problem = gate_one.continue_problem(
        header, num_ctx=args.num_ctx, temperature=args.temperature, compact_json=args.compact_json
    )
    if problem is not None:
        print(f"{path}: {problem}")
        return 2
    try:
        stamp = protocol.current_stamp()
    except protocol.ProtocolError as error:
        print(error)
        return 2
    try:
        limit = _fit_limit_of(args)
    except ValueError as error:
        print(error)
        return 2
    window = header.num_ctx
    port, probe, check = _open_check(args, window)
    print("\n".join(_header_lines(args, stamp, probe, window, limit)))
    print(f"continuing {path.name}: {len(answers)} answers already made")
    progress = Progress(0, style=args.progress)
    failures = 0
    for tag, saved in gate1_answers.by_tag(answers).items():
        temperature = gate_one.temperature_of(saved[0], header)
        profile = check.profile_of(tag, temperature)
        _print_model_block(port, probe, tag, profile)
        if (why := gate_one.other_weights(saved, probe, tag)) is not None:
            print(f"  → refused: {why}")
            failures += 1
            continue
        try:
            previous = gate_one.judge_saved(tag, saved, header)
        except gate_one.StaleAnswersError as error:
            print(error)
            failures += 1
            continue
        decided = gate_one.smoke_gate(
            tag, previous, profile, window=window, rule=check.rule, force=args.force
        )
        _print_lines(list(decided.lines))
        if not decided.proceed:
            failures += 1
            continue
        done = {(answer.fixture, answer.round, answer.run) for answer in saved}
        progress.grow(gate_one.missing_requests(temperature, done))
        runs = check.run(
            tag,
            temperature,
            on_run=_print_run(check.rule, progress),
            on_retry=_print_retry(progress, temperature),
            on_sent=gate_one.answer_saver(tag, probe, path, profile),
            on_request=_report_request(progress),
            previous=previous,
            done=done,
        )
        all_runs = previous + runs
        _print_lines(report_lines(all_runs, check.rule))
        _print_lines(margin_lines(all_runs))
        failures += 0 if _print_decision(all_runs, check.rule) else 1
    return 1 if failures else 0


def _candidates(tags: list[str], session: _Session, window: int) -> int:
    """Check candidate *tags* and return how many of them failed."""
    verdicts = {tag: session.verdict(session.check_model(tag)) for tag in tags}
    passed = [tag for tag, verdict in verdicts.items() if verdict]
    print(f"\n{len(passed)}/{len(tags)} candidates pass the rule at num_ctx {window}")
    for tag, verdict in verdicts.items():
        print(f"  {tag}: {'NO VERDICT' if verdict is None else 'PASS' if verdict else 'FAIL'}")
    return len(tags) - len(passed)


def _header_lines(
    args: argparse.Namespace,
    stamp: protocol.Stamp,
    probe: ServerProbe,
    window: int,
    limit: FitLimit,
) -> list[str]:
    """Return the header of the record: what the verdicts below are valid for.

    A verdict is only valid for the answer format and the request it was made on, so both are
    named.  The commit is information only: what was compared is the fingerprint, which catches
    an uncommitted prompt edit as well as a committed one.
    """
    temperature = "each model's own" if args.temperature is None else f"{args.temperature:g}"
    return [
        f"── structured output at num_ctx {window}, temperature {temperature} ──",
        f"date: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"command: {_command_line()}",
        f"tool commit: {tool_revision()}",
        f"ollama: {probe.version()}",
        f"eval version {stamp.eval_version}, fingerprint {stamp.fingerprint}",
        f"answer format version {ANSWER_FORMAT_VERSION}",
        f"fit limit: {_limit_line(limit)}",
    ]


def _command_line() -> str:
    """Return the command that was run, without the server address.

    The address is left out because it says nothing about the check and would have to be
    redacted from a committed record.
    """
    kept: list[str] = []
    skip = False
    for item in sys.argv[1:]:
        if skip:
            skip = False
        elif item == "--ollama-url":
            skip = True
        elif not item.startswith("--ollama-url="):
            kept.append(item)
    return "verify_model_catalog " + " ".join(kept)


def _profile_line(profile: RequestProfile) -> str:
    """Return the request profile as one line, so the record says what was asked."""
    rule = "compact JSON" if profile.compact_json else "the plain answer"
    return f"request profile: {rule}, temperature {profile.temperature:g}"


def _model_line(probe: ServerProbe, tag: str) -> str:
    """Return the model's digest and parameter size as one line.

    The tag is a name that can be re-pointed; the digest pins the weights a verdict was made on.
    The quantisation is on the settings line above, which reads it from the same server.
    """
    info = gate_one.model_info(probe, tag)
    digest = str(info.get("digest", ""))[:12] or "?"
    return f"digest {digest}; parameters {info.get('parameter_size') or '?'}"


def _window_note(tag: str, window: int) -> str | None:
    """Return the note that the app runs *tag* at a larger window, or ``None``.

    The check runs at one window; the app picks one per tier.  When the automatic rule gives a
    model more than the checked window on a nominal card, a verdict made here does not cover
    what a teacher on that tier gets.
    """
    spec = next((candidate for candidate in OLLAMA_CATALOG if candidate.tag == tag), None)
    if spec is None:
        return None
    for profile, card_gb in NOMINAL_CARD_GB.items():
        if card_gb <= 0:
            continue
        automatic = automatic_num_ctx(
            spec,
            card_gb,
            cap=PROOFREADING_NUM_CTX_CAP,
            full_speed_share=FULL_SPEED_SHARE,
        )
        if automatic > window:
            return (
                f"  NOTE: the app runs this model at {automatic} on the {profile} tier; "
                "check it there too"
            )
    return None


def _print_lines(lines: list[str]) -> None:
    """Print *lines*, and nothing at all (not a blank line) when there are none."""
    if lines:
        print("\n".join(lines))


def _recheck(files: list[str]) -> int:
    """Judge saved answers again with the current rule and return the exit code.

    No server is needed: the answers are in the files.  A file whose fingerprint differs from
    the current one is refused, because a different request makes the answers stale, and so is a
    run whose text no longer hashes to what was sent.
    """
    rule = CheckRule()
    failures = 0
    for name in files:
        path = Path(name)
        try:
            header, answers = gate1_answers.read_answers(path)
        except (OSError, ValueError) as error:
            print(f"{path}: {error}")
            failures += 1
            continue
        current = protocol.fingerprint_sha256()
        if header.fingerprint != current:
            print(
                f"{path}: the answers were made with fingerprint {header.fingerprint}, the "
                f"current one is {current}: the request changed, so the answers are stale"
            )
            failures += 1
            continue
        temperature = (
            "each model's own" if header.temperature is None else f"{header.temperature:g}"
        )
        print(
            f"── recheck of {path.name}: num_ctx {header.num_ctx}, "
            f"temperature {temperature}, eval version {header.eval_version}, "
            f"answer format version {header.answer_format_version} ──"
        )
        for tag, saved in gate1_answers.by_tag(answers).items():
            try:
                runs = gate_one.judge_saved(tag, saved, header)
            except gate_one.StaleAnswersError as error:
                print(error)
                failures += 1
                continue
            print(f"{tag}:")
            _print_lines(report_lines(runs, rule))
            _print_lines(margin_lines(runs))
            failures += 0 if _print_decision(runs, rule) else 1
    return 1 if failures else 0


def _print_decision(runs: list[CheckRun], rule: CheckRule) -> bool:
    """Print what *runs* say about their model and return whether it is a pass."""
    rounds = max(run.round for run in runs)
    temperature = max(run.temperature for run in runs)
    decision = rule.decision(runs, rounds=rounds, can_retry=rounds == 1 and temperature > 0)
    if decision is Decision.MORE_RUNS:
        print("  → needs a second round")
        return False
    print(f"  → {'PASS' if decision is Decision.PASS else 'FAIL'}")
    return decision is Decision.PASS


def _print_retry(progress: Progress, temperature: float):
    """Return the callback that says the second round starts, and grows the progress total."""

    def announce(failures: int) -> None:
        progress.grow(len(FIXTURES) * (RUNS_AT_TEMPERATURE if temperature > 0 else 1))
        print(
            f"  → second round ({failures} soft failures in {len(FIXTURES) * RUNS_AT_TEMPERATURE})",
            flush=True,
        )

    return announce


def _print_run(rule: CheckRule, progress: Progress):
    """Return the per-run callback: print the report line of each run as it finishes."""

    def report(run, number: int, runs: int) -> None:
        line = report_lines([run], rule)[0]
        suite = f" (run {number}/{runs})" if runs > 1 else ""
        print(f"{line}{suite}", flush=True)
        progress.finish()

    return report


def _print_smoke_run(rule: CheckRule, progress: Progress):
    """Return the smoke check's per-run callback: gate 1's line, without a run number."""

    def report(run) -> None:
        print(report_lines([run], rule)[0], flush=True)
        progress.finish()

    return report


def _report_request(progress: Progress):
    """Return the per-request callback: name the request that starts, for the progress display."""

    def report(tag: str, fixture: str, run_number: int, attempt: int) -> None:
        label = f"{tag} · {fixture} · run {run_number}"
        if attempt > 1:
            label += f", try {attempt}"
        progress.start(label)

    return report


def _normalise(argv: list[str]) -> list[str]:
    """Map the older flag-only invocation onto a subcommand.

    A bare call stays the registry check it used to be, ``--tag`` used to imply the
    structured-output check, and ``--ollama`` was what enabled the capability check.  The
    help option has to reach the top-level parser, so it is passed through.
    """
    if argv and argv[0] in SUBCOMMANDS:
        return argv
    if not argv or argv[0] in ("-h", "--help"):
        return argv
    if "--context-growth" in argv:
        return ["fit", *[item for item in argv if item != "--context-growth"]]
    if "--structured-output" in argv:
        return ["structured-output", *[item for item in argv if item != "--structured-output"]]
    if "--ollama" in argv:
        return ["capabilities", *[item for item in argv if item != "--ollama"]]
    if "--tag" in argv:
        return ["structured-output", *argv]
    return ["sizes", *argv]


def _add_fit_options(parser: argparse.ArgumentParser) -> None:
    """Add the size limit and the memory agent: what a screen needs to check the fit."""
    parser.add_argument(
        "--fit-limit",
        default="16gb",
        metavar="LIMIT",
        help="Fail a setup whose model needs more than this at the checked window: 8gb, 16gb, a "
        "number of GB, or off (default %(default)s).",
    )
    parser.add_argument(
        "--fit-store",
        type=workspace_path,
        default=workspace_path(str(DEFAULT_STORE)),
        metavar="PATH",
        help="Where the size measurements are kept (default: %(default)s).",
    )
    parser.add_argument(
        "--server",
        default=None,
        metavar="LABEL",
        help="Short name of the machine, recorded with a measurement made here (default: the "
        "host of --ollama-url).",
    )
    parser.add_argument(
        "--gpu-agent",
        default=None,
        metavar="URL",
        help="The memory agent on the machine with the card, asked only when a model has to be "
        "measured.",
    )
    parser.add_argument(
        "--gpu-token-file",
        type=workspace_path,
        default=None,
        metavar="PATH",
        help="File holding the agent's token, sent as X-Token.",
    )


def _add_structured(sub, shared) -> None:
    """Add the ``structured-output`` subcommand: gate 1's eighteen runs per setup."""
    structured = sub.add_parser(
        "structured-output",
        parents=[shared],
        help="check that a model answers the review answer format reliably",
    )
    structured.add_argument(
        "--ollama-url",
        default=defaults.default_ollama_url(),
        help="Ollama server (default: $OLLAMA_HOST, else http://127.0.0.1:11434).",
    )
    structured.add_argument(
        "--temperature",
        type=float,
        default=None,
        metavar="X",
        help="Check at temperature X (default: each model's request profile, which is the "
        "temperature its vendor recommends, else the app's 0.2).",
    )
    structured.add_argument(
        "--compact-json",
        choices=defaults.COMPACT_JSON_CHOICES,
        default="catalog",
        help="Ask with the compact-answer rule: 'catalog' (each model's profile, default), 'on' "
        "or 'off' for every model.  The gate-1 run that decides the profiles compares 'off' "
        "with 'on'.",
    )
    structured.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        metavar="SECONDS",
        help="Seconds one request may wait for its first token (default %(default)g).  Raise it "
        "for a model that does not fit the graphics card and runs partly on the processor.",
    )
    structured.add_argument(
        "--deadline",
        type=float,
        default=SMOKE_DEADLINE,
        metavar="SECONDS",
        help="Seconds a whole answer to a short fixture may take (default %(default)g; 2.5 times "
        "that for the two long ones).  Past it the request is abandoned and counts as cut off, "
        "so a model that loops cannot hold the run.  0 sets no limit.",
    )
    structured.add_argument(
        "--num-ctx",
        type=int,
        choices=CONTEXT_STEPS,
        default=None,
        metavar="TOKENS",
        help=f"Check at this context window, one of {CONTEXT_STEPS} (default: "
        f"{BASE_NUM_CTX}, the window the app uses without a measurement).",
    )
    structured.add_argument(
        "--answers",
        type=workspace_path,
        default=None,
        metavar="DIR",
        help="Save every answer to DIR as JSON lines, so `recheck` can judge them again "
        "without a server.  The files stay local: they are large.",
    )
    structured.add_argument(
        "--progress",
        choices=STYLES,
        default="auto",
        help="How to show how far the run is, on stderr: 'auto' (a bar on a terminal, one line "
        "per request otherwise, default), 'lines' or 'off'.  Redirect stderr to keep it out of "
        "the record.",
    )
    structured.add_argument(
        "--no-smoke",
        action="store_true",
        help="Skip the smoke check and send the eighteen runs straight away.",
    )
    structured.add_argument(
        "--continue",
        dest="continue_from",
        type=workspace_path,
        default=None,
        metavar="FILE",
        help="Finish the setups of an answers file (the smoke check's, or an interrupted run's): "
        "judge what is there and send only the runs still missing.",
    )
    structured.add_argument(
        "--force",
        action="store_true",
        help="With --continue: finish a setup whose smoke check failed softly.  A hard failure "
        "is not continued either way.",
    )
    _add_fit_options(structured)
    structured.set_defaults(handler=_structured_output)


def _add_smoke(sub, shared) -> None:
    """Add the ``smoke`` subcommand: gate 1's cheap screen of every setup."""
    smoke = sub.add_parser(
        "smoke",
        parents=[shared],
        help="screen every setup with gate 1's cheapest fixtures before the long runs",
    )
    smoke.add_argument(
        "--ollama-url",
        default=defaults.default_ollama_url(),
        help="Ollama server (default: $OLLAMA_HOST, else http://127.0.0.1:11434).",
    )
    smoke.add_argument(
        "--temperature",
        type=float,
        default=None,
        metavar="X",
        help="Screen at temperature X (default: each model's request profile, which is the "
        "temperature its vendor recommends, else the app's 0.2).",
    )
    smoke.add_argument(
        "--compact-json",
        choices=defaults.COMPACT_JSON_CHOICES,
        default="catalog",
        help="Ask with the compact-answer rule: 'catalog' (each model's profile, default), 'on' "
        "or 'off' for every model.  A new profile is a new setup.",
    )
    smoke.add_argument(
        "--timeout",
        type=float,
        default=SMOKE_TIMEOUT,
        metavar="SECONDS",
        help="Seconds one request may wait for its first token (default %(default)g; the first "
        "request waits 600 s more, for the model to load).  The answer itself is bounded by "
        "--deadline.  A request that waited in vain is retried like any failure and is not saved "
        "as a run.",
    )
    smoke.add_argument(
        "--deadline",
        type=float,
        default=SMOKE_DEADLINE,
        metavar="SECONDS",
        help="Seconds a whole answer to a short fixture may take (default %(default)g; 2.5 times "
        "that for the two long ones).  Past it the request is abandoned and counts as cut off, "
        "so a model that loops cannot hold the run.  0 sets no limit.",
    )
    smoke.add_argument(
        "--num-ctx",
        type=int,
        choices=CONTEXT_STEPS,
        default=None,
        metavar="TOKENS",
        help=f"Screen at this context window, one of {CONTEXT_STEPS} (default: {BASE_NUM_CTX}).",
    )
    smoke.add_argument(
        "--answers",
        type=workspace_path,
        required=True,
        metavar="DIR",
        help="Save every answer to DIR as JSON lines: the smoke runs are gate 1's runs, so "
        "`structured-output --continue` finishes the setup from them.",
    )
    smoke.add_argument(
        "--progress",
        choices=STYLES,
        default="auto",
        help="How to show how far the run is, on stderr: 'auto' (a bar on a terminal, one line "
        "per request otherwise, default), 'lines' or 'off'.",
    )
    _add_fit_options(smoke)
    smoke.set_defaults(handler=_smoke)


def _add_fit(sub, shared) -> None:
    """Add the ``fit`` subcommand: measure what a model needs, or report the store."""
    fit = sub.add_parser(
        "fit",
        parents=[shared],
        aliases=["context-growth"],
        help="measure what each model needs, or report what the store holds",
    )
    fit.add_argument(
        "--ollama-url",
        default=defaults.default_ollama_url(),
        help="Ollama server (default: $OLLAMA_HOST, else http://127.0.0.1:11434).",
    )
    fit.add_argument(
        "--temperature",
        type=float,
        default=None,
        metavar="X",
        help="Measure at temperature X (default: each model's request profile, which is the "
        "temperature its vendor recommends, else the app's 0.2).",
    )
    fit.add_argument(
        "--server",
        default=None,
        metavar="LABEL",
        help="Short name of the machine measured, e.g. rx9070xt (required when measuring).  What "
        "it stands for is written once in evaluations/model-fit/README.md.",
    )
    fit.add_argument(
        "--store",
        type=workspace_path,
        default=workspace_path(str(DEFAULT_STORE)),
        metavar="PATH",
        help="Where the measurements are kept (default: %(default)s).",
    )
    fit.add_argument(
        "--gpu-agent",
        default=None,
        metavar="URL",
        help="The memory agent on the machine with the card, e.g. http://gpu-server.example:11436.",
    )
    fit.add_argument(
        "--gpu-token-file",
        type=workspace_path,
        default=None,
        metavar="PATH",
        help="File holding the agent's token, sent as X-Token.",
    )
    fit.add_argument(
        "--no-agent",
        action="store_true",
        help="Measure with Ollama's figures only.  The records are flagged: Ollama's size leaves "
        "out the driver's share and can be far off.",
    )
    fit.add_argument(
        "--again",
        action="store_true",
        help="Measure a window that is already stored (a new line, nothing rewritten).",
    )
    fit.add_argument(
        "--report",
        action="store_true",
        help="Report what the store holds; needs no server.",
    )
    fit.add_argument(
        "--compare",
        nargs=2,
        default=None,
        metavar=("A", "B"),
        help="Put two servers' measurements side by side, with the cross-check's agreement rule.",
    )
    fit.add_argument(
        "--catalog",
        action="store_true",
        help="Print the figures to enter in the catalog for each model.",
    )
    fit.add_argument(
        "--fit-limit",
        action="append",
        default=None,
        metavar="LIMIT",
        help="A size limit to report the largest fitting window for: 8gb, 16gb, a number of GB, "
        f"or off (repeatable; default: {', '.join(DEFAULT_LIMITS)}).",
    )
    fit.set_defaults(handler=_fit)


def _parser() -> argparse.ArgumentParser:
    """Return the command line: one subcommand per job."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="TAG",
        help="Only this model (repeatable).  Use it to check a candidate before adding it to "
        "the catalog.",
    )

    sizes = sub.add_parser("sizes", parents=[shared], help="compare the download estimates")
    sizes.set_defaults(handler=lambda args: _sizes(args.tag))

    capabilities = sub.add_parser(
        "capabilities", parents=[shared], help="ask a local server what each model can do"
    )
    capabilities.add_argument(
        "--ollama-url",
        default=defaults.default_ollama_url(),
        help="Ollama server (default: $OLLAMA_HOST, else http://127.0.0.1:11434).  From WSL in "
        "its default NAT mode the server on Windows is the host's address, not 127.0.0.1.",
    )
    capabilities.set_defaults(handler=lambda args: _capabilities(args.tag, args.ollama_url))

    _add_structured(sub, shared)
    _add_smoke(sub, shared)
    _add_fit(sub, shared)

    recheck = sub.add_parser(
        "recheck",
        help="judge saved answers again with the current rule, without a server",
    )
    recheck.add_argument("files", nargs="+", metavar="FILE", help="A file written by --answers.")
    recheck.set_defaults(handler=lambda args: _recheck(args.files))
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the subcommand named on the command line and return the exit code."""
    args = _parser().parse_args(_normalise(sys.argv[1:] if argv is None else argv))
    try:
        return int(args.handler(args))
    except ServerUnavailableError as error:
        print(f"the server cannot be reached ({error}); nothing was judged", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
