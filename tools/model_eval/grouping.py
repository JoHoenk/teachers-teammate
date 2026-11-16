"""The groups gate 2 reads: runs that may be compared, and the numbers of each repeat.

Gate 2 asks two questions of runs: whether one model meets the floors on its own numbers, and
whether one model is better than another beyond the noise.  Both need runs that were measured the
same way, and both need the numbers :func:`~.summary.summarise` prints for one repeat.  This module
is that grouping: a :class:`GroupKey` names what has to agree, a :class:`RunGroup` holds the runs
that do, and :class:`Range` is the spread of one number over the repeats.

A run is read from a committed file (:class:`~.run_format.StoredRun`) or from a local result folder
(:class:`~.viewer.load.CacheRun`); :class:`RunLike` is what a group needs from either, so the
comparison reads both and only the explanation sample insists on a committed one.

This is the contract gate 2 needs from the report and the grouping of a screen.  It must not import :mod:`.validation`, :mod:`.export`, :mod:`.index` or the command
line.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from . import scoring, summary
from .answer_mode import AnswerMode
from .run_format import RunHeader, StoredItem, StoredRun

DECIDING_LANGUAGES: tuple[str, ...] = scoring.DECIDING_LANGUAGES
"""The languages whose mean decides; French is reported only."""

PAGE_BAND = "page"
"""The band the comparison's guard reads: five texts per language, enough for "not worse"."""

PAGES_BAND = "pages"
"""The band with too few texts to decide on; a diagnostic."""

REPEATS = 3
"""How many repeats a group needs before gate 2 judges it."""


class RunLike(Protocol):
    """What a group needs from one run, whether it is committed or in a local result folder."""

    @property
    def meta(self) -> RunHeader:
        """The typed header of the run: what it was measured under, and its repeat number."""


@dataclass(frozen=True)
class GroupKey:
    """What has to agree before two runs may be read together.

    Everything a run was measured under, except the model itself: a group is one model on one
    test set, at one window, with one request profile, from one provider on one machine.  The
    digest and the profile are part of it because two digests of one tag are two models, and two
    profiles are two requests; the provider is, because one model name served by two providers is
    two services, and a run of a provider that ran on no machine of ours has no digest to tell
    them apart by.
    """

    testset: str
    manifest_sha256: str
    eval_version: int
    provider: str
    machine: str
    model: str
    digest: str
    request_profile: tuple[tuple[str, object], ...]
    mode: AnswerMode
    explainer: str | None
    temperature: float
    num_ctx: int

    @classmethod
    def of(cls, run: StoredRun) -> GroupKey:
        """Return the key of *run*, from its header."""
        return cls.of_header(run.meta)

    @classmethod
    def of_header(cls, meta: RunHeader) -> GroupKey:
        """Return the key of a typed header, committed or from a local cache."""
        return cls(
            testset=meta.testset,
            manifest_sha256=meta.manifest_sha256,
            eval_version=meta.eval_version,
            provider=meta.provider,
            machine=meta.machine,
            model=meta.model,
            digest=meta.digest,
            request_profile=meta.request_profile,
            mode=meta.mode,
            explainer=meta.explainer,
            temperature=meta.temperature,
            num_ctx=meta.num_ctx,
        )

    @classmethod
    def of_local(cls, header: Mapping[str, Any]) -> GroupKey:
        """Return the key of a run in a local cache, from its header dict.

        A cache's header is the dict a run writes next to its rows, not a committed run's line 1:
        it has no ``run_id`` and names the texts by ``testset_sha256``, the hash of the test
        records, where an exported run names them by ``manifest_sha256``, the hash of the
        manifest.  :class:`~.run_format.RunHeader` reads both into one field, so the keys of two
        cache runs compare as they should.  A cache run's key is **not** equal to the key of the
        exported run of the same measurement, because the two hashes are different numbers.

        Raises:
            KeyError: if the header lacks a field the key is made of.
            ValueError: if a field has a value of the wrong kind.
        """
        return cls.of_header(RunHeader.from_dict(header))

    def measured_alike(self, other: GroupKey, *, corrections: bool = False) -> bool:
        """Whether *other* was measured the same way, apart from the model and its request.

        That is what :func:`~.gate_two.compare` allows: each model is run with its own request, so
        its digest, its profile and its temperature may differ, and nothing else may.  Every model
        starts at the temperature its vendor recommends, which differs between models, and
        a refinement compares one model at two temperatures; the comparison prints both.

        With *corrections*, the two exchange modes count as one and the explainer is ignored: step
        ① of the exchange writes the corrections of both, so a correct-only run and an exchange
        run of one model are the same corrections for about a quarter of the time.  Any other pair
        of modes stays not comparable, because review and text mode answer another question.
        """
        if corrections:
            if not (self.mode.asks_exchange and other.mode.asks_exchange):
                return False
            return self._measurement(corrections=True) == other._measurement(corrections=True)
        return self._measurement() == other._measurement()

    def _measurement(self, *, corrections: bool = False) -> tuple[object, ...]:
        """Return the parts of the key that a comparison may not vary."""
        shared = (
            self.testset,
            self.manifest_sha256,
            self.eval_version,
            self.provider,
            self.machine,
        )
        if corrections:
            return (*shared, self.num_ctx)
        return (*shared, self.mode, self.explainer, self.num_ctx)

    def order(self) -> tuple[object, ...]:
        """Return a sort key, so a list of groups prints in the same order on every machine."""
        return (
            self.model,
            self.provider,
            self.mode.value,
            self.temperature,
            self.num_ctx,
            repr(self.request_profile),
            self.digest,
        )


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

    With three repeats each, that is a proper test at 5 %: for six exchangeable values
    the chance that all three of one model lie above all three of the other is 1/20.
    """
    return a.low > b.high


def within_the_spread(a: Range, b: Range) -> bool:
    """Whether neither range lies beyond the other: the two overlap."""
    return not beyond(a, b) and not beyond(b, a)  # pylint: disable=arguments-out-of-order


@dataclass(frozen=True)
class RunGroup:
    """The runs of one key, with their scored items, one entry per repeat."""

    key: GroupKey
    runs: Sequence[RunLike]
    items: tuple[tuple[scoring.ScoredItem, ...], ...]

    @property
    def repeats(self) -> tuple[int, ...]:
        """The repeat number of each run, in the order the runs are held."""
        return tuple(run.meta.repeat for run in self.runs)

    @property
    def complete(self) -> bool:
        """Whether the group has the repeats gate 2 needs."""
        return len(self.runs) >= REPEATS

    def summaries(self) -> tuple[tuple[summary.ModelSummary, ...], ...]:
        """Return the summaries of each repeat, as :func:`~.summary.summarise` prints them."""
        return tuple(tuple(summary.summarise(items)) for items in self.items)

    def first(self) -> tuple[StoredRun, tuple[StoredItem, ...]]:
        """Return the run of repeat 1 and its items: the one the explanation sample is drawn from.

        Raises:
            ValueError: if the group has no run of repeat 1, or comes from a local result folder:
                the sample is drawn from a committed run, whose items keep the notes.
        """
        for run, items in zip(self.runs, self.items, strict=True):
            if run.meta.repeat != 1:
                continue
            if not isinstance(run, StoredRun):
                raise ValueError(
                    f"{self.key.model}: the explanation sample is drawn from a committed run, "
                    "and this group comes from a local result folder"
                )
            return run, cast(tuple[StoredItem, ...], items)
        raise ValueError(f"{self.key.model}: the group has no run of repeat 1")


def groups(runs: Iterable[StoredRun], manifest: dict) -> list[RunGroup]:
    """Return the groups *runs* make, each with its repeats in order.

    Args:
        runs: The committed runs to group.
        manifest: The manifest that types their items; a run of another test set cannot be typed
            against it and is refused.

    Raises:
        ValueError: if a run belongs to another test set than *manifest*, or two runs of one group
            carry the same repeat number.
    """
    testset = str(manifest.get("testset", ""))
    grouped: dict[GroupKey, list[StoredRun]] = {}
    for run in runs:
        if testset and run.meta.testset != testset:
            raise ValueError(
                f"{run.path.name}: the run is of test set {run.meta.testset!r}, "
                f"the manifest of {testset!r}"
            )
        grouped.setdefault(GroupKey.of(run), []).append(run)
    built: list[RunGroup] = []
    for key in sorted(grouped, key=GroupKey.order):
        ordered = sorted(grouped[key], key=lambda run: run.meta.repeat)
        repeats = [run.meta.repeat for run in ordered]
        if len(set(repeats)) != len(repeats):
            raise ValueError(
                f"{key.model}: two runs of repeat {sorted(repeats)} are in one group; "
                "a repeat is one run"
            )
        built.append(
            RunGroup(
                key=key,
                runs=tuple(ordered),
                items=tuple(run.items(manifest) for run in ordered),
            )
        )
    return built


# ── the numbers gate 2 reads, per repeat ──────────────────────────────────────


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


def deciding_mean_gleu(summaries: Sequence[summary.ModelSummary]) -> float | None:
    """Return the mean GLEU over the deciding languages that have one, or ``None``."""
    return scoring.mean_gleu({code: row.gleu for code, row in deciding_rows(summaries).items()})


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
    "PAGES_BAND",
    "PAGE_BAND",
    "REPEATS",
    "GroupKey",
    "Range",
    "RunGroup",
    "RunLike",
    "band_mean_f_beta",
    "beyond",
    "deciding_mean_f_beta",
    "deciding_mean_gleu",
    "deciding_rows",
    "groups",
    "language_row",
    "ranges",
    "within_the_spread",
]
