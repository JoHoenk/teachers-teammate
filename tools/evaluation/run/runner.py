"""The run loop of ``evaluate.py run``: plan, resume, warm-up, one session at a time.

A run is a matrix: every answer mode, model, temperature and repeat, each writing one run file.
The loop here does what the command line used to do, so a test can drive it without a server.  It
plans every file before the first request — a conflict on the third model must not stop the run
after the first two have run for hours — and re-checks the resume when a session starts, because a
model can have been pulled again meanwhile.  Each session is wrapped in the measurement protocol of
:mod:`.measure`: an idle server if asked, the sampler, one request that is not scored (the model
loads there), then the texts.

Everything that reaches a server, a card or the clock is a seam (:class:`Seams`), so a test
replaces each of them.  The command line keeps the parsing and the printing.

A run file is named by its axes and carries the date it started (:func:`..core.axes.run_file_name`),
so a run resumed on a later day would otherwise write a second file and lose its resume.  The
identity of a run is its **run key**, which holds the repeat and the machine and has no date in it,
so :func:`run_path_for` looks for a stored file with the same key before it names a new one.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from teachers_teammate.exceptions import StageError

from ..core import axes
from ..core.formats import FormatError
from ..core.modes import Exchange, Setup, Step, setup_label
from ..core.records import Answer, RunFile, RunHeader, TestRecord, question_sha256
from ..store import layout, reader, writer
from . import defaults, measure, protocol
from . import session as sessions
from .app_corrector import CorrectorFactory
from .comparison import WritingModelComparison, text_of
from .measure import VramSampler
from .ports import ProbePort, RecordingPort, ServerUnavailableError
from .progress import Progress

if TYPE_CHECKING:
    from teachers_teammate.domain.model_catalog import RequestProfile
    from teachers_teammate.interfaces import Corrector

WARM_UP_TEXT = "Das ist ein Test."
"""What the unscored first request sends: short, so it costs next to nothing but loads the model."""


class SmokeVerdict(Protocol):
    """What the loop needs from a smoke check's result (``checks.smoke.Smoke`` satisfies it)."""

    @property
    def passed(self) -> bool:
        """Whether the setup may be run."""

    @property
    def reason(self) -> str:
        """Why it may not, in one line; empty when it passed."""


@dataclass(frozen=True)
class Matrix:
    """What one ``run`` asks for: the parsed command line, without argparse."""

    root: Path
    """The store the run writes into: its files go to ``runs/`` below it."""
    testset: str
    """The committed manifest's id (``gec-2026-12``), which the header records."""
    languages: tuple[str, ...]
    models: tuple[str, ...]
    temperatures: tuple[float | None, ...]
    """The temperatures to run; ``None`` is the model's own profile value."""
    setups: tuple[Setup, ...]
    """What to ask: each exchange with the steps of it that run; one run file per setup."""
    repeats: int
    """How often every text is sent; repeat *k* uses the seed of *k* and a file of its own."""
    base_seed: int
    """The seed of the first repeat; every repeat sends one, so a run is always reproducible."""
    timeout: float
    isolate: bool
    gpu_sampler: str | dict | None
    """The vendor of a sampler on this machine, or the agent's record (:func:`.measure.sampler_record`)."""
    machine: str
    cpu_only: bool = False
    """Whether every request keeps the model off the card (``--cpu-only``); a machine without a
    card is the one it is run for."""
    compact_json: str = "catalog"
    """``catalog`` (each model's own choice), ``on`` or ``off``; see ``--compact-json``."""
    options: tuple[tuple[str, float], ...] = ()
    """Sampling values sent with step ① besides the temperature (``--option``); empty: the tag."""
    prompt: str = ""
    """The step-① prompt variant (``--prompt``); empty: the model's own (the standard prompt unless
    a variant was adopted for it), ``standard``: the standard prompt whatever the model has."""
    question_sha256: str = ""
    """The hash of the whole test set, which a run of a subset still records.

    A ``--language de`` run answers the same question as a full run and only sends part of it, so
    the header names the set's hash and not the subset's; empty: the hash of the records given.
    """

    def seed_of(self, repeat: int) -> int:
        """Return the seed of *repeat* (1-based)."""
        return self.base_seed + repeat - 1

    def profile_of(self, model: str, temperature: float | None) -> RequestProfile:
        """Return the request profile *model* is asked with in this run."""
        return defaults.request_profile_for(
            model,
            compact_json=self.compact_json,
            temperature=temperature,
            options=self.options,
            prompt=self.prompt,
        )

    def temperature_of(self, model: str, asked: float | None) -> float:
        """Return the temperature *model* is asked at: *asked*, or the one its profile recommends."""
        return defaults.temperature_for(model, asked)


@dataclass(frozen=True)
class PlannedRun:
    """One run file of the matrix: one setup, model, temperature and repeat."""

    exchange: Exchange
    steps: tuple[Step, ...]
    model: str
    temperature: float
    repeat: int
    seed: int
    path: Path
    """The run file this session writes; a resume appends to the same one."""
    done: frozenset[str]
    """The ids stored in :attr:`path` when the run was planned.

    A session reads the file again when it starts and sends only the ids missing then.
    """


@dataclass(frozen=True)
class Plan:
    """What a run is going to do, decided before the first request."""

    sessions: tuple[PlannedRun, ...]
    complete: tuple[Path, ...]
    """Run files that already hold every text; the run skips them."""
    refused: tuple[str, ...]
    """Why a file may not be resumed into; a non-empty tuple stops the run before it starts."""
    dropped: tuple[str, ...] = ()
    """Torn answers that were dropped from a file before it was resumed into."""


@dataclass(frozen=True)
class Seams:
    """Everything that reaches a server, a card or the clock; a test replaces each."""

    probe: ProbePort
    new_client: Callable[[int], RecordingPort]
    new_factory: Callable[
        [RecordingPort, Exchange, tuple[Step, ...], RequestProfile], CorrectorFactory
    ]
    new_sampler: Callable[[], VramSampler | None]
    now: Callable[[], str]
    """The ISO time a session starts, as the header records it."""
    revision: str
    """The commit the tools are at, for a human reader; nothing checks it."""
    smoke: Callable[[str, Exchange, tuple[Step, ...], float], SmokeVerdict] | None = None
    """Screens a setup before its first session; ``None`` skips the stage (``--no-smoke``)."""


@dataclass(frozen=True)
class RunResult:
    """What a run did: why it stopped, how many sessions it finished, how many rows it wrote."""

    refused: tuple[str, ...]
    sessions: int
    rows: int
    skipped: tuple[str, ...] = ()
    """Sessions that did not run, each with the reason: a setup that failed its smoke check."""
    planned: int = 0
    """How many sessions the run planned; the ones that ran and ``skipped`` add up to it."""


def header_of(  # noqa: PLR0913  # one argument per fact the header records
    matrix: Matrix,
    *,
    exchange: Exchange,
    steps: tuple[Step, ...],
    model: str,
    temperature: float,
    repeat: int,
    seed: int,
    stamp: protocol.Stamp,
    probe: ProbePort,
    records: Sequence[TestRecord],
) -> RunHeader:
    """Return the header of one session: what it and every later session of its file share."""
    return measure.run_header(
        stamp=stamp,
        probe=probe,
        model=model,
        exchange=exchange,
        steps=steps,
        languages=matrix.languages,
        temperature=temperature,
        profile=matrix.profile_of(model, temperature),
        num_ctx=protocol.NUM_CTX,
        timeout=matrix.timeout,
        seed=seed,
        repeat=repeat,
        isolate=matrix.isolate,
        sampler=matrix.gpu_sampler,
        machine=matrix.machine,
        testset=matrix.testset,
        question_sha256=matrix.question_sha256 or question_sha256(records),
        texts=len(records),
    )


@dataclass(frozen=True)
class RunIndex:
    """The store's run files by run key, read once, so planning does not read every file per session.

    A run key names one file: the repeats of one setup on one machine are different runs, and the
    same setup on another machine is another run too.  A file a session is about to create joins the
    index, so the next session of the same key finds it instead of naming a second one.
    """

    root: Path
    by_key: Mapping[str, Path] = field(default_factory=dict)

    @classmethod
    def of(cls, root: Path) -> RunIndex:
        """Return the index of the store at *root*.

        Raises:
            ValueError: if two files carry one run key, naming both, because one of them is a copy.
        """
        return cls(root=root, by_key=reader.run_index(root))

    def path_for(self, header: RunHeader, date: str) -> Path:
        """Return the file *header* belongs to: the stored one of its run key, or a new name."""
        found = self.by_key.get(axes.run_key_sha256(header))
        if found is not None:
            return found
        return layout.run_path(self.root, axes.run_file_name(header, date))

    def with_file(self, path: Path, header: RunHeader) -> RunIndex:
        """Return the index with *path* added, for a file a session is about to create."""
        return replace(self, by_key={**self.by_key, axes.run_key_sha256(header): path})


def run_path_for(index: RunIndex, header: RunHeader, date: str) -> Path:
    """Return the file *header* belongs to: the stored one of its run key, or a new name for *date*.

    The name carries the date a run started, so a run resumed the next day would otherwise write a
    second file and lose its resume.  The run key is what a stored file is found by: it holds the
    repeat and the machine, so the second repeat of a setup and the same setup on another machine
    each get their own file instead of being taken for the first.
    """
    return index.path_for(header, date)


def _done_ids(path: Path) -> set[str]:
    """Return the ids *path* already answers, or none when it does not exist or cannot be read.

    A file that cannot be read is not hidden here: :func:`.session.resume_problem` reads it again
    and refuses the resume with the reason.
    """
    if not path.exists():
        return set()
    try:
        return reader.read_run(path).done()
    except FormatError, OSError:
        return set()


def _missing(records: Sequence[TestRecord], done: Collection[str]) -> list[TestRecord]:
    """Return the records of *records* that *done* does not already answer."""
    return [record for record in records if text_of(record) not in done]


def plan(
    matrix: Matrix,
    records: Sequence[TestRecord],
    stamp: protocol.Stamp,
    probe: ProbePort,
    *,
    date: str,
    index: RunIndex | None = None,
) -> Plan:
    """Return the sessions a run will write, the files already complete, and why any is refused.

    Nothing is sent to a model: the probe only reads the server's version and the model's
    details.  Every combination is checked here, before the first one runs, so a conflict on the
    third model does not stop the run after the first two have run for hours.

    Args:
        matrix: What to run.
        records: The test records, in file order.
        stamp: The protocol the run is made with, as :func:`.protocol.current_stamp` checked it.
        probe: Reads the server's version and the model's details.
        date: The day the run starts, used only to name a file that does not exist yet.
        index: The store's run files by run key; ``None`` reads them, once.
    """
    if index is None:
        try:
            index = RunIndex.of(matrix.root)
        except ValueError as error:
            return Plan((), (), (str(error),))
    planned: list[PlannedRun] = []
    complete: list[Path] = []
    refused: list[str] = []
    dropped: list[str] = []
    for exchange, steps in matrix.setups:
        for model in matrix.models:
            for asked in matrix.temperatures:
                temperature = matrix.temperature_of(model, asked)
                for repeat in range(1, matrix.repeats + 1):
                    seed = matrix.seed_of(repeat)
                    header = header_of(
                        matrix,
                        exchange=exchange,
                        steps=steps,
                        model=model,
                        temperature=temperature,
                        repeat=repeat,
                        seed=seed,
                        stamp=stamp,
                        probe=probe,
                        records=records,
                    )
                    path = run_path_for(index, header, date)
                    done = _done_ids(path)
                    if not _missing(records, done):
                        complete.append(path)
                        continue
                    item = PlannedRun(
                        exchange=exchange,
                        steps=steps,
                        model=model,
                        temperature=temperature,
                        repeat=repeat,
                        seed=seed,
                        path=path,
                        done=frozenset(done),
                    )
                    resume = sessions.resume_problem(path, header, done=done)
                    if resume.dropped:
                        dropped.append(resume.dropped)
                    if resume.problem:
                        refused.append(resume.problem)
                    else:
                        planned.append(item)
    return Plan(tuple(planned), tuple(complete), tuple(refused), tuple(dropped))


def run_matrix(  # noqa: PLR0913  # one callback per thing the command line says as it happens
    matrix: Matrix,
    records: Sequence[TestRecord],
    stamp: protocol.Stamp,
    seams: Seams,
    *,
    on_complete: Callable[[Path], None] = lambda _path: None,
    on_session: Callable[[PlannedRun], None] = lambda _session: None,
    on_row: Callable[[PlannedRun, Answer], None] = lambda _session, _answer: None,
    on_smoke: Callable[[PlannedRun, SmokeVerdict], None] = lambda _session, _verdict: None,
    on_dropped: Callable[[str], None] = lambda _message: None,
    new_progress: Callable[[int], Progress] | None = None,
) -> RunResult:
    """Run every session of *matrix* and return what it did.

    Args:
        matrix: What to run.
        records: The test records, in file order.
        stamp: The protocol the run is made with, as :func:`.protocol.current_stamp` checked it.
        seams: Everything that reaches a server, a card or the clock.
        on_complete: Called for a run file that already holds every text.
        on_session: Called when a session starts, before its first request.
        on_row: Called after each answer was written.
        on_smoke: Called with the session and the smoke check's result, before its first
            request; a setup that fails the smoke check is not run.
        on_dropped: Called with the message of a torn answer that was dropped from a run file
            before it was resumed into.
        new_progress: Builds the progress display from the number of texts the run will send;
            ``None`` shows nothing.  The figure is known here, before the first request.
    """
    try:
        index = RunIndex.of(matrix.root)
    except ValueError as error:
        # Two files of one run key: one of them is a copy, and nothing may be written beside them.
        return RunResult(refused=(str(error),), sessions=0, rows=0)
    decided = plan(matrix, records, stamp, seams.probe, date=seams.now()[:10], index=index)
    for message in decided.dropped:
        on_dropped(message)
    for path in decided.complete:
        on_complete(path)
    planned = len(decided.sessions)
    if decided.refused:
        return RunResult(refused=decided.refused, sessions=0, rows=0, planned=planned)
    missing = sum(len(_missing(records, item.done)) for item in decided.sessions)
    progress = Progress(0, style="off") if new_progress is None else new_progress(missing)
    finished = 0
    skipped: list[str] = []
    smoked: dict[tuple[str, Exchange, tuple[Step, ...], float], SmokeVerdict] = {}
    output = _Output(on_row, progress)
    try:
        for item in decided.sessions:
            setup = (item.model, item.exchange, item.steps, item.temperature)
            if seams.smoke is not None:
                if setup not in smoked:
                    verdict = seams.smoke(item.model, item.exchange, item.steps, item.temperature)
                    on_smoke(item, verdict)
                    smoked[setup] = verdict
                if not smoked[setup].passed:
                    skipped.append(f"{session_label(item)}: {smoked[setup].reason}")
                    progress.skip(len(_missing(records, item.done)))
                    continue
            on_session(item)
            # Hours may have passed since the run was planned: a model can have been pulled again.
            current = header_of(
                matrix,
                exchange=item.exchange,
                steps=item.steps,
                model=item.model,
                temperature=item.temperature,
                repeat=item.repeat,
                seed=item.seed,
                stamp=stamp,
                probe=seams.probe,
                records=records,
            )
            done = _done_ids(item.path)
            resume = sessions.resume_problem(item.path, current, done=done)
            if resume.dropped:
                on_dropped(resume.dropped)
            if resume.problem:
                return RunResult(
                    refused=(resume.problem,),
                    sessions=finished,
                    rows=output.rows,
                    skipped=tuple(skipped),
                    planned=planned,
                )
            _run_session(matrix, item, records, current, done, seams, output)
            index = index.with_file(item.path, current)
            finished += 1
    except measure.CardUsedError as error:
        return RunResult(
            refused=(str(error),),
            sessions=finished,
            rows=output.rows,
            skipped=tuple(skipped),
            planned=planned,
        )
    except ServerUnavailableError as error:
        # Nothing was written for the text that met the outage, so a resume sends it again.
        return RunResult(
            refused=(f"the server cannot be reached ({error}); nothing was judged, run again",),
            sessions=finished,
            rows=output.rows,
            skipped=tuple(skipped),
            planned=planned,
        )
    return RunResult(
        refused=(), sessions=finished, rows=output.rows, skipped=tuple(skipped), planned=planned
    )


def session_label(item: PlannedRun) -> str:
    """Return one session as a line names it: the model, its temperature, the setup and the repeat."""
    return (
        f"{item.model} @ {item.temperature:g} "
        f"({setup_label((item.exchange, item.steps))}), repeat {item.repeat}"
    )


@dataclass
class _Output:
    """What a run shows and counts as its answers are written, shared by all its sessions."""

    on_row: Callable[[PlannedRun, Answer], None]
    progress: Progress
    rows: int = 0
    """How many answers the sessions have written so far."""


def _server_unreachable(probe: ProbePort) -> bool:
    """Return whether the server does not answer, asked after a text failed.

    A failed answer can mean the model failed or the server went away; only the first says
    anything about the model.
    """
    try:
        probe.version()
    except OSError, ValueError:
        return True
    return False


def _run_session(
    matrix: Matrix,
    item: PlannedRun,
    records: Sequence[TestRecord],
    header: RunHeader,
    done: set[str],
    seams: Seams,
    output: _Output,
) -> None:
    """Send the missing texts of one session, counting the answers written in *output*.

    Raises:
        ServerUnavailableError: a text failed and the server no longer answers.  That answer is
            not written: an outage is not the model's failure.
    """
    client = seams.new_client(item.seed)
    factory = seams.new_factory(
        client, item.exchange, item.steps, matrix.profile_of(item.model, item.temperature)
    )
    measured = measure.MeasuredRun(
        client=client,
        probe=seams.probe,
        model=item.model,
        sampler=seams.new_sampler(),
        repeat=item.repeat,
        cpu_only=matrix.cpu_only,
    )
    warm_up = factory(item.model, item.temperature)
    unreachable = False
    try:
        preparation = measured.prepare(lambda: _warm_up(warm_up), isolate=matrix.isolate)
        if not item.path.exists():
            writer.start_run(item.path, RunFile(header=header))
        writer.append_session(
            item.path,
            measure.session_line(preparation, revision=seams.revision, now=seams.now()),
        )
        comparison = WritingModelComparison(factory, instrumentation=measured)
        for answer in comparison.run(
            records,
            model=item.model,
            temperature=item.temperature,
            done=done,
            on_request=lambda record: output.progress.start(f"{item.model} · {record.id}"),
        ):
            if answer.failed and _server_unreachable(seams.probe):
                unreachable = True
                raise ServerUnavailableError(answer.warning or "no answer")
            writer.append_answer(item.path, answer)
            output.rows += 1
            output.progress.finish()
            output.on_row(item, answer)
    finally:
        # Unloading a server that was just found dead would raise its own error and replace the
        # "server unreachable" message the caller needs.  The sampler is still stopped.
        measured.finish(unload=matrix.isolate and not unreachable)


def _warm_up(corrector: Corrector) -> None:
    """Load the model with one request that is not scored.

    A warm-up that fails is not reported: the first scored text sends the same request, and its
    row in the run file is where a failure belongs.
    """
    try:
        corrector.correct(WARM_UP_TEXT, "German")
    except StageError:
        pass


__all__ = [
    "WARM_UP_TEXT",
    "Matrix",
    "Plan",
    "PlannedRun",
    "RunIndex",
    "RunResult",
    "Seams",
    "SmokeVerdict",
    "header_of",
    "plan",
    "run_matrix",
    "run_path_for",
    "session_label",
]
