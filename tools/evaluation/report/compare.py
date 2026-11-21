"""Comparing runs along axes: pick the axes that vary, fix the rest.

Every comparison the tools make is one shape: some runs were measured the same way except for one
or two attributes, and the question is what those attributes did.  A sweep by temperature, a
refinement of one model at two temperatures, a prompt variant, a sampling option, a cross-model
comparison — all of them are ``compare --along <axes>``, and none of them needs its own script.

The axes are the ones of :mod:`..core.axes`, so a name a command line accepts is a name a file name
and a page use.  A single sampling option is named ``sampling.<option>``, as ``compare --along``
names it and as the file name tags it.

What the module refuses is as important as what it prints: two runs that differ in an axis the
command did not name are an error and not a silent average, because the difference would then be
attributed to the wrong attribute.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ..core import protocol_table
from ..core.axes import AXIS_NAMES, SAMPLING_PREFIX
from . import groups, stats
from .tables import sweep

_PROFILE_AXES: frozenset[str] = frozenset({"sampling", "step_one_prompt", "compact_json"})
"""The axes the sweep's ``Profile`` column stands for, so a table does not print one of them twice."""

_DERIVED_BY: dict[str, frozenset[str]] = {
    "instructions": frozenset({"exchange", "steps", "step_one_prompt", "compact_json", "model"}),
}
"""The axes that decide a derived axis: it may vary only when one of them varies too."""


VERDICT_WORDS: dict[stats.Verdict, str] = {
    stats.Verdict.BETTER: "better than the baseline",
    stats.Verdict.TIE: "a tie with the baseline",
    stats.Verdict.WORSE: "worse than the baseline",
}
"""A verdict as the phrase a verdict line ends in."""


def axes_of(text: str) -> tuple[str, ...]:
    """Return the axis names of an ``--along`` value such as ``"temperature,steps"``.

    ``machine`` and ``isolated`` count as axes: runs that differ only there are separate groups.

    Raises:
        ValueError: if a name is not an axis of :mod:`..core.axes`, naming the ones that are.
    """
    names = tuple(part.strip() for part in text.split(",") if part.strip())
    if not names:
        raise ValueError("--along needs at least one axis")
    environment = sorted(groups.ENVIRONMENT_AXES)
    for name in names:
        if name in AXIS_NAMES or name in groups.ENVIRONMENT_AXES:
            continue
        if name.startswith(SAMPLING_PREFIX) and len(name) > len(SAMPLING_PREFIX):
            continue
        allowed = ", ".join([*AXIS_NAMES, *environment])
        raise ValueError(f"unknown axis {name!r}; use one of: {allowed}, or sampling.<option>")
    return names


def where_of(pairs: Iterable[str]) -> dict[str, str]:
    """Return the ``--where`` filters as ``{axis: value}``, from ``"model=gemma4:12b"`` pairs.

    Raises:
        ValueError: if a pair has no ``=``, or names an axis that is not one.
    """
    found: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--where wants axis=value, not {pair!r}")
        name, value = pair.split("=", 1)
        axes_of(name)
        found[name.strip()] = value.strip()
    return found


def value_of(key: groups.GroupKey, axis: str) -> str:
    """Return one axis of a group's key as the text a table prints and a filter compares."""
    return key.value_of(axis)


@dataclass(frozen=True)
class Along:
    """What a comparison varies, what it fixes, and which value it measures the others against."""

    axes: tuple[str, ...]
    where: Mapping[str, str]
    baseline: str | None = None
    """The value of the first axis the differences are measured against; the first row when
    ``None``."""

    def values(self, key: groups.GroupKey) -> tuple[str, ...]:
        """Return the values of the varying axes of one group, in the order of :attr:`axes`."""
        return tuple(value_of(key, axis) for axis in self.axes)

    def matches(self, key: groups.GroupKey) -> bool:
        """Whether one group's key has every value :attr:`where` fixes."""
        return all(value_of(key, axis) == value for axis, value in self.where.items())


@dataclass(frozen=True)
class Comparison:
    """The rows of one comparison, and the row the differences are measured against."""

    along: Along
    rows: tuple[sweep.SweepRow, ...]
    baseline: sweep.SweepRow | None

    def values(self, row: sweep.SweepRow) -> tuple[str, ...]:
        """Return the values of the varying axes of one row."""
        return self.along.values(row.group.key)

    def verdicts(self) -> list[str]:
        """Return one line per row saying what its difference against the baseline is.

        The interval says whether the gap is the sampler's; the minimum effect says whether it is
        worth a reader's attention, and the GLEU and page guards may turn a loss into a tie.  A row
        the comparison cannot judge says so instead of showing a number without a meaning.
        """
        lines: list[str] = []
        baseline = self.baseline
        if baseline is None:
            return [f"- {self._name(row)}: no baseline to measure against" for row in self.rows]
        for row in self.rows:
            name = self._name(row)
            if row is baseline:
                lines.append(f"- {name}: the baseline")
                continue
            if not row.comparable:
                lines.append(f"- {name}: not comparable with the baseline")
                continue
            if row.delta is None:
                lines.append(f"- {name}: no difference could be measured")
                continue
            verdict = stats.verdict_of(row.group, baseline.group)
            if verdict is None:
                lines.append(f"- {name}: no difference could be measured")
                continue
            lines.append(f"- {name}: {VERDICT_WORDS[verdict]} ({_delta(row.delta)})")
        return lines

    def _name(self, row: sweep.SweepRow) -> str:
        """Return a row's name: its varying values, and its model when the model does not vary."""
        values = ", ".join(
            f"{axis} {value}" for axis, value in zip(self.along.axes, self.values(row), strict=True)
        )
        if "model" in self.along.axes:
            return f"`{values}`"
        return f"`{row.model}` at {values}"

    def table(self) -> str:
        """Return the comparison as markdown: the varying axes, then the figures of each row."""
        return comparison_table(self)


def _delta(interval: stats.Interval) -> str:
    """Return a difference with its interval, as the sweep's column prints it."""
    return f"{interval.delta:+.3f} [{interval.low:+.3f}, {interval.high:+.3f}]"


def select(
    chosen: Sequence[groups.RunGroup], along: Along, *, corrections: bool = False
) -> list[groups.RunGroup]:
    """Return the groups that match :attr:`Along.where`, one per value of the varying axes.

    Args:
        chosen: Every group the store holds, or the ones a command line narrowed down.
        along: The axes that vary, the values that are fixed, and the baseline.
        corrections: Whether the two exchange modes count as one, as in a gate-2 comparison.

    Raises:
        ValueError: if no group matches, if the matching groups differ in an axis the comparison
            did not name, or if two groups share one value of the varying axes.
    """
    matching = [group for group in chosen if along.matches(group.key)]
    if not matching:
        fixed = ", ".join(f"{axis}={value}" for axis, value in along.where.items()) or "nothing"
        raise ValueError(f"no run matches {fixed}")
    _refuse_cross_set(matching)
    _refuse_hidden(matching, along, corrections=corrections)
    _refuse_derived(matching)
    by_value: dict[tuple[str, ...], list[groups.RunGroup]] = {}
    for group in matching:
        by_value.setdefault(along.values(group.key), []).append(group)
    for value, found in by_value.items():
        if len(found) > 1:
            raise ValueError(
                f"{', '.join(value)} names {len(found)} runs that differ in "
                f"{_differing(found[0].key, found[1].key)}; name it in --along, or fix it with "
                "--where <axis>=<value>"
            )
    return [by_value[value][0] for value in sorted(by_value, key=_sort_key)]


def _refuse_cross_set(matching: Sequence[groups.RunGroup]) -> None:
    """Refuse a selection that spans two test sets.

    A paired difference needs the same texts on both sides, and two sets of this store share none:
    the held-out set holds 22 texts and not one of them is in the main set.  Comparing across sets
    would pair nothing, so it is refused until a set and its extension exist.

    Raises:
        ValueError: naming the sets and saying to compare within one.
    """
    sets = list(dict.fromkeys(group.key.testset for group in matching))
    if len(sets) < 2:
        return
    raise ValueError(f"the sets share no text ({', '.join(sets)}); compare within one set")


def _refuse_hidden(
    matching: Sequence[groups.RunGroup], along: Along, *, corrections: bool = False
) -> None:
    """Refuse a selection whose groups differ in an axis the comparison did not name.

    A difference the command did not name would be attributed to the axis it did name, so the
    comparison is refused instead of averaged.  The axes a comparison may always read apart are the
    ones :mod:`.groups` names: the mode, the instruction hash and the machine.  The explainer is
    read apart only when the corrections are compared, because the corrections are the same whoever
    wrote the explanations.  The model's own request may vary only when the model itself is
    compared.

    Raises:
        ValueError: naming the axes that vary and how to name or fix them.
    """
    allowed = {_key_name(axis) for axis in along.axes}
    allowed |= groups.READ_APART | groups.ENVIRONMENT_AXES
    if not corrections:
        allowed.discard("explainer")
    if "model" in along.axes:
        allowed |= groups.MODEL_AXES
    hidden = [name for name in _varying_names(matching) if name not in allowed]
    if not hidden:
        return
    named = ", ".join(hidden)
    raise ValueError(
        f"the runs differ in {named}, which --along does not name; "
        f"name it in --along, or fix it with --where {hidden[0]}=<value>"
    )


def _refuse_derived(matching: Sequence[groups.RunGroup]) -> None:
    """Refuse a selection whose prompt text differs without the axis that decides it.

    ``instructions`` is the hash of what was sent, so it moves with the exchange, the steps, the
    step-one prompt, the compact-JSON flag and the model.  Two runs that differ in it and in none of
    those sent different prompt text under one name, which is a protocol change and not a
    comparison: the difference would be attributed to the axis the command named.

    Raises:
        ValueError: naming the two protocol versions and how to narrow the comparison.
    """
    varying = set(_varying_names(matching))
    for name, by in _DERIVED_BY.items():
        if name not in varying or varying & by:
            continue
        versions = sorted(
            {version for group in matching if (version := _version_of(group)) is not None}
        )
        named = " vs ".join(str(version) for version in versions) or "unknown"
        raise ValueError(
            f"the prompt text differs (protocol version {named}); "
            "fix it with --where or compare within one version"
        )


def _version_of(group: groups.RunGroup) -> int | None:
    """Return the protocol version a group's runs were made under, or ``None`` when unknown."""
    if not group.runs:
        return None
    return protocol_table.version_of(group.runs[0].header.protocol.fingerprint)


def _key_name(axis: str) -> str:
    """Return the compare-key name of an axis: one sampling option is the ``sampling`` axis."""
    return "sampling" if axis.startswith(SAMPLING_PREFIX) else axis


def _varying_names(matching: Sequence[groups.RunGroup]) -> list[str]:
    """Return the axes whose value differs over *matching*, in the order of the key.

    An axis a run left at its default is absent from that run's key, so the names are the union of
    every key's names and an absent one counts as its own value.
    """
    keys = [dict(group.key.values) for group in matching]
    names = list(dict.fromkeys([*keys[0], *(name for key in keys for name in key)]))
    return [name for name in names if len({key.get(name) for key in keys}) > 1]


def _differing(first: groups.GroupKey, second: groups.GroupKey) -> str:
    """Return the axes two keys disagree in, as a readable list."""
    names = [
        name
        for name, value in first.values
        if name in dict(second.values) and dict(second.values)[name] != value
    ]
    return ", ".join(names) or "an axis the key does not name"


def _sort_key(values: tuple[str, ...]) -> tuple[tuple[int, float, str], ...]:
    """Return a sort key that orders numbers as numbers and everything else as text."""
    return tuple(_one_sort_key(value) for value in values)


def _one_sort_key(value: str) -> tuple[int, float, str]:
    """Return one value's sort key: a number first, then the text, so ``0.4`` sorts before ``0.7``."""
    try:
        return (0, float(value), value)
    except ValueError:
        return (1, 0.0, value)


def compare(
    chosen: Sequence[groups.RunGroup],
    *,
    along: Along,
    corrections: bool = False,
) -> Comparison:
    """Return the comparison of the groups that vary along :attr:`Along.axes`.

    Args:
        chosen: Every group the store holds, or the ones a command line narrowed down.
        along: The axes that vary, the values that are fixed, and the baseline.
        corrections: Whether the two exchange modes count as one, as in a gate-2 comparison.

    Raises:
        ValueError: if no group matches, if two groups share one value of the varying axes, or if
            the baseline names no row.
    """
    selected = select(chosen, along, corrections=corrections)
    baseline = _baseline(selected, along)
    rows = sweep.sweep_rows(selected, baseline, corrections=corrections)
    return Comparison(
        along=along,
        rows=tuple(rows),
        baseline=next((row for row in rows if row.group is baseline), None),
    )


def _baseline(selected: Sequence[groups.RunGroup], along: Along) -> groups.RunGroup | None:
    """Return the group the differences are measured against, or ``None`` for the first row.

    Raises:
        ValueError: if :attr:`Along.baseline` names no value of the varying axes.
    """
    if along.baseline is None:
        return selected[0] if selected else None
    for group in selected:
        values = along.values(group.key)
        if along.baseline in values or along.baseline == "__".join(values):
            return group
    raise ValueError(
        f"the baseline {along.baseline!r} is not one of: "
        + ", ".join("__".join(along.values(group.key)) for group in selected)
    )


def comparison_table(comparison: Comparison) -> str:
    """Return a comparison as markdown: the varying axes, the figures, then the verdicts.

    A comparison of one model's configurations also prints which row is best and which rows are a
    tie with it: a tie goes to the cheaper configuration, and that is a choice a reader makes.  A
    comparison of models has no such row, because no model is a configuration of another.
    """
    along = comparison.along
    indexes = _sweep_indexes(along)
    header = [*along.axes, *(sweep.SWEEP_COLUMNS[index] for index in indexes)]
    lines = [
        f"## Along {', '.join(along.axes)}",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in comparison.rows:
        cells = [*comparison.values(row)]
        cells += [sweep.row_cells(row)[index] for index in indexes]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", *comparison.verdicts()]
    if "model" not in along.axes:
        lines += ["", *sweep.best_lines(comparison.rows)]
    if along.where:
        fixed = ", ".join(f"{axis}={value}" for axis, value in along.where.items())
        lines += ["", f"Fixed: {fixed}."]
    return "\n".join(lines)


def _sweep_indexes(along: Along) -> list[int]:
    """Return the sweep columns to print, leaving out the ones the varying axes already show."""
    dropped: set[int] = set()
    if "temperature" in along.axes:
        dropped.add(sweep.SWEEP_COLUMNS.index("T"))
    if _PROFILE_AXES & set(along.axes):
        dropped.add(sweep.SWEEP_COLUMNS.index("Profile"))
    return [index for index in range(len(sweep.SWEEP_COLUMNS)) if index not in dropped]


__all__ = [
    "VERDICT_WORDS",
    "Along",
    "Comparison",
    "axes_of",
    "compare",
    "comparison_table",
    "select",
    "value_of",
    "where_of",
]
