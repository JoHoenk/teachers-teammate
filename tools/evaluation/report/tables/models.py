"""How close to the reference: the results table, the band table and the texts that lost text.

The table answers "how close to the reference?" per model and language; the band table says where
the difference sits, because a model that is good on a short text and bad on a page is a different
model from one that is the reverse at the same F-score.  Both are pure: summaries in, markdown out.

Only the rows over all texts of a language are printed by :func:`model_table`; the bands are
:func:`band_table`'s.  A language whose texts scored nothing shows a dash and is left out of the
deciding mean, which counts only the languages that have a score.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ...core.languages import name as language_name
from ...metrics import text_loss
from .. import groups
from ..pooled import ItemFacts
from ..summary import ModelSummary, share_of

DIAGNOSTIC_BANDS: frozenset[str] = frozenset({groups.PAGES_BAND})
"""Bands with too few texts to decide on: printed, but marked as a diagnostic."""


def _share(value: float | None) -> str:
    """Return a score as two decimals, or a dash when nothing was scored."""
    return "—" if value is None else f"{value:.2f}"


def _gleu_cell(summary: ModelSummary) -> str:
    """Return the GLEU of a row next to the GLEU of its unchanged source."""
    if summary.gleu is None:
        return "—"
    return f"{summary.gleu:.2f} (src {_share(summary.gleu_source)})"


def _coverage_cell(summary: ModelSummary) -> str:
    """Return the share of the touched sentences the model looked at."""
    return _share(share_of(summary.cover, summary.cover_of))


def _halves_cell(summary: ModelSummary) -> str:
    """Return the recall of the first and of the second half of the texts."""
    return f"{_share(summary.recall_h1)} / {_share(summary.recall_h2)}"


def _located_cell(summary: ModelSummary) -> str:
    """Return the second recall and, in brackets, the share noticed but fixed differently."""
    if summary.recall_located is None:
        return "—"
    gap = summary.noticed_gap
    return (
        f"{_share(summary.recall_located)} (+{gap:.2f})"
        if gap is not None
        else _share(summary.recall_located)
    )


def _per_page_cell(value: float | None) -> str:
    """Return a per-page figure as one decimal, or a dash when the texts have no words."""
    return "—" if value is None else f"{value:.1f}"


def _fallback_cell(summary: ModelSummary) -> str:
    """Return the fallbacks of a row, and how many of them the window cut off."""
    if not summary.cut_off:
        return str(summary.fallback)
    return f"{summary.fallback} ({summary.cut_off} cut)"


def _explained_cell(summary: ModelSummary) -> str:
    """Return the explained share of a row, and how many texts were left out of it."""
    cell = f"{summary.explained}/{summary.needed}"
    if not summary.explanations_unknown:
        return cell
    return f"{cell} ({summary.explanations_unknown} unknown)"


def _loss_cell(summary: ModelSummary) -> str:
    """Return the texts that lost or added text, or a dash when the run was not measured for it."""
    if not summary.loss_checked:
        return "—"
    return f"{summary.lost_text} / {summary.added_text}"


def model_table(summaries: Iterable[ModelSummary]) -> str:
    """Return the results table as markdown: one row per configuration and language.

    Only the rows over all texts of a language are printed; :func:`band_table` shows the bands.
    """
    rows = [summary for summary in summaries if not summary.band]
    lines = [
        "| Model | T | Mode | Language | P | R | Found the spot | F0.5 | F0.5 lenient | GLEU "
        "| Left alone "
        "| Coverage | Recall 1st / 2nd half | Fixed/page | Wrong/page | Missed/page | Failed "
        "| Fallback | Lost / added | Added marks | Explained | Orphans/text | s/text |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- "
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for summary in rows:
        lines.append(
            f"| `{summary.label}` | {summary.temperature:g} | {summary.mode} "
            f"| {language_name(summary.language)} "
            f"| {_share(summary.precision)} | {_share(summary.recall)} "
            f"| {_located_cell(summary)} | {_share(summary.f_beta)} "
            f"| {_share(summary.lenient_f_beta())} | {_gleu_cell(summary)} "
            f"| {summary.alone}/{summary.alone_of} | {_coverage_cell(summary)} "
            f"| {_halves_cell(summary)} "
            f"| {_per_page_cell(summary.fixed_per_page)} "
            f"| {_per_page_cell(summary.wrong_per_page)} "
            f"| {_per_page_cell(summary.missed_per_page)} "
            f"| {summary.failed}/{summary.rows} | {_fallback_cell(summary)} "
            f"| {_loss_cell(summary)} "
            f"| {summary.added_marks}/{summary.rows} "
            f"| {_explained_cell(summary)} "
            f"| {summary.orphans_per_text:.1f} | {summary.seconds_per_text:.0f} |"
        )
    lines += _deciding_lines(rows)
    return "\n".join(lines)


def band_table(summaries: Iterable[ModelSummary]) -> str:
    """Return the band rows as markdown: one row per configuration, language and band.

    The bands hold few texts each, so a band row is a diagnostic and not a decision; the ``pages``
    band is marked as such.  The rows of one language add up to its row in :func:`model_table`,
    because both are counts and not means.  A run without bands (an older one, or French alone)
    gives an empty string, not a table with only a header.
    """
    rows = [summary for summary in summaries if summary.band]
    if not rows:
        return ""
    lines = [
        "| Model | T | Mode | Language | Band | Texts | P | R | F0.5 | GLEU | Left alone "
        "| Coverage | Recall 1st / 2nd half | Fallback |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for summary in rows:
        band = summary.band
        if band in DIAGNOSTIC_BANDS:
            band = f"{band} ({summary.rows} texts, diagnostic)"
        lines.append(
            f"| `{summary.label}` | {summary.temperature:g} | {summary.mode} "
            f"| {language_name(summary.language)} | {band} | {summary.rows} "
            f"| {_share(summary.precision)} | {_share(summary.recall)} | {_share(summary.f_beta)} "
            f"| {_gleu_cell(summary)} | {summary.alone}/{summary.alone_of} "
            f"| {_coverage_cell(summary)} | {_halves_cell(summary)} "
            f"| {_fallback_cell(summary)} |"
        )
    return "\n".join(lines)


def _deciding_lines(summaries: Sequence[ModelSummary]) -> list[str]:
    """Return the mean F0.5 and GLEU over the deciding languages, per configuration."""
    parts: dict[tuple[str, float, str], list[ModelSummary]] = {}
    for summary in summaries:
        parts.setdefault((summary.label, summary.temperature, summary.mode), []).append(summary)
    lines = ["", "Mean over German and English, the languages that decide:", ""]
    for (label, temperature, mode), part in sorted(parts.items()):
        scored = groups.deciding_rows(part)
        lines.append(
            f"- `{label}` at {temperature:g} ({mode}): "
            f"F0.5 {_share(groups.deciding_mean_f_beta(part))} "
            f"(lenient {_share(groups.deciding_mean_lenient_f_beta(part))}), "
            f"GLEU {_share(groups.deciding_mean_gleu(part))} ({len(scored)} languages)"
        )
    lines += ["", "French is reported only: it has no band and does not decide."]
    return lines


def loss_lines(items: Iterable[ItemFacts]) -> str:
    """Return one line per text that lost or added text, or an empty string when none did.

    The table counts them; this says which texts they are, so a run can be looked at without
    reading every row.  A row written before the words were stored is not listed: it was not
    measured, which is not the same as clean.
    """
    lines: list[str] = []
    for item in items:
        lost = text_loss.is_lost(item.scores)
        added = text_loss.is_added(item.scores)
        if not lost and not added:
            continue
        run = int(item.scores[text_loss.CUT_RUN if lost else text_loss.ADDED_RUN])
        lines.append(
            f"- `{item.model}` at {item.temperature:g} ({item.mode}) {item.item}: "
            f"{int(item.scores[text_loss.WORDS_REF])} → "
            f"{int(item.scores[text_loss.WORDS_OUT])} words, "
            f"{'cut' if lost else 'added'} {run}"
        )
    if not lines:
        return ""
    return "\n".join(["Texts that lost or added text (words in → out, longest run):", "", *lines])


__all__ = [
    "DIAGNOSTIC_BANDS",
    "band_table",
    "loss_lines",
    "model_table",
]
