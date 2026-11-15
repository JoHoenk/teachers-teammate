"""The scorecard: per variant, language and band, per repeat, with the mean and the range.

Every number here comes from the functions the report and gate 2 already use:
:func:`~..summary.summarise` for the quality of one repeat, :func:`~..scoring.pooled` and
:func:`~..scoring.precision_recall` for a row over several languages, :func:`~..scoring.mean_gleu`
for a GLEU over languages, and :func:`~..grouping.Range` for the spread of the repeats.  The page
therefore cannot show a number the command line would print differently.

Two rules are in the shape of the data, not in the page:

* **Counts are never summed over the repeats.**  A row holds one value per repeat, and the mean
  and the range of them; a variant with one repeat says so.
* **An incomplete variant has no range.**  A repeat that lacks a text has no value for the row, and
  a spread of fewer repeats than the variant has would look tighter than it is.

The row over several languages is the one the report prints: the mean of the figures of German and
English, the languages that decide, with French reported only
(:data:`~..grouping.DECIDING_LANGUAGES`, ``report._deciding_lines``).  It is never the counts of
all languages added together, which no command of the report prints.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .. import scoring, summary
from ..grouping import DECIDING_LANGUAGES, Range
from ..performance import per_100_words
from ..quality import DEFAULT_BETA
from ..results import ResultRow
from ..summary import ModelSummary, share_of

ALL = "all"
"""The band key of a row over every band of a language."""

DECIDING = "+".join(DECIDING_LANGUAGES)
"""The language key of the row over the deciding languages (``de+en``): their mean."""

MEASURES: tuple[str, ...] = (
    "recall",
    "precision",
    "f_beta",
    "gleu",
    "alone",
    "explained",
    "seconds_per_100_words",
    "failed",
    "fallback",
)
"""The figures of one row, in the order the scorecard prints them."""


@dataclass(frozen=True)
class Measure:
    """One figure over the repeats of a variant: its values, their mean, and their spread."""

    values: tuple[float | None, ...]
    mean: float | None
    low: float | None
    high: float | None

    @classmethod
    def of(cls, values: Sequence[float | None], *, spread: bool = True) -> Measure:
        """Return the measure of *values*.

        Args:
            values: One value per repeat, ``None`` for a repeat that has no value for the row.
            spread: Whether the values may be spread; ``False`` for an incomplete variant, whose
                repeats did not see the same texts, so a spread would look tighter than it is.
        """
        found = tuple(values)
        if not found or any(value is None for value in found):
            return cls(values=found, mean=None, low=None, high=None)
        found_range = Range.of(value for value in found if value is not None)
        if not spread:
            return cls(values=found, mean=found_range.mean, low=None, high=None)
        return cls(values=found, mean=found_range.mean, low=found_range.low, high=found_range.high)

    @property
    def has_range(self) -> bool:
        """Whether the figure has a spread, which it has only when every repeat has a value."""
        return self.low is not None


@dataclass(frozen=True)
class Row:
    """One line of the scorecard: a variant's figures for one language and band."""

    language: str
    band: str
    """The band of the texts, or ``""`` for the row over all texts of the language."""

    texts: int
    repeats: tuple[int, ...]
    complete: bool
    """Whether every repeat of the variant holds every text of the test set."""
    measures: Mapping[str, Measure]

    @property
    def key(self) -> str:
        """Return the key the page asks for: ``de/page``, ``de/all``, ``de+en/all``."""
        return f"{self.language}/{self.band or ALL}"


@dataclass(frozen=True)
class VariantNumbers:
    """The scorecard of one variant: one row per language and band, and the rows over all."""

    variant: str
    rows: Mapping[str, Row]

    def row(self, language: str, band: str = "") -> Row | None:
        """Return the row of one language and band, or ``None`` when the variant has none."""
        return self.rows.get(f"{language}/{band or ALL}")


def build(
    variant: str,
    rows_by_repeat: Mapping[int, Sequence[ResultRow]],
    *,
    complete: bool,
    beta: float = DEFAULT_BETA,
) -> VariantNumbers:
    """Return the scorecard of one variant.

    Args:
        variant: The variant's id, which the page keys the numbers by.
        rows_by_repeat: The rows of each repeat, in the order the repeats are shown.
        complete: Whether every repeat holds every text of the test set.
        beta: Weight of the F-score; precision counts twice as much at ``0.5``.

    Raises:
        ValueError: if the rows of one repeat hold more than one model, temperature or mode,
            because then they are not one variant.
    """
    repeats = tuple(rows_by_repeat)
    per_repeat: dict[int, dict[tuple[str, str], ModelSummary]] = {}
    for repeat, rows in rows_by_repeat.items():
        summaries = summary.summarise(rows, beta)
        _check_one_configuration(variant, summaries)
        per_repeat[repeat] = {(part.language, part.band): part for part in summaries}
    keys = sorted({key for parts in per_repeat.values() for key in parts})
    bands = sorted({band for _, band in keys if band})
    built: dict[str, Row] = {}
    for language, band in keys:
        built[f"{language}/{band or ALL}"] = _row(
            language, band, per_repeat, rows_by_repeat, repeats, complete, beta
        )
    for band in ("", *bands):
        built[f"{DECIDING}/{band or ALL}"] = _row(
            DECIDING, band, per_repeat, rows_by_repeat, repeats, complete, beta
        )
    return VariantNumbers(variant=variant, rows=built)


def _check_one_configuration(variant: str, summaries: Sequence[ModelSummary]) -> None:
    """Refuse the summaries of more than one model, temperature or mode.

    Raises:
        ValueError: if the rows of one repeat are not one configuration.
    """
    found = {(part.model, part.temperature, part.mode) for part in summaries}
    if len(found) > 1:
        raise ValueError(
            f"{variant}: the rows of one repeat hold {len(found)} configurations; "
            "a variant is one model at one temperature in one mode"
        )


def _row(
    language: str,
    band: str,
    per_repeat: Mapping[int, Mapping[tuple[str, str], ModelSummary]],
    rows_by_repeat: Mapping[int, Sequence[ResultRow]],
    repeats: Sequence[int],
    complete: bool,
    beta: float,
) -> Row:
    """Return one row of the scorecard; for ``de+en`` the mean of the two languages' figures."""
    parts = [_parts(per_repeat.get(repeat, {}), language, band) for repeat in repeats]
    figures = [[_figures(part, beta) for part in found] for found in parts]

    def measure(values: Sequence[float | None]) -> Measure:
        """Return the measure of one figure, spread only when the variant is complete."""
        return Measure.of(values, spread=complete)

    def over(name: str) -> Measure:
        """Return one figure per repeat: the language's own, or the mean of the languages'."""
        return measure([_mean([one[name] for one in found]) for found in figures])

    measures = {
        "recall": over("recall"),
        "precision": over("precision"),
        "f_beta": over("f_beta"),
        "gleu": over("gleu"),
        "alone": over("alone"),
        "explained": over("explained"),
        "seconds_per_100_words": measure(
            [
                _seconds_per_100_words(rows_by_repeat.get(repeat, ()), language, band)
                for repeat in repeats
            ]
        ),
        "failed": measure([float(sum(part.failed for part in found)) for found in parts]),
        "fallback": measure([float(sum(part.fallback for part in found)) for found in parts]),
    }
    texts = max((sum(part.rows for part in found) for found in parts), default=0)
    return Row(
        language=language,
        band=band,
        texts=texts,
        repeats=tuple(repeats),
        complete=complete,
        measures=measures,
    )


def _figures(part: ModelSummary, beta: float) -> dict[str, float | None]:
    """Return the figures of one language's summary, as the report prints them."""
    return {
        "recall": part.recall,
        "precision": part.precision,
        "f_beta": scoring.f_beta(part.score, beta),
        "gleu": part.gleu,
        "alone": share_of(part.alone, part.alone_of),
        "explained": share_of(part.explained, part.needed),
    }


def _mean(values: Sequence[float | None]) -> float | None:
    """Return the mean of the values that exist, or ``None`` when none does.

    A language without a value (no reference edit, nothing scored) is left out of the mean, as
    ``report._deciding_lines`` leaves it out: counting it as 0 would drag the mean down.
    """
    found = [value for value in values if value is not None]
    return sum(found) / len(found) if found else None


def _parts(
    summaries: Mapping[tuple[str, str], ModelSummary], language: str, band: str
) -> list[ModelSummary]:
    """Return the summaries a row is made of: one language's, or the deciding languages'."""
    if language != DECIDING:
        found = summaries.get((language, band))
        return [found] if found is not None else []
    return [summaries[(code, band)] for code in DECIDING_LANGUAGES if (code, band) in summaries]


def _seconds_per_100_words(rows: Sequence[ResultRow], language: str, band: str) -> float | None:
    """Return the seconds a hundred words of student text took, or ``None`` without words.

    The same figure :func:`~..report.summarise_performance` prints, from the same helper, but over
    the texts of one language and band.
    """
    languages = DECIDING_LANGUAGES if language == DECIDING else (language,)
    part = [row for row in rows if row.language in languages and (not band or row.band == band)]
    words = sum(row.words for row in part)
    return per_100_words(sum(row.seconds for row in part), words) if words else None


__all__ = [
    "ALL",
    "DECIDING",
    "MEASURES",
    "Measure",
    "Row",
    "VariantNumbers",
    "build",
]
