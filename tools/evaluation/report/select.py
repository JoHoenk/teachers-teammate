"""Which runs a report reads: the selection of one or two groups.

A command names a model, not a file: the runs of that model are found in the store, and the parts
of the key the command does not name are taken from the newest run of the model — its test set and
its machine.  The newest run is the newest one that matches the exchange, the steps, the temperature
and the eval version the command asks for, so an older run of another mode cannot decide which
evaluation is read.  A comparison takes the test set and the machine from the model it judges, not
from the one it compares against, so the two groups are always the same evaluation.

:func:`newest_series` is that rule on its own, for a command that reads every temperature or every
model of one series: it returns the machine and the test set, and the other series it could have
read, so the command can print what it chose.

What is left ambiguous is refused rather than guessed: two digests of one tag, or two request
profiles, are two models or two requests, and the command asks for ``--digest`` or
``--compact-json`` instead of picking one.

The temperatures are **given**, not looked up: the vendor's recommendation is the app's knowledge
and lives in :mod:`..run.defaults`, which this layer may not import (the import graph runs
``run → store``, never ``report → run``).  A command line resolves them and passes them in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from teachers_teammate.domain.model_tags import normalize_tag

from ..core import protocol_table
from ..core.axes import date_of, digest8, host_of
from ..core.modes import Exchange, Step
from . import groups
from .groups import RunGroup
from .load import Loaded, LoadedRun


@dataclass(frozen=True)
class Series:
    """The series a command reads: the machine and the test set of the newest run of a model."""

    machine: str
    """The machine the runs ran on, or the provider of an online service; ``""`` when unnamed."""
    testset: str
    date: str
    """The date of the newest run of the series, which is why it was chosen."""
    others: tuple[str, ...] = ()
    """The other series of the same model, as ``machine/testset``, for the line a command prints."""

    def describe(self) -> str:
        """Return the line a command prints when it chose this series."""
        line = (
            f"chose machine {self.machine or '(unnamed)'}, set {self.testset} (newest, {self.date})"
        )
        if self.others:
            line += f"; also stored: {', '.join(self.others)}"
        return line


@dataclass(frozen=True)
class Side:
    """One model a command names, and the axes it is read at.

    An axis that is ``None`` is not read apart: the command reads every value of it, which is what
    a comparison along that axis needs.
    """

    model: str
    temperature: float | None = None
    exchange: Exchange | None = None
    steps: tuple[Step, ...] | None = None


@dataclass(frozen=True)
class Selection:
    """What a report command asks for: one model, or two, and how to tell their runs apart."""

    model: str
    temperature: float | None = None
    """The temperature the runs of ``model`` are read at; the command line resolves it, and ``None``
    reads every temperature."""
    against: str | None = None
    against_temperature: float | None = None
    """The temperature of the runs of ``against``; ``None`` takes ``temperature``."""
    exchange: Exchange | None = None
    """The exchange the runs were asked with; ``None`` reads every exchange."""
    steps: tuple[Step, ...] | None = None
    """The steps of the exchange; ``None`` reads every combination."""
    against_exchange: Exchange | None = None
    """The exchange of the runs of ``against``; ``None`` takes ``exchange``."""
    against_steps: tuple[Step, ...] | None = None
    """The steps of the runs of ``against``; ``None`` takes ``steps``."""
    machine: str | None = None
    """The machine whose runs are read, or the provider of an online service; ``None`` takes the
    newest run's."""
    testset: str | None = None
    """The test set whose runs are read; ``None`` takes the newest run's."""
    digest: str | None = None
    """The digest of the model, when two digests of one tag are stored."""
    compact_json: bool | None = None
    """The compact-JSON rule of the request profile, when two profiles are stored."""
    eval_version: int | None = None
    """The eval version; ``None`` takes the current one, because older runs measured another
    request."""

    def sides(self) -> tuple[Side, ...]:
        """Return the side to judge first, then the one to compare against when there is one.

        The second side takes its own temperature, exchange and steps when the command names them,
        and the first side's otherwise: a comparison of two models at their own starting points
        names neither, and a refinement of one model at two temperatures names both.
        """
        first = Side(self.model, self.temperature, self.exchange, self.steps)
        if self.against is None:
            return (first,)
        return (
            first,
            Side(
                self.against,
                self.temperature if self.against_temperature is None else self.against_temperature,
                self.against_exchange or self.exchange,
                self.against_steps if self.against_steps is not None else self.steps,
            ),
        )


def select(loaded: Loaded, wanted: Selection) -> list[RunGroup]:
    """Return one group per side *wanted* names, in the order it names them.

    Args:
        loaded: The store as :func:`~.load.load` read it.
        wanted: The model or models, and the parts of the key the command fixed.

    Raises:
        ValueError: if no run matches, or the runs of one side make more than one group.
    """
    return select_runs(loaded.runs, wanted)


def select_runs(runs: Sequence[LoadedRun], wanted: Selection) -> list[RunGroup]:
    """Return one group per side *wanted* names, from the runs of a store.

    The test set and the machine are resolved once, from the newest run of the model the command
    judges, and every model is read on them: a comparison of two models is a comparison of one
    evaluation, so ``against`` cannot bring its own test set or machine.

    Raises:
        ValueError: if no run matches, or the runs of one side make more than one group.
    """
    if not runs:
        raise ValueError("no run in the store")
    version = wanted.eval_version if wanted.eval_version is not None else _current_version()
    chosen: list[RunGroup] = []
    anchor: tuple[str, str] | None = None
    for side in wanted.sides():
        mine = [run for run in runs if _tag(run) == normalize_tag(side.model)]
        if not mine:
            raise ValueError(f"no run of {side.model!r} in the store")
        if anchor is None:
            anchor = _anchor(mine, wanted, version, side)
        chosen.extend(_groups(_narrow(mine, wanted, version, anchor, side), side.model))
    return chosen


def _current_version() -> int:
    """Return the eval version of the code, which is the one a new run would carry."""
    return protocol_table.current_version()


def _tag(run: LoadedRun) -> str:
    """Return the normalised model tag of *run*."""
    return normalize_tag(run.header.protocol.model)


def _date(run: LoadedRun) -> str:
    """Return the date of the last session of *run*, or ``""`` when it has none."""
    return run.run.sessions[-1].date if run.run.sessions else ""


def _host(run: LoadedRun) -> str:
    """Return the machine of *run*, or the provider of an online service."""
    return host_of(run.header)


def _version(run: LoadedRun) -> int | None:
    """Return the eval version of *run*, or ``None`` when its fingerprint is not in the table."""
    return protocol_table.version_of(run.header.protocol.fingerprint)


def newest_series(
    runs: Sequence[LoadedRun],
    *,
    model: str | None = None,
    machine: str | None = None,
    testset: str | None = None,
    exchange: Exchange | None = None,
    steps: Sequence[Step] | None = None,
    temperature: float | None = None,
    version: int | None = None,
) -> Series:
    """Return the series a command reads: the machine and the test set of the newest run.

    The newest run is the newest one that matches what the command named, at the eval version of the
    code, so an older run of another mode or of an older request cannot decide which evaluation is
    read.  A part the command named is kept as given, so ``--machine`` overrides the choice.

    Args:
        runs: The runs of the store, as :func:`~.load.load` read them.
        model: The model tag whose runs are read; ``None`` reads every model.
        machine: The machine the command named, or ``None`` to take the newest run's.
        testset: The test set the command named, or ``None`` to take the newest run's.
        exchange: The exchange the command named, or ``None`` for every exchange.
        steps: The steps the command named, or ``None`` for every combination.
        temperature: The temperature the command named, or ``None`` for every temperature.
        version: The eval version, or ``None`` for the current one.

    Returns:
        The series, with the other series of the same model for the line a command prints.

    Raises:
        ValueError: if no run matches what the command named.
    """
    wanted_version = _current_version() if version is None else version
    mine = [
        run
        for run in runs
        if (model is None or _tag(run) == normalize_tag(model)) and _version(run) == wanted_version
    ]
    matching = [
        run
        for run in mine
        if (machine is None or _host(run) == machine)
        and (testset is None or run.header.input.testset == testset)
        and _reads(run, exchange=exchange, steps=steps, temperature=temperature)
    ]
    if not matching:
        raise ValueError(
            f"no run of {model or 'any model'} at eval version {wanted_version}, "
            f"{_exchange_text(exchange)} with steps {_steps(steps)}, "
            f"temperature {_temperature_text(temperature)}, "
            f"on {testset or 'any test set'} at {machine or 'any machine'}"
        )
    newest = max(matching, key=_date)
    chosen = (machine or _host(newest), testset or newest.header.input.testset)
    others = sorted(
        {
            f"{_host(run)}/{run.header.input.testset}"
            for run in mine
            if (_host(run), run.header.input.testset) != chosen
        }
    )
    return Series(
        machine=chosen[0], testset=chosen[1], date=date_of(_date(newest)), others=tuple(others)
    )


def _reads(
    run: LoadedRun,
    *,
    exchange: Exchange | None,
    steps: Sequence[Step] | None,
    temperature: float | None,
) -> bool:
    """Return whether *run* was asked the way the command named; ``None`` names no value."""
    protocol = run.header.protocol
    return (
        (exchange is None or protocol.exchange is exchange)
        and (steps is None or protocol.steps == tuple(steps))
        and (temperature is None or protocol.temperature == temperature)
    )


def _anchor(
    runs: Sequence[LoadedRun], wanted: Selection, version: int, side: Side
) -> tuple[str, str]:
    """Return the test set and the machine of the newest run that matches *side*.

    The exchange, the steps, the temperature and the eval version are filtered first, so the newest
    run of another mode or of an older request cannot decide which evaluation is read.

    Raises:
        ValueError: if no run of the model matches the exchange, the steps, the temperature and the
            version.
    """
    series = newest_series(
        runs,
        model=side.model,
        machine=wanted.machine,
        testset=wanted.testset,
        exchange=side.exchange,
        steps=side.steps,
        temperature=side.temperature,
        version=version,
    )
    return (series.testset, series.machine)


def _narrow(
    runs: Sequence[LoadedRun],
    wanted: Selection,
    version: int,
    anchor: tuple[str, str],
    side: Side,
) -> list[LoadedRun]:
    """Return the runs of one model that match *side* on the evaluation *anchor* names.

    Raises:
        ValueError: if nothing matches, or two digests or two profiles remain.
    """
    testset, machine = anchor
    kept = [
        run
        for run in runs
        if run.header.input.testset == testset
        and _host(run) == machine
        and _version(run) == version
        and _reads(run, exchange=side.exchange, steps=side.steps, temperature=side.temperature)
    ]
    if not kept:
        raise ValueError(
            f"no run of {side.model!r} on {testset} at {machine}, eval version {version}, "
            f"{_exchange_text(side.exchange)} with steps {_steps(side.steps)}, "
            f"temperature {_temperature_text(side.temperature)}"
        )
    return _one_profile(_one_digest(kept, wanted), wanted)


def _one_digest(runs: Sequence[LoadedRun], wanted: Selection) -> list[LoadedRun]:
    """Return the runs of one digest, refusing two when the command named none.

    Raises:
        ValueError: if two digests remain and ``digest`` was not given.
    """
    digests = sorted({_digest(run) for run in runs})
    if wanted.digest is not None:
        kept = [run for run in runs if _digest(run).startswith(wanted.digest)]
        if not kept:
            raise ValueError(f"no run of {wanted.model!r} with digest {wanted.digest!r}")
        return kept
    if len(digests) > 1:
        raise ValueError(
            f"{wanted.model!r} has {len(digests)} digests stored "
            f"({', '.join(digest[:12] for digest in digests)}): give --digest"
        )
    return list(runs)


def _one_profile(runs: Sequence[LoadedRun], wanted: Selection) -> list[LoadedRun]:
    """Return the runs of one request profile, refusing two when the command named none.

    Raises:
        ValueError: if two profiles remain and ``compact_json`` was not given.
    """
    profiles = sorted({run.header.protocol.compact_json for run in runs})
    if wanted.compact_json is not None:
        kept = [run for run in runs if run.header.protocol.compact_json == wanted.compact_json]
        if not kept:
            raise ValueError(
                f"no run of {wanted.model!r} with compact JSON "
                f"{'on' if wanted.compact_json else 'off'}"
            )
        return kept
    if len(profiles) > 1:
        raise ValueError(
            f"{wanted.model!r} has {len(profiles)} request profiles stored "
            "(compact JSON on and off): give --compact-json"
        )
    return list(runs)


def _digest(run: LoadedRun) -> str:
    """Return the model digest of *run*, without the ``sha256:`` prefix."""
    digest = run.header.protocol.model_digest
    return digest.split(":", 1)[1] if ":" in digest else digest


def _steps(steps: Sequence[Step] | None) -> str:
    """Return the steps as a readable list, for a message."""
    if steps is None:
        return "any"
    return "+".join(step.value for step in steps) or "none"


def _exchange_text(exchange: Exchange | None) -> str:
    """Return the exchange as a message names it."""
    return "any exchange" if exchange is None else exchange.value


def _temperature_text(temperature: float | None) -> str:
    """Return the temperature as a message names it."""
    return "any" if temperature is None else f"{temperature:g}"


def _groups(runs: Sequence[LoadedRun], model: str) -> list[RunGroup]:
    """Return the one group *runs* make.

    Raises:
        ValueError: if the runs make more than one group.
    """
    found = groups.groups(runs)
    if len(found) != 1:
        raise ValueError(
            f"{model!r}: the runs make {len(found)} groups (they differ in the machine or "
            "provider, the test set, the temperature or the request profile); "
            "give --machine, --testset or --temperature to choose one"
        )
    return found


@dataclass(frozen=True)
class Narrowing:
    """What a command line narrows a store's groups to, besides the series it reads.

    An axis that is ``None`` is not narrowed: every value of it is kept, which is what a comparison
    along that axis needs.
    """

    model: str | None = None
    temperature: float | None = None
    exchange: Exchange | None = None
    steps: tuple[Step, ...] | None = None
    digest: str | None = None
    """The model digest, when two digests of one tag are stored."""
    compact_json: bool | None = None
    """The compact-JSON rule of the request profile, when two profiles are stored."""
    prompt: str | None = None
    """The step-① prompt variant (``standard`` for the standard prompt), when two are stored."""


def series_of(
    runs: Sequence[LoadedRun],
    *,
    model: str | None = None,
    machine: str | None = None,
    testset: str | None = None,
) -> tuple[Series, list[RunGroup]]:
    """Return the series a command reads, and the groups of the runs on it.

    The machine and the test set are the ones the newest run of *model* ran on, unless the command
    names them; the series says which it chose, so a command can print it and a report never
    silently reads another machine than the one expected.

    Args:
        runs: The runs of the store, as :func:`~.load.load` read them.
        model: The model tag whose runs are read; ``None`` reads every model.
        machine: The machine the command named, or ``None`` to take the newest run's.
        testset: The test set the command named, or ``None`` to take the newest run's.

    Returns:
        The series, and the groups of the runs that ran on it.

    Raises:
        ValueError: if no run matches, or the runs of one model make more than one group.
    """
    series = newest_series(runs, model=model, machine=machine, testset=testset)
    kept = [
        run
        for run in runs
        if run.header.input.testset == series.testset and _host(run) == series.machine
    ]
    return series, groups.groups(kept)


def narrow(chosen: Sequence[RunGroup], wanted: Narrowing) -> list[RunGroup]:
    """Return the groups *wanted* selects: model, temperature, exchange, steps, profile, prompt."""
    found = list(chosen)
    if wanted.model:
        tag = normalize_tag(wanted.model)
        found = [group for group in found if normalize_tag(group.key.model) == tag]
    if wanted.temperature is not None:
        found = [group for group in found if group.key.temperature == wanted.temperature]
    if wanted.exchange is not None:
        found = [group for group in found if group.key.exchange == wanted.exchange.value]
    if wanted.steps is not None:
        steps = tuple(step.value for step in wanted.steps)
        found = [group for group in found if group.key.steps == steps]
    if wanted.digest:
        found = [
            group for group in found if digest8(group.key.get("model_digest")) == wanted.digest
        ]
    if wanted.compact_json is not None:
        found = [group for group in found if group.key.get("compact_json") is wanted.compact_json]
    if wanted.prompt:
        found = [group for group in found if group.key.get("step_one_prompt") == wanted.prompt]
    return found


__all__ = [
    "Narrowing",
    "Selection",
    "Series",
    "Side",
    "narrow",
    "newest_series",
    "select",
    "select_runs",
    "series_of",
]
