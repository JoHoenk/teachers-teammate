"""A comparison as the window's rows: plain words first, then a widget draws them.

The three tabs show the same comparison three ways, and none of them decides anything: the
sites in text order for *Changes*, the sentences of every version for *Side by side*, and one
column of figures per version for *Overview*.  What a row holds is worked out here, away from
Qt, so a test reads the rows without a window.

Pure: no Qt, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from ...domain.proofreading.model_comparison import (
    ChangeSite,
    Comparison,
    Option,
    VersionFigures,
)
from ...texts import compare as txt
from ...texts.review import format_span

__all__ = [
    "ChangeRow",
    "SideRow",
    "Sites",
    "change_rows",
    "kept",
    "overview_cells",
    "side_rows",
]


class Sites(StrEnum):
    """Which change sites the *Changes* tab shows; ``txt.FILTERS`` words each one."""

    ALL = "all"
    """Every place any model changed."""

    DISAGREE = "disagree"
    """The places where the models wrote different replacements."""

    ONE = "one"
    """The places only one model changed, where the others saw nothing to do."""


@dataclass(frozen=True)
class ChangeRow:
    """One place a model changed the student's text, as the *Changes* tab lists it."""

    original: str
    """The student's words there; empty for an insertion."""

    cells: tuple[str, ...]
    """What each version wrote there, as its cell reads, in the order of the columns.

    A dash where it changed nothing, a word where it deleted the words, every wording it wrote
    when there are several, and a note on a wording that only one of its runs wrote."""

    agreed: int
    """How many versions wrote the replacement most of them chose."""


@dataclass(frozen=True)
class SideRow:
    """One row of the *Side by side* tab: a sentence as the student and every version read it."""

    source: str
    versions: tuple[str, ...]
    changed: tuple[bool, ...]
    """Whether each version changed anything here, in the order of the columns."""


def change_rows(comparison: Comparison, *, only: Sites = Sites.ALL) -> tuple[ChangeRow, ...]:
    """Return the change sites of *comparison* as rows, keeping the ones *only* asks for."""
    rows: list[ChangeRow] = []
    for site in comparison.sites:
        if not kept(site, only):
            continue
        rows.append(
            ChangeRow(
                original=site.original,
                cells=tuple(
                    _cell([option for option in site.options if figures.key in option.keys])
                    for figures in comparison.versions
                ),
                agreed=site.agreed,
            )
        )
    return tuple(rows)


def _cell(options: Sequence[Option]) -> str:
    """Return one version's cell at a place: its wordings there, or a dash when it left it."""
    if not options:
        return txt.UNCHANGED
    return txt.OPTION_SEPARATOR.join(
        txt.cell_wording(option.replacement, once=option.once) for option in options
    )


def kept(site: ChangeSite, only: Sites) -> bool:
    """Return whether a site belongs in a tab that shows *only*."""
    if only is Sites.DISAGREE:
        return len(site.options) > 1
    if only is Sites.ONE:
        return sum(len(option.made_by) for option in site.options) == 1
    return True


def side_rows(comparison: Comparison) -> tuple[SideRow, ...]:
    """Return the rows of the *Side by side* tab, one per sentence as every version reads it."""
    return tuple(
        SideRow(source=row.source, versions=row.versions, changed=row.changed)
        for row in comparison.rows
    )


def overview_cells(figures: Sequence[VersionFigures]) -> tuple[tuple[str, ...], ...]:
    """Return one tuple of cells per row of the *Overview*, in the order of ``OVERVIEW_ROWS``."""
    return tuple(
        tuple(cell(figures_of_version) for figures_of_version in figures) for cell in _CELLS
    )


def _time(figures: VersionFigures) -> str:
    """Return how long this answer took, loading the model included."""
    return format_span(figures.seconds, figures.seconds) if figures.seconds else txt.NOT_RUN


def _per_hundred_words(figures: VersionFigures) -> str:
    """Return the time this answer took per 100 words, so two texts compare."""
    return format_span(figures.seconds_per_100_words, figures.seconds_per_100_words)


def _explained(figures: VersionFigures) -> str:
    """Return how many of the changes this model explained, against how many needed one."""
    return txt.explained(figures.explained.explained, figures.explained.needed)


def _problems(figures: VersionFigures) -> str:
    """Return what is wrong with this answer, as the words of the window."""
    return ", ".join(txt.PROBLEMS[problem] for problem in figures.problems) or txt.NO_PROBLEMS


def _in_every_run(figures: VersionFigures) -> str:
    """Return how many of its changes this model made in every run; a dash while it ran once."""
    return txt.UNCHANGED if figures.runs < 2 else str(figures.in_every_run)


def _once(figures: VersionFigures) -> str:
    """Return how many of its changes this model made in one run only; a dash while it ran once."""
    return txt.UNCHANGED if figures.runs < 2 else str(figures.once)


def _score(figures: VersionFigures) -> str:
    """Return F0.5 against the teacher's marks, or a dash while a change is still unmarked."""
    found = figures.marked.f_beta
    return txt.UNCHANGED if found is None else f"{found:.2f}"


#: One function per row of the *Overview*, in the order of ``txt.OVERVIEW_ROWS``.
_CELLS = (
    _time,
    _per_hundred_words,
    lambda figures: str(figures.changes),
    lambda figures: str(figures.shared),
    lambda figures: str(figures.alone),
    _in_every_run,
    _once,
    _explained,
    lambda figures: str(figures.unclear),
    _problems,
    lambda figures: str(figures.marked.right),
    lambda figures: str(figures.marked.wrong),
    lambda figures: str(figures.marked.unmarked),
    lambda figures: str(figures.marked.missed),
    _score,
)
