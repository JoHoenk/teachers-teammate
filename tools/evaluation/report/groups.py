"""The groups a report reads: runs that may be compared, and the numbers of each repeat.

Two questions need runs that were measured the same way: whether one model meets the floors on its
own numbers, and whether one model is better than another beyond the noise.  Both need the numbers
:func:`~.summary.summarise` prints for one repeat.  This module is that grouping: a
:class:`GroupKey` names what has to agree, a :class:`RunGroup` holds the runs that do, and
:class:`Range` is the spread of one number over the repeats.

The key is :func:`~..core.axes.series_key` and not a second list of fields: the axes table is what
the file name, the command lines and the page all read, so a group cannot disagree with a name.  It
holds the machine and the isolation, so the repeats of one setup on one machine are one group and two
machines are two; a comparison of quality reads the machine apart, because two machines measured the
same quality.  The fingerprint is not in it, and neither are the seed, the repeat and the timeout, so
the repeats of one measurement pool.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..core.axes import AXIS_BY_NAME, SAMPLING_PREFIX, series_key
from ..core.records import RunHeader
from . import pooled, summary
from .load import LoadedItem, LoadedRun, distinct_runs

DECIDING_LANGUAGES: tuple[str, ...] = pooled.DECIDING_LANGUAGES
"""The languages whose mean decides; French is reported only."""

PAGE_BAND = "page"
"""The band the comparison's guard reads: five texts per language, enough for "not worse"."""

PAGES_BAND = "pages"
"""The band with too few texts to decide on; a diagnostic."""

REPEATS = 3
"""How many repeats a group needs before a comparison judges it."""

MODEL_AXES: frozenset[str] = frozenset(
    {"model", "model_digest", "temperature", "sampling", "step_one_prompt", "compact_json"}
)
"""The axes a comparison may vary: each model is run with its own request, at its own temperature.

Every model starts at the temperature the app sends it, which differs between models, and a
refinement compares one model at two temperatures; the comparison prints both.  Everything else has
to agree, or the two runs answered different questions.
"""

ENVIRONMENT_AXES: frozenset[str] = frozenset({"machine", "isolated"})
"""The axes a comparison of quality reads apart: two machines measured the same quality.

Only speed needs them, which is why the speed table groups by the environment key instead.
"""

READ_APART: frozenset[str] = frozenset({"exchange", "steps", "explainer", "instructions"})
"""The axes a comparison reads apart from the measurement, because another axis already says them.

``exchange`` and ``steps`` are the mode, which a comparison asks for on its own; ``explainer`` is
ignored, because the corrections are the same whoever wrote the explanations.  ``instructions`` is
the hash of the instruction text per language, so it moves with the exchange, the steps and the
step-one prompt - all of which are compared on their own or may vary as a model's own request.
Requiring it to agree would refuse the prompt variants of one model, which is the comparison the
``results-prompt`` folder exists for.
"""


def _hashable(value: Any) -> Any:
    """Return *value* with every list a tuple, so a compare key can be a dict key."""
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _hashable(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_hashable(item) for item in value)
    return value


@dataclass(frozen=True)
class GroupKey:
    """What has to agree before two runs may be read together: the compare key, made hashable."""

    values: tuple[tuple[str, Any], ...]

    @classmethod
    def of(cls, header: RunHeader) -> GroupKey:
        """Return the key of *header*: its series key, made hashable."""
        return cls(values=_hashable(series_key(header)))

    @classmethod
    def series(cls, header: RunHeader) -> GroupKey:
        """Return the series key of *header*: the compare key plus the machine and the isolation.

        This is what a variant of the page is: the repeats of one setup on one machine.  It is the
        same key :func:`groups` groups by, so the page and a gate cannot disagree about which runs
        belong together.
        """
        return cls.of(header)

    def get(self, name: str) -> Any:
        """Return one axis of the key, or its default when the run left it at the default.

        The compare key holds only the axes that differ from their default, so a reader that asks
        for ``provider`` of an Ollama run gets ``ollama`` and not a ``KeyError``.

        Raises:
            KeyError: if the key has no such axis and the table has no default for it.
        """
        found = dict(self.values)
        if name in found:
            return found[name]
        axis = AXIS_BY_NAME.get(name)
        if axis is None or axis.default is None:
            raise KeyError(name)
        return _hashable(axis.default)

    @property
    def model(self) -> str:
        """The model tag."""
        return str(self.get("model"))

    @property
    def temperature(self) -> float:
        """The temperature the run asked at."""
        return float(self.get("temperature"))

    @property
    def testset(self) -> str:
        """The test set the run was asked about."""
        return str(self.get("testset"))

    @property
    def machine(self) -> str:
        """The machine the runs ran on, or ``""`` when the header names none."""
        return str(self.get("machine"))

    @property
    def isolated(self) -> bool:
        """Whether the runs ran with the graphics card to themselves."""
        return bool(self.get("isolated"))

    @property
    def exchange(self) -> str:
        """How the text travelled to the model and back."""
        return str(self.get("exchange"))

    @property
    def steps(self) -> tuple[str, ...]:
        """Which steps of the exchange ran."""
        return tuple(self.get("steps"))

    @property
    def mode(self) -> str:
        """How the run was asked, as :func:`~.pooled.mode_of` writes it.

        The same label :class:`~.load.LoadedItem` prints, so a table of groups and a table of items
        name one configuration the same way.
        """
        return pooled.mode_of(self.exchange, self.steps)

    def value_of(self, axis: str) -> str:
        """Return one axis as the text a table prints and a ``--where`` filter compares.

        A sampling option is read out of the ``sampling`` axis, so ``sampling.presence_penalty``
        names one option and not the whole dict.
        """
        if axis.startswith(SAMPLING_PREFIX):
            options = dict(self.get("sampling") or {})
            return axis_text(options.get(axis[len(SAMPLING_PREFIX) :], ""))
        return axis_text(self.get(axis))

    def measured_alike(self, other: GroupKey, *, corrections: bool = False) -> bool:
        """Whether *other* was measured the same way, apart from the model and its request.

        With *corrections*, the exchange modes that write the same corrections count as one and the
        explainer is ignored: step ① of the exchange writes the corrections of both, so a
        correct-only run and an exchange run of one model are the same corrections for about a
        quarter of the time.  Any other pair of exchanges stays not comparable, because review and
        text mode answer another question.
        """
        if corrections:
            if not (self._asks_exchange and other._asks_exchange):
                return False
            return self._measurement(corrections=True) == other._measurement(corrections=True)
        return self._measurement() == other._measurement()

    @property
    def _asks_exchange(self) -> bool:
        """Whether the run asked the app's exchange, in any of its step combinations."""
        return self.exchange == "text-first"

    def _measurement(self, *, corrections: bool = False) -> tuple[Any, ...]:
        """Return the parts of the key that a comparison may not vary."""
        shared = tuple(
            (name, value)
            for name, value in self.values
            if name not in MODEL_AXES and name not in READ_APART and name not in ENVIRONMENT_AXES
        )
        if corrections:
            return shared
        return (*shared, ("exchange", self.exchange), ("steps", self.steps))

    def order(self) -> tuple[Any, ...]:
        """Return a sort key, so a list of groups prints in the same order on every machine."""
        return (
            self.model,
            str(self.get("provider")),
            self.machine,
            self.isolated,
            self.exchange,
            self.steps,
            self.temperature,
            int(self.get("num_ctx")),
            repr(self.get("sampling")),
            str(self.get("model_digest")),
        )


def axis_text(value: Any) -> str:
    """Return an axis value as one readable string, the way a file name tag writes it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple)):
        return "+".join(axis_text(item) for item in value)
    return str(value)


@dataclass(frozen=True)
class Range:
    """One number over the repeats of a group: its values, and the spread they make."""

    values: tuple[float, ...]

    @classmethod
    def of(cls, values: Iterable[float]) -> Range:
        """Return the range of *values*.

        Raises:
            ValueError: if *values* is empty, because a range of nothing has no spread.
        """
        found = tuple(values)
        if not found:
            raise ValueError("a range needs at least one value")
        return cls(values=found)

    @property
    def low(self) -> float:
        """The smallest value."""
        return min(self.values)

    @property
    def high(self) -> float:
        """The largest value."""
        return max(self.values)

    @property
    def mean(self) -> float:
        """The mean of the values."""
        return sum(self.values) / len(self.values)


def beyond(a: Range, b: Range) -> bool:
    """Whether every value of *a* lies above every value of *b*.

    With three repeats each, that is a proper test at 5 %: for six exchangeable values the chance
    that all three of one model lie above all three of the other is 1/20.
    """
    return a.low > b.high


def within_the_spread(a: Range, b: Range) -> bool:
    """Whether neither range lies beyond the other: the two overlap."""
    return not beyond(a, b) and not beyond(b, a)


@dataclass(frozen=True)
class RunGroup:
    """The runs of one key, with their scored items, one entry per repeat."""

    key: GroupKey
    runs: Sequence[LoadedRun]
    items: tuple[tuple[LoadedItem, ...], ...]

    @property
    def repeats(self) -> tuple[int, ...]:
        """The repeat number of each run, in the order the runs are held."""
        return tuple(run.repeat for run in self.runs)

    @property
    def complete(self) -> bool:
        """Whether the group has the repeats a comparison needs."""
        return len(self.runs) >= REPEATS

    def summaries(self) -> tuple[tuple[summary.ModelSummary, ...], ...]:
        """Return the summaries of each repeat, as :func:`~.summary.summarise` prints them."""
        return tuple(tuple(summary.summarise(items)) for items in self.items)

    def first(self) -> LoadedRun:
        """Return the run of repeat 1: the one a spot-check sheet is drawn from.

        Raises:
            ValueError: if the group has no run of repeat 1.
        """
        for run in self.runs:
            if run.repeat == 1:
                return run
        raise ValueError(f"{self.key.model}: the group has no run of repeat 1")


def groups(runs: Iterable[LoadedRun]) -> list[RunGroup]:
    """Return the groups *runs* make, each with its repeats in order.

    A group is one series: the repeats of one setup on one machine.  Two files of one run key are
    one measurement written twice, which :func:`distinct_runs` settles before the grouping.
    """
    kept, _ = distinct_runs(runs)
    grouped: dict[GroupKey, list[LoadedRun]] = {}
    for run in kept:
        grouped.setdefault(GroupKey.of(run.header), []).append(run)
    built: list[RunGroup] = []
    for key in sorted(grouped, key=GroupKey.order):
        ordered = sorted(grouped[key], key=lambda run: run.repeat)
        built.append(
            RunGroup(
                key=key,
                runs=tuple(ordered),
                items=tuple(run.items for run in ordered),
            )
        )
    return built


def same_texts(a: RunGroup, b: RunGroup) -> bool:
    """Whether both groups hold the same texts, each run of a group the same as the others.

    A paired difference needs the same texts on both sides: a text one group has and the other does
    not would be counted as a change of the model.  A text is named by its id outside its own set
    file (``de/de-01``), so two sets that both number a text ``de-01`` are not mistaken for one.
    """
    if not same_items(a) or not same_items(b):
        return False
    return {item.text for item in a.items[0]} == {item.text for item in b.items[0]}


def same_items(group: RunGroup) -> bool:
    """Whether every run of *group* holds the same texts, so no item is missing from one."""
    sets = [{item.text for item in items} for items in group.items]
    return all(found == sets[0] for found in sets)


def comparable(a: RunGroup, b: RunGroup, *, corrections: bool = False) -> bool:
    """Whether a paired difference of *a* and *b* can be taken at all.

    The two were measured alike and hold the same texts.  The repeats are not asked for: whether
    there are enough of them is a question of the verdict, not of the number.
    """
    return a.key.measured_alike(b.key, corrections=corrections) and same_texts(a, b)


# ── the numbers a comparison reads, per repeat ───────────────────────────────


def language_row(
    summaries: Sequence[summary.ModelSummary], language: str
) -> summary.ModelSummary | None:
    """Return the row over all texts of one language, or ``None`` when the run has none."""
    return next(
        (summary for summary in summaries if not summary.band and summary.language == language),
        None,
    )


def deciding_rows(summaries: Sequence[summary.ModelSummary]) -> dict[str, summary.ModelSummary]:
    """Return the row over all texts of each deciding language."""
    return {
        code: row
        for code in DECIDING_LANGUAGES
        if (row := language_row(summaries, code)) is not None
    }


def deciding_mean_f_beta(summaries: Sequence[summary.ModelSummary]) -> float | None:
    """Return the mean F0.5 over the deciding languages that have a score, or ``None``."""
    values = [row.f_beta for row in deciding_rows(summaries).values() if row.f_beta is not None]
    return sum(values) / len(values) if values else None


def deciding_mean_lenient_f_beta(summaries: Sequence[summary.ModelSummary]) -> float | None:
    """Return the mean lenient F0.5 over the deciding languages that have one, or ``None``."""
    values = [
        value
        for row in deciding_rows(summaries).values()
        if (value := row.lenient_f_beta()) is not None
    ]
    return sum(values) / len(values) if values else None


def deciding_mean_gleu(summaries: Sequence[summary.ModelSummary]) -> float | None:
    """Return the mean GLEU over the deciding languages that have one, or ``None``."""
    return pooled.mean_gleu({code: row.gleu for code, row in deciding_rows(summaries).items()})


def band_mean_f_beta(
    summaries: Sequence[summary.ModelSummary], band: str = PAGE_BAND
) -> float | None:
    """Return the mean F0.5 of one band over the deciding languages, or ``None``."""
    values = [
        summary.f_beta
        for summary in summaries
        if summary.band == band
        and summary.language in DECIDING_LANGUAGES
        and summary.f_beta is not None
    ]
    return sum(values) / len(values) if values else None


def ranges(summaries: Sequence[Sequence[summary.ModelSummary]], measure) -> Range | None:
    """Return the range of *measure* over the repeats, or ``None`` when a repeat has no value.

    A repeat without the number cannot be left out: the spread would then be of fewer repeats than
    the group has, and a comparison would look tighter than it is.
    """
    values = [measure(part) for part in summaries]
    if any(value is None for value in values):
        return None
    return Range.of(value for value in values if value is not None)


__all__ = [
    "DECIDING_LANGUAGES",
    "ENVIRONMENT_AXES",
    "MODEL_AXES",
    "PAGES_BAND",
    "PAGE_BAND",
    "READ_APART",
    "REPEATS",
    "GroupKey",
    "Range",
    "RunGroup",
    "axis_text",
    "band_mean_f_beta",
    "beyond",
    "comparable",
    "deciding_mean_f_beta",
    "deciding_mean_gleu",
    "deciding_mean_lenient_f_beta",
    "deciding_rows",
    "groups",
    "language_row",
    "ranges",
    "same_items",
    "same_texts",
    "within_the_spread",
]
