"""Gate 2: the floors a model meets on its own numbers, and the comparison of two models.

Two questions, two answers.  :meth:`GateTwoRule.judge` asks whether one model, on its own numbers,
meets the bar for being offered at all: pass or fail per model, no other model involved, no
baseline needed.  :func:`compare` asks whether model *a* is better than model *b* beyond
the noise; it is used once, at the end of the screen, to fill each tier's places, and it is not a
gate.

Both questions are answered in two steps, measuring and then rating.  Measuring pools
nothing across languages: every floor is measured per language, French too, so each language reads
as its own evaluation.  Rating decides which languages enter the outcome — German and English by
default, until a calibration on the full screen settles it — and shows the others with their ✓/✗ and
"(not deciding)".  The comparison is split the same way: :func:`measure_pair` builds the
per-language ranges and bootstraps, and :func:`rate_pair` combines them, by default with the
deciding mean.

The floors are events over all runs and rates pooled over the repeats, because a rate on
one repeat is noisy.  The comparison needs the interval of a paired bootstrap over the texts to
exclude zero on the right **and** the gap to be at least :data:`MIN_EFFECT`: the first says the gap
is bigger than the choice of texts, the second that it is big enough to matter.  Both are needed,
and neither is a reason to widen the other.  The spread of the repeats is printed next to them and
decides nothing: at a low temperature it collapses, and a gap of 0.001 would then look like a
finding.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
import random

from teachers_teammate.domain.model_catalog import normalize_tag

from . import grouping, scoring, spot_check, summary, text_loss
from .answer_mode import AnswerMode
from .performance import per_100_words
from .quality import EditScore

DECIDING_LANGUAGES: tuple[str, ...] = grouping.DECIDING_LANGUAGES
"""The languages whose mean decides; French is reported only."""

BOOTSTRAP_DRAWS = 2000
"""How many draws the paired bootstrap makes."""

BOOTSTRAP_SEED = 20261111
"""The seed of the bootstrap, so every machine gets the same answer."""

MIN_EFFECT = 0.01
"""The smallest gap that counts as a difference, whatever the interval says."""

TIME_TOLERANCE = 0.05
"""How much slower one model may be per 100 words and still dominate the other."""

_EXPLANATIONS = "explanations,"
"""How the explanation checks are named."""

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
    "rows",
)
"""The numbers of :class:`~.summary.ModelSummary` the floors add up."""


class Outcome(Enum):
    """What the floors say about one model."""

    PASS = "pass"
    PASS_PENDING = "pass, explanations not yet marked"
    """Every floor holds, but the explanations are not marked yet: enough for an alternative's
    place, not for a star's."""

    FAIL = "fail"
    INCOMPLETE = "incomplete"
    """Fewer than three repeats, or an item missing: nothing is judged."""


class Comparison(Enum):
    """What the comparison of two models says."""

    BETTER = "better"
    """Every winning test holds."""

    WORSE = "worse"
    """The same holds with the two models swapped."""

    TIE = "within the spread"
    NOT_COMPARABLE = "not comparable"
    """The keys differ in more than model, digest and request profile."""


@dataclass(frozen=True)
class Interval:
    """One measure's difference between two groups, over the drawn texts."""

    delta: float
    """The difference measured on every text, not on the draws."""
    low: float
    """The 2.5 % point of the drawn differences."""
    high: float
    """The 97.5 % point of the drawn differences."""
    p_not_better: float
    """The share of draws at or below zero."""
    p_not_worse: float
    """The share of draws at or above zero."""

    def negated(self) -> Interval:
        """Return the same difference the other way round, without a second bootstrap."""
        return Interval(
            delta=-self.delta,
            low=-self.high,
            high=-self.low,
            p_not_better=self.p_not_worse,
            p_not_worse=self.p_not_better,
        )


class Verdict(Enum):
    """What one measure says about the first model against the second."""

    BETTER = "better"
    TIE = "tie"
    WORSE = "worse"


class CheckKind(Enum):
    """What a check is for, which decides what an unmeasured result (``passed is None``) means."""

    FLOOR = "floor"
    """A bar the model has to meet.  Unmeasured (no text to measure it on) leaves the verdict
    open: a model is never passed on a floor nobody measured."""

    EXPLANATION = "explanation"
    """The teacher's marks of the explanations.  Unmeasured means "not yet marked": enough for an
    alternative's place, not for a star's."""

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

    group: grouping.RunGroup
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
class MeasureVerdict:
    """One measure's difference between two groups, and what it says."""

    name: str
    interval: Interval
    verdict: Verdict

    def line(self) -> str:
        """Return the measure as one line: its difference, its interval and its verdict."""
        return (
            f"· {self.name}: Δ {self.interval.delta:+.3f} "
            f"[{self.interval.low:+.3f}, {self.interval.high:+.3f}] → {self.verdict.value}"
        )


@dataclass(frozen=True)
class ComparisonResult:
    """The checks of one comparison and what they add up to."""

    comparison: Comparison
    checks: tuple[Check, ...]
    side_by_side: tuple[Check, ...]
    per_language: tuple[Check, ...] = ()
    """The figures of each language, printed above the verdict and never decisive."""
    measures: tuple[MeasureVerdict, ...] = ()
    """One line per measure, each with its interval and its own verdict."""
    dominance: str = ""
    """Which model dominates the other, or that neither does."""

    def lines(self) -> list[str]:
        """Return the checks, the measures, the figures and the outcome."""
        lines = [check.line() for check in self.checks]
        if self.measures:
            lines += ["", "Per measure:"] + [measure.line() for measure in self.measures]
        if self.dominance:
            lines += ["", f"· dominance: {self.dominance}"]
        if self.per_language:
            lines += ["", "Per language, never decisive:"] + [
                f"· {check.name}: {check.value}" for check in self.per_language
            ]
        if self.side_by_side:
            lines += ["", "Side by side, never decisive:"] + [
                f"· {check.name}: {check.value}" for check in self.side_by_side
            ]
        return [*lines, "", f"→ {self.comparison.value}"]


# ── the floors ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateTwoRule:
    """The floors of gate 2, as data, so a calibration changes a value and not a branch.

    Every floor is a hard stop.  The starting values come from two runs on ``gec-2026-12``
    and are to be calibrated on the full screen.
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

    coverage: float = 0.90
    """Share of the reference's error sentences the model touches, pooled per language."""

    explained: float = 0.85
    """Share of the changes that carry an explanation, pooled per language (review mode)."""

    left_alone: float = 0.50
    """Share of the sentences the reference leaves alone that the model leaves alone too."""

    correct_share: float = spot_check.CORRECT_SHARE
    """Share of a language's explanation sample that has to be marked ``correct``."""

    repeats: int = grouping.REPEATS
    """How many repeats a group needs before it is judged."""

    deciding: tuple[str, ...] = DECIDING_LANGUAGES
    """The languages whose checks enter the outcome.  A calibration on the full
    screen settles it; until then German and English decide and French is shown with "(not deciding)"."""

    def judge(self, group: grouping.RunGroup, marks: spot_check.Marks | None = None) -> Judgement:
        """Return the checks of *group* and what they add up to: measure, then rate.

        Args:
            group: The runs of one model, measured one way.
            marks: The teacher's marks of the group's explanations, or ``None`` when nobody has
                marked them yet.
        """
        return self.rate(self.measure(group), marks)

    def measure(self, group: grouping.RunGroup) -> Measurement:
        """Return one check per floor and language, for every language the runs hold.

        Nothing is pooled across languages: each language is measured on its own, French too, and
        which of them decides is :meth:`rate`'s question.
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
        if not _same_items(group):
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
        review = group.key.mode is AnswerMode.REVIEW
        checks: list[Check] = []
        for code in _languages(parts):
            pooled = _pooled(parts, code)
            checks.append(
                self._at_most(f"failed texts, {code}", code, pooled["failed"], self.failed_texts)
            )
            checks.append(
                self._at_most(
                    f"cut-offs that cost the explanations, {code}",
                    code,
                    pooled["cut_off"],
                    self.cut_offs,
                )
            )
            if review:
                checks.append(self._other_fallbacks(code, parts))
            checks.append(
                self._at_most(f"added marks, {code}", code, pooled["added_marks"], self.added_marks)
            )
            checks.append(self._lost_texts(group, code))
            checks.append(
                self._share(f"coverage, {code}", code, parts, "cover", "cover_of", self.coverage)
            )
            if review:
                checks.append(
                    self._share(
                        f"explained share, {code}",
                        code,
                        parts,
                        "explained",
                        "needed",
                        self.explained,
                    )
                )
            checks.append(self._gleu(code, parts))
            checks.append(
                self._share(
                    f"left alone, {code}", code, parts, "alone", "alone_of", self.left_alone
                )
            )
        return Measurement(
            group=group,
            parts=parts,
            checks=tuple(checks),
            diagnostics=_diagnostics(group, parts),
        )

    def rate(self, measurement: Measurement, marks: spot_check.Marks | None = None) -> Judgement:
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
        if measurement.parts and measurement.group.key.mode is AnswerMode.REVIEW:
            checks.extend(self._explanations(measurement.group, measurement.parts, marks))
        return Judgement(
            checks=tuple(checks),
            outcome=measurement.outcome or _outcome(checks),
            diagnostics=measurement.diagnostics,
        )

    def _at_most(self, name: str, language: str, found: int, allowed: int) -> Check:
        """Return a check that *found* is at most *allowed*."""
        return Check(name, found <= allowed, str(found), f"≤ {allowed}", language=language)

    def _lost_texts(self, group: grouping.RunGroup, code: str) -> Check:
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
        pooled = _pooled(parts, code)
        other = pooled["fallback"] - pooled["cut_off"]
        return Check(
            f"other fallbacks, {code}",
            other <= self.other_fallbacks,
            str(other),
            f"≤ {self.other_fallbacks}",
            language=code,
        )

    def _share(
        self,
        name: str,
        code: str,
        parts: Sequence[Sequence[summary.ModelSummary]],
        numerator: str,
        denominator: str,
        floor: float,
    ) -> Check:
        """Return one language's pooled share against *floor*."""
        pooled = _pooled(parts, code)
        share = pooled[numerator] / pooled[denominator] if pooled[denominator] else None
        return Check(
            name,
            None if share is None else share >= floor,
            "no text" if share is None else f"{share:.2f}",
            f"≥ {floor:.2f}",
            language=code,
        )

    def _gleu(self, code: str, parts: Sequence[Sequence[summary.ModelSummary]]) -> Check:
        """Return the smallest GLEU margin over the source of one language, over the runs."""
        margins = []
        for part in parts:
            row = grouping.language_row(part, code)
            if row is None or row.gleu is None or row.gleu_source is None:
                continue
            margins.append(row.gleu - row.gleu_source)
        if not margins:
            return Check(
                f"GLEU above the source, {code}", None, "no text", "> 0 in every run", language=code
            )
        smallest = min(margins)
        return Check(
            f"GLEU above the source, {code}",
            smallest > 0,
            f"{smallest:+.2f}",
            "> 0 in every run",
            language=code,
        )

    def _explanations(
        self,
        group: grouping.RunGroup,
        parts: Sequence[Sequence[summary.ModelSummary]],
        marks: spot_check.Marks | None,
    ) -> list[Check]:
        """Return the explanation check per deciding language: 80 % of the sample ``correct``.

        Review mode only: text mode has no explanations to mark, and says so by mode and not
        because its sample happens to be empty.  A deciding language whose sample is empty gets a
        check that is neither passed nor failed, with "nothing to mark", so the outcome is pending
        and never a pass: no star is given without marks.
        """
        run, items = group.first()
        drawn = spot_check.sample(run, items)
        per_language = drawn.per_language()
        counted = marks.correct(drawn) if marks is not None else {}
        checks = []
        for code in _languages(parts):
            if code not in self.deciding:
                continue
            notes = per_language.get(code, ())
            if not notes:
                checks.append(
                    Check(
                        f"{_EXPLANATIONS} {code}",
                        None,
                        "nothing to mark",
                        "",
                        language=code,
                        kind=CheckKind.EXPLANATION,
                    )
                )
                continue
            threshold = drawn.threshold(code)
            if marks is None:
                checks.append(
                    Check(
                        f"{_EXPLANATIONS} {code}",
                        None,
                        f"{len(notes)} to mark",
                        f"≥ {threshold} correct",
                        language=code,
                        kind=CheckKind.EXPLANATION,
                    )
                )
                continue
            correct, total = counted.get(code, (0, len(notes)))
            checks.append(
                Check(
                    f"{_EXPLANATIONS} {code}",
                    correct >= threshold,
                    f"{correct}/{total} correct",
                    f"≥ {threshold}",
                    language=code,
                    kind=CheckKind.EXPLANATION,
                )
            )
        return checks


def _outcome(checks: Sequence[Check]) -> Outcome:
    """Return what the deciding checks add up to.

    A failed floor fails the model.  A floor nobody measured leaves it ``INCOMPLETE`` (never a
    pass).  Unmarked explanations leave it ``PASS_PENDING``.  Otherwise it passes.
    """
    deciding = [
        check for check in checks if check.deciding and check.kind is not CheckKind.DIAGNOSTIC
    ]
    if any(check.passed is False for check in deciding):
        return Outcome.FAIL
    unmeasured = [check for check in deciding if check.passed is None]
    if any(check.kind is CheckKind.FLOOR for check in unmeasured):
        return Outcome.INCOMPLETE
    if unmeasured:
        return Outcome.PASS_PENDING
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


def _pooled_deciding(parts: Sequence[Sequence[summary.ModelSummary]]) -> dict[str, int]:
    """Return the counted numbers of the deciding languages, pooled over the repeats."""
    totals = dict.fromkeys(_COUNTED, 0)
    for part in parts:
        for row in part:
            if row.band or row.language not in DECIDING_LANGUAGES:
                continue
            for name in _COUNTED:
                totals[name] += getattr(row, name)
    return totals


def _same_items(group: grouping.RunGroup) -> bool:
    """Whether every run of *group* holds the same texts, so no item is missing from one."""
    sets = [{item.item for item in items} for items in group.items]
    return all(found == sets[0] for found in sets)


def _same_texts(a: grouping.RunGroup, b: grouping.RunGroup) -> bool:
    """Whether both groups hold the same texts, each run of a group the same as the others."""
    if not _same_items(a) or not _same_items(b):
        return False
    return {item.item for item in a.items[0]} == {item.item for item in b.items[0]}


# ── the diagnostics ───────────────────────────────────────────────────────────


def _diagnostics(
    group: grouping.RunGroup, parts: Sequence[Sequence[summary.ModelSummary]]
) -> tuple[Check, ...]:
    """Return the figures printed with the floors that never decide.

    French is not among them: it has its own block now, with its own ✓/✗.
    """
    return (
        _halves(parts),
        _band(parts, grouping.PAGES_BAND),
        _absorbed(group),
        _speed(group),
    )


def _halves(parts: Sequence[Sequence[summary.ModelSummary]]) -> Check:
    """Return the recall of the first and of the second half of the texts, per language."""
    values = []
    for code in DECIDING_LANGUAGES:
        first = [
            row.recall_h1
            for part in parts
            if (row := grouping.deciding_rows(part).get(code)) is not None
            and row.recall_h1 is not None
        ]
        second = [
            row.recall_h2
            for part in parts
            if (row := grouping.deciding_rows(part).get(code)) is not None
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
    values = [
        value for part in parts if (value := grouping.band_mean_f_beta(part, band)) is not None
    ]
    mean = sum(values) / len(values) if values else None
    return Check(
        f"{band} band, F0.5",
        None,
        "no text" if mean is None else f"{mean:.2f}",
        "",
        kind=CheckKind.DIAGNOSTIC,
    )


def absorbed_cut_offs(items: Sequence[scoring.ScoredItem]) -> dict[str, int]:
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


def _absorbed(group: grouping.RunGroup) -> Check:
    """Return the cut-offs the second try absorbed, per language over all runs."""
    counts: dict[str, int] = {}
    for items in group.items:
        for code, found in absorbed_cut_offs(items).items():
            counts[code] = counts.get(code, 0) + found
    values = ", ".join(f"{code} {counts[code]}" for code in sorted(counts))
    return Check(
        "cut-offs the second try absorbed", None, values or "none", "", kind=CheckKind.DIAGNOSTIC
    )


def _seconds_per_100_words(group: grouping.RunGroup) -> float:
    """Return the seconds per 100 words of student text, over every text of the group."""
    seconds = sum(item.seconds for items in group.items for item in items)
    words = sum(item.words for items in group.items for item in items)
    return per_100_words(seconds, words)


def _speed(group: grouping.RunGroup) -> Check:
    """Return the seconds per 100 words of student text, over every text of the group."""
    return Check(
        "seconds per 100 words",
        None,
        f"{_seconds_per_100_words(group):.1f}",
        "",
        kind=CheckKind.DIAGNOSTIC,
    )


# ── the comparison ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LanguageFigures:
    """One language's figures of both models, one value per repeat."""

    f_beta: tuple[tuple[float | None, ...], tuple[float | None, ...]]
    """The F0.5 of each repeat, of the first model and of the second."""
    gleu: tuple[tuple[float | None, ...], tuple[float | None, ...]]
    """The GLEU of each repeat, of the first model and of the second."""
    page: tuple[tuple[float | None, ...], tuple[float | None, ...]]
    """The F0.5 of the ``page`` band of each repeat, of the first model and of the second."""
    chance: float
    """``P(Δ ≤ 0)`` of this language's F0.5, over the texts."""


@dataclass(frozen=True)
class PairMeasurement:
    """What the comparison measured, per language, before any of it decides."""

    a: grouping.RunGroup
    b: grouping.RunGroup
    figures: dict[str, LanguageFigures]
    """The figures of each language both groups hold, in the order of the deciding ones first."""
    intervals: dict[str, Interval] = field(default_factory=dict)
    """The interval of each measure over the deciding languages, by name."""


def measure_pair(
    a: grouping.RunGroup, b: grouping.RunGroup, *, deciding: tuple[str, ...] = DECIDING_LANGUAGES
) -> PairMeasurement:
    """Return the per-language ranges and the intervals of two groups.

    Nothing is pooled across languages in the figures: each language gets its own ranges and its
    own bootstrap, French too.  The intervals are the deciding mean, one set of draws for every
    measure, and :func:`rate_pair` reads them.
    """
    left = a.summaries()
    right = b.summaries()
    shared = set(_languages(right))
    codes = [code for code in _languages(left) if code in shared]
    intervals, chances = _bootstrap(a, b, deciding=deciding, also=codes)
    figures = {
        code: LanguageFigures(
            f_beta=(_values(left, code, _f_beta), _values(right, code, _f_beta)),
            gleu=(_values(left, code, _gleu), _values(right, code, _gleu)),
            page=(_values(left, code, _page), _values(right, code, _page)),
            chance=chances.get(code, 1.0),
        )
        for code in codes
    }
    return PairMeasurement(a=a, b=b, figures=figures, intervals=intervals)


def rate_pair(
    measurement: PairMeasurement, *, deciding: tuple[str, ...] = DECIDING_LANGUAGES
) -> ComparisonResult:
    """Return whether *a* is better than *b* beyond the noise, by the deciding mean.

    The verdict: the interval of the deciding mean F0.5 over the texts, which has to exclude zero
    on the right and to hold a gap of at least :data:`MIN_EFFECT`, plus two guards — the deciding
    mean GLEU and the deciding mean of the ``page`` band may not be below the other's by more than
    :data:`MIN_EFFECT`.  The per-language figures, the spread of the repeats and the table of the
    measures are printed with it and never decide, so a calibration can change what decides
    without changing the verdict's shape.
    """
    winning = _better(measurement, deciding)
    figures = _per_language(measurement)
    measures = _measures(measurement)
    side = side_by_side(measurement.a, measurement.b)
    if all(check.passed is not False for check in winning):
        comparison, checks = Comparison.BETTER, winning
    else:
        losing = _better(measurement, deciding, swap=True)
        if all(check.passed is not False for check in losing):
            comparison, checks = Comparison.WORSE, losing
        else:
            comparison, checks = Comparison.TIE, winning
    return ComparisonResult(
        comparison,
        tuple(checks),
        side,
        figures,
        measures=measures,
        dominance=_dominance(measurement, measures, comparison),
    )


def compare(
    a: grouping.RunGroup,
    b: grouping.RunGroup,
    *,
    deciding: tuple[str, ...] = DECIDING_LANGUAGES,
    corrections: bool = False,
) -> ComparisonResult:
    """Return whether *a* is better than *b* beyond the noise, or the other way round, or a tie.

    The two groups may differ in model, digest, request profile and temperature (each model starts
    at its maker's recommended temperature, and a refinement compares one model at two) and
    in nothing else; the side-by-side figures print both temperatures.  A group with fewer repeats
    than gate 2 needs is not comparable: the spread of two values says nothing.

    Args:
        a: The first model's runs.
        b: The second model's runs.
        deciding: The languages whose mean decides; the rest is shown, never decisive.
        corrections: Whether the two exchange modes count as one, so a correct-only run is
            compared with an exchange run of the same corrections.
    """
    if not a.key.measured_alike(b.key, corrections=corrections):
        return ComparisonResult(
            comparison=Comparison.NOT_COMPARABLE,
            checks=(
                Check(
                    "measured alike",
                    False,
                    f"{a.key.model} and {b.key.model} differ in more than the model",
                    "one test set, window, machine and eval version",
                ),
            ),
            side_by_side=(),
        )
    if not a.complete or not b.complete:
        return ComparisonResult(
            comparison=Comparison.NOT_COMPARABLE,
            checks=(
                Check(
                    "repeats",
                    False,
                    f"{len(a.runs)} and {len(b.runs)}",
                    f"≥ {grouping.REPEATS} each",
                ),
            ),
            side_by_side=(),
        )
    if not _same_texts(a, b):
        return ComparisonResult(
            comparison=Comparison.NOT_COMPARABLE,
            checks=(
                Check(
                    "items",
                    False,
                    "the groups hold different texts",
                    "one set, the same in every run",
                ),
            ),
            side_by_side=(),
        )
    return rate_pair(measure_pair(a, b, deciding=deciding), deciding=deciding)


def _better(
    measurement: PairMeasurement, deciding: tuple[str, ...], *, swap: bool = False
) -> list[Check]:
    """Return the tests that have to hold for the first model to be better than the second.

    The interval of the deciding mean F0.5, the two guards on the means of GLEU and of the
    ``page`` band, and the spread of the repeats as a diagnostic.  With *swap* the same interval
    is read the other way round, so no second bootstrap is drawn.
    """
    first, second = (1, 0) if swap else (0, 1)
    codes = [code for code in deciding if code in measurement.figures]
    interval = measurement.intervals.get("F0.5")
    if swap and interval is not None:
        interval = interval.negated()
    return [
        _effect_check("F0.5", interval),
        _guard_check(
            "GLEU",
            _mean_range([measurement.figures[code].gleu[first] for code in codes]),
            _mean_range([measurement.figures[code].gleu[second] for code in codes]),
        ),
        _band_check(
            [measurement.figures[code].page[first] for code in codes],
            [measurement.figures[code].page[second] for code in codes],
        ),
        _repeats_check(
            _mean_range([measurement.figures[code].f_beta[first] for code in codes]),
            _mean_range([measurement.figures[code].f_beta[second] for code in codes]),
        ),
    ]


def _effect_check(name: str, interval: Interval | None) -> Check:
    """Return the check on one measure's interval: above zero, and at least MIN_EFFECT."""
    if interval is None:
        return Check(name, False, "no text to draw", "a text in every deciding language")
    return Check(
        name,
        interval.low > 0 and interval.delta >= MIN_EFFECT,
        f"Δ {interval.delta:+.3f} [{interval.low:+.3f}, {interval.high:+.3f}]",
        f"above 0 and at least {MIN_EFFECT:.2f}",
    )


def _guard_check(name: str, left: grouping.Range | None, right: grouping.Range | None) -> Check:
    """Return a guard on the means of one measure: not below the other's by more than MIN_EFFECT."""
    if left is None or right is None:
        return Check(name, False, "a repeat has no score", "a score in every repeat")
    return Check(
        name,
        left.mean >= right.mean - MIN_EFFECT,
        f"{left.mean:.3f} against {right.mean:.3f}",
        f"not below by more than {MIN_EFFECT:.2f}",
    )


def _band_check(
    left: Sequence[Sequence[float | None]], right: Sequence[Sequence[float | None]]
) -> Check:
    """Return the page-band guard: five texts per language, enough for "not worse".

    A group without a page text has no band to guard, so the check is a diagnostic and does not
    stop a comparison: the guard is for the test set's page band, and a set without one says
    nothing either way.
    """
    name = f"{grouping.PAGE_BAND} band, F0.5"
    if not any(value is not None for values in left for value in values) or not any(
        value is not None for values in right for value in values
    ):
        return Check(name, None, "no page text", "", kind=CheckKind.DIAGNOSTIC)
    return _guard_check(name, _mean_range(left), _mean_range(right))


def _repeats_check(left: grouping.Range | None, right: grouping.Range | None) -> Check:
    """Return the spread of the repeats, printed next to the verdict and never decisive.

    At a low temperature the repeats collapse, so a gap of 0.001 would look like a finding; the
    interval over the texts is what decides.
    """
    return Check("F0.5, repeats", None, _spread(left, right), "", kind=CheckKind.DIAGNOSTIC)


def _measures(measurement: PairMeasurement) -> tuple[MeasureVerdict, ...]:
    """Return one line per measure: its interval and what that interval says."""
    return tuple(
        MeasureVerdict(name=name, interval=interval, verdict=_verdict(interval))
        for name, interval in measurement.intervals.items()
    )


def _verdict(interval: Interval) -> Verdict:
    """Return what one interval says: better, worse, or a tie by the minimum effect."""
    if interval.low > 0 and interval.delta >= MIN_EFFECT:
        return Verdict.BETTER
    if interval.high < 0 and interval.delta <= -MIN_EFFECT:
        return Verdict.WORSE
    return Verdict.TIE


def _dominance(
    measurement: PairMeasurement, measures: Sequence[MeasureVerdict], comparison: Comparison
) -> str:
    """Return which model dominates the other, never against the verdict.

    A model dominates when the verdict is in its favour, no measure says it is worse, and it is
    not slower; a tie or a verdict the guards decided is "neither", so the line cannot name a
    winner the verdict did not.
    """
    if not measures:
        return "neither dominates: no measure could be drawn"
    verdicts = [measure.verdict for measure in measures]
    if comparison is Comparison.BETTER and Verdict.WORSE not in verdicts:
        if _not_slower(measurement.a, measurement.b):
            return f"`{measurement.a.key.model}` dominates `{measurement.b.key.model}`"
    if comparison is Comparison.WORSE and Verdict.BETTER not in verdicts:
        if _not_slower(measurement.b, measurement.a):
            return f"`{measurement.b.key.model}` dominates `{measurement.a.key.model}`"
    return "neither dominates"


def _not_slower(one: grouping.RunGroup, other: grouping.RunGroup) -> bool:
    """Whether *one* is not more than TIME_TOLERANCE slower per 100 words than *other*."""
    return _seconds_per_100_words(one) <= _seconds_per_100_words(other) * (1 + TIME_TOLERANCE)


def _values(
    parts: Sequence[Sequence[summary.ModelSummary]],
    code: str,
    measure,
) -> tuple[float | None, ...]:
    """Return one language's value of *measure* in each repeat."""
    return tuple(measure(part, code) for part in parts)


def _f_beta(part: Sequence[summary.ModelSummary], code: str) -> float | None:
    """Return the F0.5 of one language in one repeat."""
    row = grouping.language_row(part, code)
    return None if row is None else row.f_beta


def _gleu(part: Sequence[summary.ModelSummary], code: str) -> float | None:
    """Return the GLEU of one language in one repeat."""
    row = grouping.language_row(part, code)
    return None if row is None else row.gleu


def _page(part: Sequence[summary.ModelSummary], code: str) -> float | None:
    """Return the F0.5 of one language's ``page`` band in one repeat."""
    return next(
        (
            row.f_beta
            for row in part
            if row.band == grouping.PAGE_BAND and row.language == code and row.f_beta is not None
        ),
        None,
    )


def _range(values: Sequence[float | None]) -> grouping.Range | None:
    """Return the range of *values*, or ``None`` when a repeat has no value."""
    if any(value is None for value in values):
        return None
    return grouping.Range.of(value for value in values if value is not None)


def _mean_range(per_language: Sequence[Sequence[float | None]]) -> grouping.Range | None:
    """Return the range of the mean over the languages, repeat by repeat, or ``None``.

    A repeat in which no language has a value has no mean, and the range is then ``None``: the
    spread would be of fewer repeats than the group has.
    """
    if not per_language:
        return None
    length = len(per_language[0])
    if any(len(values) != length for values in per_language):
        return None
    means = []
    for index in range(length):
        found = [value for values in per_language if (value := values[index]) is not None]
        if not found:
            return None
        means.append(sum(found) / len(found))
    return grouping.Range.of(means)


def _per_language(measurement: PairMeasurement) -> tuple[Check, ...]:
    """Return the figures of each language, printed above the verdict and never decisive."""
    checks = []
    for code, figures in measurement.figures.items():
        checks.append(
            Check(
                f"F0.5, {code}",
                None,
                _spread(_range(figures.f_beta[0]), _range(figures.f_beta[1])),
                "",
                language=code,
                kind=CheckKind.DIAGNOSTIC,
            )
        )
        checks.append(
            Check(
                f"F0.5, texts, {code}",
                None,
                f"P(Δ ≤ 0) = {figures.chance:.3f}",
                "",
                language=code,
                kind=CheckKind.DIAGNOSTIC,
            )
        )
        checks.append(
            Check(
                f"GLEU, {code}",
                None,
                _spread(_range(figures.gleu[0]), _range(figures.gleu[1])),
                "",
                language=code,
                kind=CheckKind.DIAGNOSTIC,
            )
        )
        checks.append(
            Check(
                f"{grouping.PAGE_BAND} band, F0.5, {code}",
                None,
                _spread(_range(figures.page[0]), _range(figures.page[1])),
                "",
                language=code,
                kind=CheckKind.DIAGNOSTIC,
            )
        )
    return tuple(checks)


def _spread(left: grouping.Range | None, right: grouping.Range | None) -> str:
    """Return two ranges as one figure, or a dash when a repeat has no value."""
    if left is None or right is None:
        return "a repeat has no score"
    return (
        f"{left.mean:.3f} [{left.low:.3f}-{left.high:.3f}] against "
        f"{right.mean:.3f} [{right.low:.3f}-{right.high:.3f}]"
    )


Counts = tuple[int, int, int, int, int, int, int]
"""One text's counts, pooled over the repeats: ``tp, fp, fn, alone, alone_of, cover, cover_of``."""

Measure = Callable[[Sequence[Counts]], float | None]
"""A number a comparison reads, from the counts of the drawn texts."""


def _score_of(counts: Sequence[Counts]) -> EditScore:
    """Return the pooled edit score of the drawn texts."""
    return EditScore(
        true_positives=sum(found[0] for found in counts),
        false_positives=sum(found[1] for found in counts),
        false_negatives=sum(found[2] for found in counts),
    )


def _f_beta_of(counts: Sequence[Counts]) -> float | None:
    """Return the F0.5 of the pooled counts, or ``None`` when nothing was scored."""
    return scoring.f_beta(_score_of(counts))


def _precision_of(counts: Sequence[Counts]) -> float | None:
    """Return the share of the model's changes the reference agrees with, or ``None``."""
    return scoring.precision_recall(_score_of(counts))[0]


def _recall_of(counts: Sequence[Counts]) -> float | None:
    """Return the share of the reference's changes the model made, or ``None``."""
    return scoring.precision_recall(_score_of(counts))[1]


def _left_alone_of(counts: Sequence[Counts]) -> float | None:
    """Return the share of the sentences the reference leaves alone that the model left alone."""
    alone = sum(found[3] for found in counts)
    alone_of = sum(found[4] for found in counts)
    return alone / alone_of if alone_of else None


def _coverage_of(counts: Sequence[Counts]) -> float | None:
    """Return the share of the sentences the model covered."""
    cover = sum(found[5] for found in counts)
    cover_of = sum(found[6] for found in counts)
    return cover / cover_of if cover_of else None


MEASURES: dict[str, Measure] = {
    "F0.5": _f_beta_of,
    "recall": _recall_of,
    "precision": _precision_of,
    "left alone": _left_alone_of,
    "coverage": _coverage_of,
}
"""The numbers a comparison prints, each with its own interval."""


def paired_bootstrap(
    a: grouping.RunGroup,
    b: grouping.RunGroup,
    measures: Mapping[str, Measure] | None = None,
    *,
    deciding: tuple[str, ...] = DECIDING_LANGUAGES,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Interval]:
    """Return the interval of each measure of *a* minus *b*, over the drawn texts.

    Per text and model the counts are pooled over the repeats; per language as many texts are
    drawn as it has, with replacement, the same draw for both models, and the mean over the
    deciding languages is taken.  One set of draws serves every measure, so the intervals agree
    with each other.  The seed is fixed, so every machine gets the same numbers.

    A measure counts only where it can be measured in every deciding language, in a draw as in
    the point estimate: a draw in which one language has no sentence to measure it by is left
    out of that measure, so the interval and ``delta`` average over the same languages.

    Args:
        a: The first model's runs.
        b: The second model's runs.
        measures: The numbers to measure, by name; :data:`MEASURES` when not given.
        deciding: The languages whose mean decides.
        draws: How many draws to make.
        seed: The seed of the draws.

    Returns:
        One interval per measure that could be measured; a measure that cannot be measured in
        every deciding language in any draw is left out.
    """
    return _bootstrap(a, b, measures, deciding=deciding, draws=draws, seed=seed)[0]


def _bootstrap(
    a: grouping.RunGroup,
    b: grouping.RunGroup,
    measures: Mapping[str, Measure] | None = None,
    *,
    deciding: tuple[str, ...],
    also: Sequence[str] = (),
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[dict[str, Interval], dict[str, float]]:
    """Return the intervals of the deciding mean and ``P(Δ ≤ 0)`` of F0.5 per language.

    The languages of *also* are drawn with the same draws, so their own chance costs no second
    bootstrap.  They never enter the deciding mean.
    """
    measures = MEASURES if measures is None else measures
    rng = random.Random(seed)  # a reproducible draw, not a security decision
    left = _per_text(a, (*deciding, *also))
    right = _per_text(b, (*deciding, *also))
    both = [code for code in dict.fromkeys((*deciding, *also)) if code in left and code in right]
    languages = [code for code in both if code in deciding]
    if not languages:
        return {}, {}
    drawn: dict[str, list[float]] = {name: [] for name in measures}
    own: dict[str, list[float]] = {code: [] for code in both}
    for _ in range(draws):
        deltas: dict[str, dict[str, float]] = {name: {} for name in measures}
        for code in both:
            items = sorted(left[code])
            draw = [items[rng.randrange(len(items))] for _ in items]
            first = [left[code][item] for item in draw]
            second = [right[code][item] for item in draw]
            for name, measure in measures.items():
                if code not in deciding and name != "F0.5":
                    continue
                one = measure(first)
                other = measure(second)
                if one is not None and other is not None:
                    deltas[name][code] = one - other
        for name, found in deltas.items():
            if all(code in found for code in languages):
                drawn[name].append(sum(found[code] for code in languages) / len(languages))
        for code in both:
            if code in deltas.get("F0.5", {}):
                own[code].append(deltas["F0.5"][code])
    intervals: dict[str, Interval] = {}
    for name, measure in measures.items():
        values = drawn[name]
        if not values:
            continue
        delta = _measured_difference(left, right, languages, measure)
        if delta is None:
            continue
        ordered = sorted(values)
        intervals[name] = Interval(
            delta=delta,
            low=_quantile(ordered, 0.025),
            high=_quantile(ordered, 0.975),
            p_not_better=sum(1 for value in values if value <= 0) / len(values),
            p_not_worse=sum(1 for value in values if value >= 0) / len(values),
        )
    chances = {
        code: sum(1 for value in values if value <= 0) / len(values)
        for code, values in own.items()
        if values
    }
    return intervals, chances


def _measured_difference(
    left: Mapping[str, Mapping[str, Counts]],
    right: Mapping[str, Mapping[str, Counts]],
    languages: Sequence[str],
    measure: Measure,
) -> float | None:
    """Return the mean over *languages* of *measure* of *left* minus *right*, or ``None``.

    ``None`` when the measure is undefined in any of *languages*, as in a draw.
    """
    deltas = []
    for code in languages:
        one = measure(list(left[code].values()))
        other = measure(list(right[code].values()))
        if one is None or other is None:
            return None
        deltas.append(one - other)
    return sum(deltas) / len(deltas) if deltas else None


def _quantile(ordered: Sequence[float], share: float) -> float:
    """Return the *share* point of *ordered*, which is sorted, by nearest rank."""
    return ordered[round(share * (len(ordered) - 1))]


def bootstrap(
    a: grouping.RunGroup,
    b: grouping.RunGroup,
    *,
    deciding: tuple[str, ...] = DECIDING_LANGUAGES,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> float:
    """Return ``P(Δ ≤ 0)`` for the mean F0.5 of *a* minus *b* over the deciding languages.

    The share of the draws in which *a* is not better, which the per-language figures print; the
    verdict reads the interval of :func:`paired_bootstrap` instead.  One language alone gives that
    language's own bootstrap.
    """
    found = paired_bootstrap(a, b, {"F0.5": _f_beta_of}, deciding=deciding, draws=draws, seed=seed)
    return found["F0.5"].p_not_better if "F0.5" in found else 1.0


def _per_text(group: grouping.RunGroup, languages: Sequence[str]) -> dict[str, dict[str, Counts]]:
    """Return ``{language: {item: counts}}`` of *languages*, the counts pooled over the repeats.

    Error texts only: a ``correct`` text has no errors to compare, and the language rows are built
    from the error texts too.
    """
    pooled: dict[str, dict[str, list[int]]] = {}
    for items in group.items:
        for item in items:
            if item.language not in languages or item.kind != "errors":
                continue
            counts = pooled.setdefault(item.language, {}).setdefault(item.item, [0] * 7)
            counts[0] += item.true_positives
            counts[1] += item.false_positives
            counts[2] += item.false_negatives
            counts[3] += int(item.scores.get("alone", 0))
            counts[4] += int(item.scores.get("alone_of", 0))
            counts[5] += int(item.scores.get("cover", 0))
            counts[6] += int(item.scores.get("cover_of", 0))
    return {
        code: {
            item: (found[0], found[1], found[2], found[3], found[4], found[5], found[6])
            for item, found in by_item.items()
        }
        for code, by_item in pooled.items()
    }


def side_by_side(a: grouping.RunGroup, b: grouping.RunGroup) -> tuple[Check, ...]:
    """Return the figures printed next to a comparison that never decide it."""
    checks = [
        Check(
            "temperature",
            None,
            f"{a.key.temperature:g} / {b.key.temperature:g}",
            "",
            kind=CheckKind.DIAGNOSTIC,
        ),
        _side("left alone", a, b, "alone", "alone_of"),
        _side("coverage", a, b, "cover", "cover_of"),
        _side("explained share", a, b, "explained", "needed"),
        _side_count("cut-offs the second try absorbed", a, b),
        _side_speed(a, b),
        _side_memory(a, b),
    ]
    return tuple(checks)


def _side(
    name: str, a: grouping.RunGroup, b: grouping.RunGroup, numerator: str, denominator: str
) -> Check:
    """Return one pooled share of both models, side by side."""
    left = _share_of(a, numerator, denominator)
    right = _share_of(b, numerator, denominator)
    return Check(name, None, f"{left} / {right}", "", kind=CheckKind.DIAGNOSTIC)


def _share_of(group: grouping.RunGroup, numerator: str, denominator: str) -> str:
    """Return one pooled share of the deciding languages, as two decimals, or a dash."""
    pooled = _pooled_deciding(group.summaries())
    if not pooled[denominator]:
        return "—"
    return f"{pooled[numerator] / pooled[denominator]:.2f}"


def _side_count(name: str, a: grouping.RunGroup, b: grouping.RunGroup) -> Check:
    """Return one count of both models, side by side."""
    left = sum(sum(absorbed_cut_offs(items).values()) for items in a.items)
    right = sum(sum(absorbed_cut_offs(items).values()) for items in b.items)
    return Check(name, None, f"{left} / {right}", "", kind=CheckKind.DIAGNOSTIC)


def _side_speed(a: grouping.RunGroup, b: grouping.RunGroup) -> Check:
    """Return the seconds per 100 words of both models, side by side."""
    return Check(
        "seconds per 100 words",
        None,
        f"{_speed(a).value} / {_speed(b).value}",
        "",
        kind=CheckKind.DIAGNOSTIC,
    )


def _side_memory(a: grouping.RunGroup, b: grouping.RunGroup) -> Check:
    """Return the memory Ollama reported for both models, side by side."""
    return Check("memory, GB", None, f"{_memory(a)} / {_memory(b)}", "", kind=CheckKind.DIAGNOSTIC)


def _memory(group: grouping.RunGroup) -> str:
    """Return the largest size Ollama reported for the model, in GB, or a dash."""
    sizes = [
        loaded.size_bytes
        for items in group.items
        for item in items
        if item.performance is not None
        for loaded in item.performance.loaded
        if normalize_tag(loaded.name) == normalize_tag(group.key.model)
    ]
    return f"{max(sizes) / 1e9:.1f}" if sizes else "—"


__all__ = [
    "BOOTSTRAP_DRAWS",
    "BOOTSTRAP_SEED",
    "MEASURES",
    "MIN_EFFECT",
    "TIME_TOLERANCE",
    "Check",
    "Comparison",
    "ComparisonResult",
    "GateTwoRule",
    "Interval",
    "Judgement",
    "LanguageFigures",
    "MeasureVerdict",
    "Measurement",
    "Outcome",
    "PairMeasurement",
    "Verdict",
    "absorbed_cut_offs",
    "bootstrap",
    "compare",
    "measure_pair",
    "paired_bootstrap",
    "rate_pair",
    "side_by_side",
]
