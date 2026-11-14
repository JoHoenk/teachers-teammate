"""The run loop of ``compare_writing_models run``: plan, resume, warm-up, one session at a time.

A run is a matrix: every answer mode, model, temperature and repeat, each writing one results
file.  The loop here does what the command line used to do, so a test can drive it without a
server.  It plans every file before the first request — a conflict on the third model must not
stop the run after the first two have run for hours — and re-checks the resume when a session
starts, because a model can have been pulled again meanwhile.  Each session is wrapped in the
measurement protocol of :mod:`.measure`: an idle server if asked, the sampler, one request that is
not scored (the model loads there), then the texts.

Everything that reaches a server, a card or the clock is a seam (:class:`Seams`), so a test
replaces each of them.  The command line keeps the parsing and the printing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import defaults, measure, protocol, results
from . import session as sessions
from .answer_mode import AnswerMode
from .app_corrector import CorrectorFactory
from .comparison import WritingModelComparison
from .measure import VramSampler
from .ports import ProbePort, RecordingPort, ServerUnavailableError
from .progress import Progress
from .results import ResultRow, TestRecord
from .smoke import Smoke

if TYPE_CHECKING:
    from teachers_teammate.domain.model_catalog import RequestProfile

WARM_UP_TEXT = "Das ist ein Test."
"""What the unscored first request sends: short, so it costs next to nothing but loads the model."""


@dataclass(frozen=True)
class Matrix:
    """What one ``run`` asks for: the parsed command line, without argparse."""

    out: Path
    """Where the results files go."""
    testset: Path
    """The directory of ``<language>.jsonl`` test records."""
    languages: tuple[str, ...]
    models: tuple[str, ...]
    temperatures: tuple[float | None, ...]
    """The temperatures to run; ``None`` is the model's own profile value."""
    modes: tuple[AnswerMode, ...]
    repeats: int
    """How often every text is sent; repeat *k* uses the seed of *k* and a file of its own."""
    base_seed: int | None
    """The seed of the first repeat; ``None`` sends no seed at all (``--no-seed``)."""
    timeout: float
    isolate: bool
    gpu_sampler: str | dict | None
    """The vendor of a sampler on this machine, or the agent's record (:func:`.layout.sampler_record`)."""
    machine: str
    compact_json: str = "catalog"
    """``catalog`` (each model's own choice), ``on`` or ``off``; see ``--compact-json``."""

    def seed_of(self, repeat: int) -> int | None:
        """Return the seed of *repeat* (1-based), or ``None`` when no seed is sent."""
        return None if self.base_seed is None else self.base_seed + repeat - 1

    def profile_of(self, model: str, temperature: float | None) -> RequestProfile:
        """Return the request profile *model* is asked with in this run."""
        return defaults.request_profile_for(
            model, compact_json=self.compact_json, temperature=temperature
        )

    def temperature_of(self, model: str, temperature: float | None) -> float:
        """Return the temperature a session sends: the given one, or the model's profile value."""
        return defaults.temperature_for(model, temperature)


@dataclass(frozen=True)
class Session:
    """One results file a run is going to write, and what is already in it."""

    mode: AnswerMode
    model: str
    temperature: float
    repeat: int
    seed: int | None
    rows_path: Path
    header_path: Path
    done: frozenset[str]
    """The ids stored in ``rows_path`` when the run was planned.

    A session reads the file again when it starts and sends only the ids missing then.
    """


@dataclass(frozen=True)
class Plan:
    """What a run is going to do, decided before the first request."""

    sessions: tuple[Session, ...]
    complete: tuple[Path, ...]
    """Results files that already hold every text; the run skips them."""
    refused: tuple[str, ...]
    """Why a file may not be resumed into; a non-empty tuple stops the run before it starts."""


@dataclass(frozen=True)
class Seams:
    """Everything that reaches a server, a card or the clock; a test replaces each."""

    probe: ProbePort
    new_client: Callable[[int | None], RecordingPort]
    new_factory: Callable[[RecordingPort, AnswerMode, RequestProfile], CorrectorFactory]
    new_sampler: Callable[[], VramSampler | None]
    now: Callable[[], str]
    """The ISO time a session starts, as the header records it."""
    revision: str
    """The commit the tools are at, for a human reader; nothing checks it."""
    smoke: Callable[[str, AnswerMode, float], Smoke] | None = None
    """Screens a setup before its first session; ``None`` skips the stage (``--no-smoke``)."""


@dataclass(frozen=True)
class RunResult:
    """What a run did: why it stopped, how many sessions it finished, how many rows it wrote."""

    refused: tuple[str, ...]
    sessions: int
    rows: int


def as_dict(profile: RequestProfile) -> dict:
    """Return *profile* as the header records it: its knobs, JSON-ready."""
    return {"compact_json": profile.compact_json, "temperature": profile.temperature}


def identity(
    matrix: Matrix,
    session: Session,
    stamp: protocol.Stamp,
    probe: ProbePort,
    records: Sequence[TestRecord],
) -> dict:
    """Return the header facts of *session*: what it and every later session of its file share."""
    return measure.run_identity(
        stamp=stamp,
        probe=probe,
        model=session.model,
        mode=session.mode,
        languages=matrix.languages,
        temperature=session.temperature,
        request_profile=as_dict(matrix.profile_of(session.model, session.temperature)),
        num_ctx=protocol.NUM_CTX,
        timeout=matrix.timeout,
        seed=session.seed,
        repeat=session.repeat,
        isolate=matrix.isolate,
        sampler_vendor=matrix.gpu_sampler or "",
        machine=matrix.machine,
        testset=matrix.testset.name,
        testset_sha256=results.records_sha256(records),
        texts=len(records),
    )


def plan(
    matrix: Matrix, records: Sequence[TestRecord], stamp: protocol.Stamp, probe: ProbePort
) -> Plan:
    """Return the sessions a run will write, the files already complete, and why any is refused.

    Nothing is sent to a model: the probe only reads the server's version and the model's
    details.  Every combination is checked here, before the first one runs, so a conflict on the
    third model does not stop the run after the first two have run for hours.
    """
    planned: list[Session] = []
    complete: list[Path] = []
    refused: list[str] = []
    for mode in matrix.modes:
        for model in matrix.models:
            for asked in matrix.temperatures:
                temperature = matrix.temperature_of(model, asked)
                for repeat in range(1, matrix.repeats + 1):
                    name = results.result_file_name(
                        model, temperature, mode, repeat if matrix.repeats > 1 else None
                    )
                    path = matrix.out / name
                    done = results.read_done_ids(path)
                    if not results.missing_ids(records, done):
                        complete.append(path)
                        continue
                    item = Session(
                        mode=mode,
                        model=model,
                        temperature=temperature,
                        repeat=repeat,
                        seed=matrix.seed_of(repeat),
                        rows_path=path,
                        header_path=path.with_suffix(".header.json"),
                        done=frozenset(done),
                    )
                    problem = sessions.resume_problem(
                        path,
                        item.header_path,
                        identity(matrix, item, stamp, probe, records),
                        done=done,
                    )
                    if problem:
                        refused.append(problem)
                    else:
                        planned.append(item)
    return Plan(tuple(planned), tuple(complete), tuple(refused))


def run_matrix(  # noqa: PLR0913  # one callback per thing the command line says as it happens
    matrix: Matrix,
    records: Sequence[TestRecord],
    stamp: protocol.Stamp,
    seams: Seams,
    *,
    on_complete: Callable[[Path], None] = lambda _path: None,
    on_session: Callable[[Session], None] = lambda _session: None,
    on_row: Callable[[Session, ResultRow], None] = lambda _session, _row: None,
    on_smoke: Callable[[Session, Smoke], None] = lambda _session, _smoke: None,
    new_progress: Callable[[int], Progress] | None = None,
) -> RunResult:
    """Run every session of *matrix* and return what it did.

    Args:
        matrix: What to run.
        records: The test records, in file order.
        stamp: The protocol the run is made with, as :func:`.protocol.current_stamp` checked it.
        seams: Everything that reaches a server, a card or the clock.
        on_complete: Called for a results file that already holds every text.
        on_session: Called when a session starts, before its first request.
        on_row: Called after each row was written.
        on_smoke: Called with the session and the smoke check's result, before its first
            request; a setup that fails the smoke check is not run.
        new_progress: Builds the progress display from the number of texts the run will send;
            ``None`` shows nothing.  The figure is known here, before the first request.
    """
    decided = plan(matrix, records, stamp, seams.probe)
    matrix.out.mkdir(parents=True, exist_ok=True)
    for path in decided.complete:
        on_complete(path)
    if decided.refused:
        return RunResult(refused=decided.refused, sessions=0, rows=0)
    missing = sum(len(records) - len(item.done) for item in decided.sessions)
    progress = Progress(0, style="off") if new_progress is None else new_progress(missing)
    finished = 0
    smoked: dict[tuple[str, AnswerMode, float], bool] = {}
    output = _Output(on_row, progress)
    try:
        for item in decided.sessions:
            setup = (item.model, item.mode, item.temperature)
            if seams.smoke is not None:
                if setup not in smoked:
                    smoke = seams.smoke(item.model, item.mode, item.temperature)
                    on_smoke(item, smoke)
                    smoked[setup] = smoke.passed
                if not smoked[setup]:
                    progress.skip(len(records) - len(item.done))
                    continue
            on_session(item)
            # Hours may have passed since the run was planned: a model can have been pulled again meanwhile.
            current = identity(matrix, item, stamp, seams.probe, records)
            done = results.read_done_ids(item.rows_path)
            problem = sessions.resume_problem(item.rows_path, item.header_path, current, done=done)
            if problem:
                return RunResult(refused=(problem,), sessions=finished, rows=output.rows)
            _run_session(matrix, item, records, current, done, seams, output)
            finished += 1
    except ServerUnavailableError as error:
        # Nothing was written for the text that met the outage, so a resume sends it again.
        return RunResult(
            refused=(f"the server cannot be reached ({error}); nothing was judged, run again",),
            sessions=finished,
            rows=output.rows,
        )
    return RunResult(refused=(), sessions=finished, rows=output.rows)


@dataclass
class _Output:
    """What a run shows and counts as its rows are written, shared by all its sessions."""

    on_row: Callable[[Session, ResultRow], None]
    progress: Progress
    rows: int = 0
    """How many rows the sessions have written so far."""


def _server_unreachable(probe: ProbePort) -> bool:
    """Return whether the server does not answer, asked after a text failed.

    A failed row can mean the model failed or the server went away; only the first says anything
    about the model.
    """
    try:
        probe.version()
    except (OSError, ValueError):
        return True
    return False


def _run_session(
    matrix: Matrix,
    item: Session,
    records: Sequence[TestRecord],
    current: dict,
    done: set[str],
    seams: Seams,
    output: _Output,
) -> None:
    """Send the missing texts of one session, counting the rows written in *output*.

    Raises:
        ServerUnavailableError: a text failed and the server no longer answers.  That row is not
            written: an outage is not the model's failure.
    """
    client = seams.new_client(item.seed)
    factory = seams.new_factory(client, item.mode, matrix.profile_of(item.model, item.temperature))
    measured = measure.MeasuredRun(
        client=client,
        probe=seams.probe,
        model=item.model,
        sampler=seams.new_sampler(),
        repeat=item.repeat,
    )
    warm_up = factory(item.model, item.temperature)
    unreachable = False
    try:
        preparation = measured.prepare(
            lambda: warm_up.correct(WARM_UP_TEXT, "German"), isolate=matrix.isolate
        )
        sessions.append_session(
            item.header_path,
            measure.session_header(
                current, preparation=preparation, revision=seams.revision, now=seams.now()
            ),
        )
        comparison = WritingModelComparison(factory, instrumentation=measured, mode=item.mode)
        for row in comparison.run(
            records,
            model=item.model,
            temperature=item.temperature,
            done=done,
            on_request=lambda record: output.progress.start(f"{item.model} · {record.id}"),
        ):
            if row.failed and _server_unreachable(seams.probe):
                unreachable = True
                raise ServerUnavailableError(row.warning or "no answer")
            results.write_rows(item.rows_path, [row])
            output.rows += 1
            output.progress.finish()
            output.on_row(item, row)
    finally:
        # Unloading a server that was just found dead would raise its own error and replace the
        # "server unreachable" message the caller needs.  The sampler is still stopped.
        measured.finish(unload=matrix.isolate and not unreachable)
