"""Gate 2, the entry gate: whether one model works in the app at all, before it is ranked.

One question, one answer.  :meth:`GateTwoRule.judge` asks whether one model, on its own numbers,
works in the app: no text it could not proofread, no cut-off that cost the explanations, no Markdown
it added, no text it keeps losing.  Pass or fail per model, no other model involved.  How good it
is, is the ranking's question (mean F0.5, :mod:`.stats` and :mod:`.compare`): coverage, left alone,
GLEU, the explained share and the teacher's marks are printed with the verdict and never decide.

The answer comes in two steps, measuring and then rating.  Measuring pools nothing across
languages: every check is measured per language, French too.  Rating decides which languages enter
the outcome — German and English — and shows the others with their ✓/✗ and "(not deciding)".

The floors are events over all runs.  Every floor is a hard stop, and the values are data, so a
calibration changes a value and not a branch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum

from ..core.modes import Step
from ..core.performance import per_100_words
from ..core.records import Mark
from ..metrics import text_loss
from . import groups, pooled, spot_check, summary

DECIDING_LANGUAGES: tuple[str, ...] = groups.DECIDING_LANGUAGES
"""The languages whose checks decide; French is reported only."""

_COUNTED: tuple[str, ...] = (
    "failed",
    "fallback",
    "cut_off",
    "added_marks",
    "cover",
    "cover_of",
    "alone",
    "alone_of",
    "explained",
    "needed",
    "explanations_unknown",
    "rows",
)
"""The numbers of :class:`~.summary.ModelSummary` the floors add up."""


class Outcome(Enum):
    """What the floors say about one model."""

    PASS = "pass"
    FAIL = "fail"
    INCOMPLETE = "incomplete"
    """Fewer than three repeats, or an item missing: nothing is judged."""


class CheckKind(Enum):
    """What a check is for, which decides what an unmeasured result (``passed is None``) means."""

    FLOOR = "floor"
    """A bar the model has to meet.  Unmeasured (no text to measure it on) leaves the verdict
    open: a model is never passed on a floor nobody measured."""

    DIAGNOSTIC = "diagnostic"
    """A figure printed with the verdict; never decisive."""


@dataclass(frozen=True)
class Check:
    """One line of a gate-2 record: what was measured, and what it was measured against."""

    name: str
    passed: bool | None
    """``None`` when nothing was measured: a diagnostic, or a floor without text."""
    value: str
    against: str
    language: str = ""
    """The language the check was measured for; empty for a check over the whole group."""
    deciding: bool = True
    """Whether the check enters the outcome; a language that does not decide keeps its ✓/✗ and is
    printed with "(not deciding)"."""
    kind: CheckKind = CheckKind.FLOOR

    def line(self) -> str:
        """Return the check as one line: a mark, the name, the value and the bar."""
        if self.passed is None:
            unmeasured = self.deciding and self.kind is not CheckKind.DIAGNOSTIC
            mark = "?" if unmeasured else "·"
        else:
            mark = "✓" if self.passed else "✗"
        bar = f" (needs {self.against})" if self.against else ""
        note = "" if self.deciding else " (not deciding)"
        return f"{mark} {self.name}: {self.value}{bar}{note}"


@dataclass(frozen=True)
class Measurement:
    """What the floors measured, per floor and language, before any of it decides."""

    group: groups.RunGroup
    parts: tuple[tuple[summary.ModelSummary, ...], ...]
    """The summaries of each repeat, which the rating step reads for the explanations."""
    checks: tuple[Check, ...]
    """One check per floor and language, named ``"<floor>, <language>"``."""
    diagnostics: tuple[Check, ...]
    """The figures printed with the floors that never decide."""
    outcome: Outcome | None = None
    """The outcome the measurement already settles — ``INCOMPLETE`` when nothing can be judged —
    or ``None`` when the rating step decides."""

    def languages(self) -> tuple[str, ...]:
        """Return the languages the checks were measured for, in the order they appear."""
        return tuple(dict.fromkeys(check.language for check in self.checks if check.language))


@dataclass(frozen=True)
class Judgement:
    """The checks of one model and what they add up to."""

    checks: tuple[Check, ...]
    outcome: Outcome
    diagnostics: tuple[Check, ...] = ()

    def languages(self) -> tuple[str, ...]:
        """Return the languages the checks were measured for, in the order they appear."""
        return tuple(dict.fromkeys(check.language for check in self.checks if check.language))

    def lines(self) -> list[str]:
        """Return the checks grouped by language, the diagnostics and the outcome."""
        lines = [check.line() for check in self.checks if not check.language]
        for code in self.languages():
            if lines:
                lines.append("")
            lines += [f"## {code}", ""]
            lines += [check.line() for check in self.checks if check.language == code]
        if self.diagnostics:
            if lines:
                lines.append("")
            lines += ["## diagnostics", ""]
            lines += [check.line() for check in self.diagnostics]
        return [*lines, "", f"→ {self.outcome.value}"]


@dataclass(frozen=True)
class GateTwoRule:
    """The floors of gate 2, as data, so a calibration changes a value and not a branch.

    Every floor is a hard stop on something that breaks a file for the teacher; nothing here is a
    measure of quality.
    """

    failed_texts: int = 0
    """Texts the model could not proofread at all, over all runs."""

    cut_offs: int = 0
    """Fallbacks whose requests include a ``length``: both tries were cut off, so the teacher lost
    the explanations of that text."""

    other_fallbacks: int = 1
    """Fallbacks that were not cut off, per language over all runs."""

    added_marks: int = 0
    """Texts where the model added Markdown marks the source does not have."""

    lost_texts: int = 0
    """Texts that lost text in two or more repeats, per language.  One repeat is shown, not
    marked: a single dropped sentence can be the sampler's doing, and the floor is about a
    configuration that keeps losing text."""

    repeats: int = groups.REPEATS
    """How many repeats a group needs before it is judged."""

    deciding: tuple[str, ...] = DECIDING_LANGUAGES
    """The languages whose checks enter the outcome; French is shown with "(not deciding)"."""

    def judge(self, group: groups.RunGroup, marks: Sequence[Mark] | None = None) -> Judgement:
        """Return the checks of *group* and what they add up to: measure, then rate.

        Args:
            group: The runs of one model, measured one way.
            marks: The teacher's marks of the group's explanations, or ``None`` when nobody has
                marked them yet.
        """
        return self.rate(self.measure(group), marks)

    def measure(self, group: groups.RunGroup) -> Measurement:
        """Return one check per floor and language, and the figures beside them.

        Nothing is pooled across languages: each language is measured on its own, French too, and
        which of them decides is :meth:`rate`'s question.  The figures (coverage, the explained
        share, GLEU, left alone) sit in their language's block and never decide.
        """
        if not group.complete or 1 not in group.repeats:
            return Measurement(
                group=group,
                parts=(),
                checks=(
                    Check(
                        "repeats",
                        False,
                        f"{len(group.runs)} ({', '.join(str(n) for n in group.repeats)})",
                        f"≥ {self.repeats}, repeat 1 among them",
                    ),
                ),
                diagnostics=(),
                outcome=Outcome.INCOMPLETE,
            )
        if not groups.same_items(group):
            return Measurement(
                group=group,
                parts=(),
                checks=(Check("items", False, "the runs hold different texts", "one set"),),
                diagnostics=(),
                outcome=Outcome.INCOMPLETE,
            )
        parts = group.summaries()
        absent = [code for code in self.deciding if code not in _languages(parts)]
        if absent:
            return Measurement(
                group=group,
                parts=(),
                checks=(
                    Check(
                        "languages",
                        False,
                        f"no text in {', '.join(absent)}",
                        f"a text in every deciding language ({', '.join(self.deciding)})",
                    ),
                ),
                diagnostics=(),
                outcome=Outcome.INCOMPLETE,
            )
        explains = Step.EXPLAIN in group.key.steps
        checks: list[Check] = []
        for code in _languages(parts):
            pooled_counts = _pooled(parts, code)
            checks.append(
                self._at_most(
                    f"failed texts, {code}", code, pooled_counts["failed"], self.failed_texts
                )
            )
            checks.append(
                self._at_most(
                    f"cut-offs that cost the explanations, {code}",
                    code,
                    pooled_counts["cut_off"],
                    self.cut_offs,
                )
            )
            if explains:
                checks.append(self._other_fallbacks(code, parts))
            checks.append(
                self._at_most(
                    f"added marks, {code}", code, pooled_counts["added_marks"], self.added_marks
                )
            )
            checks.append(self._lost_texts(group, code))
            checks.append(_share(f"coverage, {code}", code, parts, "cover", "cover_of"))
            if explains:
                checks.append(
                    _share(f"explained share, {code}", code, parts, "explained", "needed")
                )
            checks.append(_gleu(code, parts))
            checks.append(_share(f"left alone, {code}", code, parts, "alone", "alone_of"))
        return Measurement(
            group=group,
            parts=parts,
            checks=tuple(checks),
            diagnostics=_diagnostics(group, parts),
        )

    def rate(self, measurement: Measurement, marks: Sequence[Mark] | None = None) -> Judgement:
        """Return the judgement of a measurement: the deciding languages decide, the rest is shown.

        Args:
            measurement: What :meth:`measure` found.
            marks: The teacher's marks of the group's explanations, or ``None`` when nobody has
                marked them yet.
        """
        checks = [
            check
            if check.deciding and (not check.language or check.language in self.deciding)
            else replace(check, deciding=False)
            for check in measurement.checks
        ]
        if measurement.parts and Step.EXPLAIN in measurement.group.key.steps:
            checks.extend(self._explanations(measurement.group, measurement.parts, marks))
        return Judgement(
            checks=tuple(checks),
            outcome=measurement.outcome or _outcome(checks),
            diagnostics=measurement.diagnostics,
        )

    def _at_most(self, name: str, language: str, found: int, allowed: int) -> Check:
        """Return a check that *found* is at most *allowed*."""
        return Check(name, found <= allowed, str(found), f"≤ {allowed}", language=language)

    def _lost_texts(self, group: groups.RunGroup, code: str) -> Check:
        """Return the texts of one language that lost text in two or more repeats.

        Counted per text id across the repeats, not pooled: a text that lost its last sentence
        once is shown by the report and not marked here.  A group whose rows were written before
        the words were stored passes, with the value saying that nothing was measured.
        """
        lost: dict[str, int] = {}
        measured = False
        for items in group.items:
            for item in items:
                if item.language != code:
                    continue
                flag = text_loss.is_lost(item.scores)
                if flag is None:
                    continue
                measured = True
                if flag:
                    lost[item.item] = lost.get(item.item, 0) + 1
        name = f"texts that lost text in 2 or more repeats, {code}"
        if not measured:
            return Check(name, True, "not measured", f"≤ {self.lost_texts}", language=code)
        found = sum(1 for count in lost.values() if count >= 2)
        return self._at_most(name, code, found, self.lost_texts)

    def _other_fallbacks(self, code: str, parts: Sequence[Sequence[summary.ModelSummary]]) -> Check:
        """Return the fallbacks of one language that were not cut off, over all runs."""
        counts = _pooled(parts, code)
        other = counts["fallback"] - counts["cut_off"]
        return Check(
            f"other fallbacks, {code}",
            other <= self.other_fallbacks,
            str(other),
            f"≤ {self.other_fallbacks}",
            language=code,
        )

    def _explanations(
        self,
        group: groups.RunGroup,
        parts: Sequence[Sequence[summary.ModelSummary]],
        marks: Sequence[Mark] | None,
    ) -> list[Check]:
        """Return the teacher's marks per deciding language, as figures that never decide.

        Runs with explanations only: a run without them has nothing to mark, and says so by its
        steps and not because its sample happens to be empty.
        """
        drawn = spot_check.sample(group.first())
        per_language = drawn.per_language()
        counted = spot_check.correct_share(drawn, marks) if marks is not None else {}
        checks = []
        for code in _languages(parts):
            if code not in self.deciding:
                continue
            notes = per_language.get(code, ())
            if not notes:
                value = "nothing to mark"
            elif marks is None:
                value = f"{len(notes)} to mark"
            else:
                correct, total = counted.get(code, (0, len(notes)))
                value = f"{correct}/{total} correct"
            checks.append(_figure(f"{spot_check.EXPLANATIONS}, {code}", value, code))
        return checks


def _outcome(checks: Sequence[Check]) -> Outcome:
    """Return what the deciding floors add up to.

    A failed floor fails the model.  A floor nobody measured leaves it ``INCOMPLETE`` (never a
    pass).  Otherwise it passes.
    """
    deciding = [check for check in checks if check.deciding and check.kind is CheckKind.FLOOR]
    if any(check.passed is False for check in deciding):
        return Outcome.FAIL
    if any(check.passed is None for check in deciding):
        return Outcome.INCOMPLETE
    return Outcome.PASS


def _languages(parts: Sequence[Sequence[summary.ModelSummary]]) -> tuple[str, ...]:
    """Return the languages the runs hold, in the order of the deciding ones first."""
    found = {row.language for part in parts for row in part if not row.band}
    return tuple(code for code in DECIDING_LANGUAGES if code in found) + tuple(
        sorted(found - set(DECIDING_LANGUAGES))
    )


def _pooled(parts: Sequence[Sequence[summary.ModelSummary]], language: str) -> dict[str, int]:
    """Return the counted numbers of *parts*, pooled over the repeats and one language.

    One language at a time: nothing is pooled across languages, so every floor is measured per
    language and the rating step decides which of them count.
    """
    totals = dict.fromkeys(_COUNTED, 0)
    for part in parts:
        for row in part:
            if row.band or row.language != language:
                continue
            for name in _COUNTED:
                totals[name] += getattr(row, name)
    return totals


# ── the figures ───────────────────────────────────────────────────────────────


def _figure(name: str, value: str, language: str) -> Check:
    """Return one language's figure: printed in its block, never deciding."""
    return Check(name, None, value, "", language=language, kind=CheckKind.DIAGNOSTIC)


def _share(
    name: str,
    code: str,
    parts: Sequence[Sequence[summary.ModelSummary]],
    numerator: str,
    denominator: str,
) -> Check:
    """Return one language's share, pooled over the repeats."""
    counts = _pooled(parts, code)
    share = counts[numerator] / counts[denominator] if counts[denominator] else None
    return _figure(name, "no text" if share is None else f"{share:.2f}", code)


def _gleu(code: str, parts: Sequence[Sequence[summary.ModelSummary]]) -> Check:
    """Return the smallest GLEU margin over the source of one language, over the runs."""
    margins = []
    for part in parts:
        row = groups.language_row(part, code)
        if row is None or row.gleu is None or row.gleu_source is None:
            continue
        margins.append(row.gleu - row.gleu_source)
    value = f"{min(margins):+.2f} in the weakest run" if margins else "no text"
    return _figure(f"GLEU above the source, {code}", value, code)


# ── the diagnostics ───────────────────────────────────────────────────────────


def _diagnostics(
    group: groups.RunGroup, parts: Sequence[Sequence[summary.ModelSummary]]
) -> tuple[Check, ...]:
    """Return the figures printed with the floors that never decide.

    French is not among them: it has its own block now, with its own ✓/✗.
    """
    return (
        _halves(parts),
        _band(parts, groups.PAGES_BAND),
        _absorbed(group),
        _speed(group),
        _unknown_explanations(parts),
    )


def _unknown_explanations(parts: Sequence[Sequence[summary.ModelSummary]]) -> Check:
    """Return how many texts were left out of the explanation figures, and why.

    A review of a version this tool does not read is kept, but its explanations cannot be counted.
    The floor is measured over the texts that were read, and this line says how many were not, so a
    share of 0.90 over three texts is not mistaken for a share over thirty.
    """
    left_out = sum(row.explanations_unknown for part in parts for row in part if not row.band)
    return Check(
        "explanations of an unreadable review",
        None,
        f"{left_out} texts left out" if left_out else "none",
        "",
        kind=CheckKind.DIAGNOSTIC,
    )


def _halves(parts: Sequence[Sequence[summary.ModelSummary]]) -> Check:
    """Return the recall of the first and of the second half of the texts, per language."""
    values = []
    for code in DECIDING_LANGUAGES:
        first = [
            row.recall_h1
            for part in parts
            if (row := groups.deciding_rows(part).get(code)) is not None
            and row.recall_h1 is not None
        ]
        second = [
            row.recall_h2
            for part in parts
            if (row := groups.deciding_rows(part).get(code)) is not None
            and row.recall_h2 is not None
        ]
        if first and second:
            values.append(f"{code} {sum(first) / len(first):.2f} / {sum(second) / len(second):.2f}")
    return Check(
        "recall, 1st / 2nd half",
        None,
        ", ".join(values) or "no text",
        "",
        kind=CheckKind.DIAGNOSTIC,
    )


def _band(parts: Sequence[Sequence[summary.ModelSummary]], band: str) -> Check:
    """Return the mean F0.5 of one band over the repeats: too few texts to decide on."""
    values = [value for part in parts if (value := groups.band_mean_f_beta(part, band)) is not None]
    mean = sum(values) / len(values) if values else None
    return Check(
        f"{band} band, F0.5",
        None,
        "no text" if mean is None else f"{mean:.2f}",
        "",
        kind=CheckKind.DIAGNOSTIC,
    )


def absorbed_cut_offs(items: Sequence[pooled.ItemFacts]) -> dict[str, int]:
    """Return, per language, the texts whose answer was cut off and asked again successfully.

    They cost time and nothing else: the second try answered, so the teacher kept the
    explanations.  A diagnostic, not a floor.
    """
    counts: dict[str, int] = {}
    for item in items:
        if item.fallback or item.performance is None:
            continue
        if any(request.done_reason == "length" for request in item.performance.requests):
            counts[item.language] = counts.get(item.language, 0) + 1
    return counts


def _absorbed(group: groups.RunGroup) -> Check:
    """Return the cut-offs the second try absorbed, per language over all runs."""
    counts: dict[str, int] = {}
    for items in group.items:
        for code, found in absorbed_cut_offs(items).items():
            counts[code] = counts.get(code, 0) + found
    values = ", ".join(f"{code} {counts[code]}" for code in sorted(counts))
    return Check(
        "cut-offs the second try absorbed", None, values or "none", "", kind=CheckKind.DIAGNOSTIC
    )


def _seconds_per_100_words(group: groups.RunGroup) -> float:
    """Return the seconds per 100 words of student text, over every text of the group."""
    seconds = sum(item.seconds for items in group.items for item in items)
    words = sum(item.words for items in group.items for item in items)
    return per_100_words(seconds, words)


def _speed(group: groups.RunGroup) -> Check:
    """Return the seconds per 100 words of student text, over every text of the group."""
    return Check(
        "seconds per 100 words",
        None,
        f"{_seconds_per_100_words(group):.1f}",
        "",
        kind=CheckKind.DIAGNOSTIC,
    )


__all__ = [
    "DECIDING_LANGUAGES",
    "Check",
    "CheckKind",
    "GateTwoRule",
    "Judgement",
    "Measurement",
    "Outcome",
    "absorbed_cut_offs",
]
