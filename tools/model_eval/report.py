"""The reports the writing-model comparison produces, rendered from typed rows.

All are pure: rows in, markdown out.  The table answers "how close to the reference?" per model
and language; the band table and the by-error view say where the difference sits; the performance
table says what a text costs.  The explanation sheet is :mod:`.spot_check`, because it is drawn
from a committed run and not from a cache.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from . import scoring
from .error_labels import UNLABELLED, ReferenceLabel
from .languages import name as language_name
from .languages import ordered
from .performance import per_100_words
from .quality import match_edits
from .results import ResultRow
from .scoring import ScoredItem
from .summary import ModelSummary, share_of, summarise

__all__ = [
    "DECIDING_LANGUAGES",
    "DIAGNOSTIC_BANDS",
    "ModelSummary",
    "PerformanceSummary",
    "band_table",
    "error_table",
    "performance_table",
    "summarise",
    "summarise_performance",
    "table",
]

DECIDING_LANGUAGES = scoring.DECIDING_LANGUAGES
"""The languages whose mean decides: the two the test set has bands for."""

DIAGNOSTIC_BANDS: frozenset[str] = frozenset({"pages"})
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


def _fallback_cell(summary: ModelSummary) -> str:
    """Return the fallbacks of a row, and how many of them the window cut off."""
    if not summary.cut_off:
        return str(summary.fallback)
    return f"{summary.fallback} ({summary.cut_off} cut)"


def table(summaries: Iterable[ModelSummary]) -> str:
    """Return the results table as markdown: one row per configuration and language.

    Only the rows over all texts of a language are printed; :func:`band_table` shows the bands.
    A language whose texts scored nothing shows a dash and is left out of the deciding mean,
    which counts only the languages that have a score.
    """
    rows = [summary for summary in summaries if not summary.band]
    lines = [
        "| Model | T | Mode | Language | P | R | F0.5 | GLEU | Left alone | Coverage "
        "| Recall 1st / 2nd half | Failed | Fallback | Added marks | Explained | Orphans/text "
        "| s/text |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- "
        "| --- | --- |",
    ]
    for summary in rows:
        lines.append(
            f"| `{summary.model}` | {summary.temperature:g} | {summary.mode} "
            f"| {language_name(summary.language)} "
            f"| {_share(summary.precision)} | {_share(summary.recall)} | {_share(summary.f_beta)} "
            f"| {_gleu_cell(summary)} "
            f"| {summary.alone}/{summary.alone_of} | {_coverage_cell(summary)} "
            f"| {_halves_cell(summary)} "
            f"| {summary.failed}/{summary.rows} | {_fallback_cell(summary)} "
            f"| {summary.added_marks}/{summary.rows} "
            f"| {summary.explained}/{summary.needed} "
            f"| {summary.orphans_per_text:.1f} | {summary.seconds_per_text:.0f} |"
        )
    lines += _deciding_lines(rows)
    return "\n".join(lines)


def band_table(summaries: Iterable[ModelSummary]) -> str:
    """Return the band rows as markdown: one row per configuration, language and band.

    The bands hold few texts each (4, 5 and 2 per language), so a band row is a diagnostic and
    not a decision; the ``pages`` band is marked as such.  The rows of one language add up to its
    row in :func:`table`, because both are counts and not means.  A run without bands (an older
    one, or French alone) gives an empty string, not a table with only a header.
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
            f"| `{summary.model}` | {summary.temperature:g} | {summary.mode} "
            f"| {language_name(summary.language)} | {band} | {summary.rows} "
            f"| {_share(summary.precision)} | {_share(summary.recall)} | {_share(summary.f_beta)} "
            f"| {_gleu_cell(summary)} | {summary.alone}/{summary.alone_of} "
            f"| {_coverage_cell(summary)} | {_halves_cell(summary)} "
            f"| {_fallback_cell(summary)} |"
        )
    return "\n".join(lines)


def _deciding_lines(summaries: Sequence[ModelSummary]) -> list[str]:
    """Return the mean F0.5 and GLEU over the deciding languages, per configuration."""
    parts: dict[tuple[str, float, str], dict[str, ModelSummary]] = {}
    for summary in summaries:
        if summary.language in DECIDING_LANGUAGES:
            parts.setdefault((summary.model, summary.temperature, summary.mode), {})[
                summary.language
            ] = summary
    lines = ["", "Mean over German and English, the languages that decide:", ""]
    for (model, temperature, mode), by_language in sorted(parts.items()):
        scored = {
            code: part
            for code, part in by_language.items()
            if part.f_beta is not None or part.gleu is not None
        }
        f_values = [part.f_beta for part in scored.values() if part.f_beta is not None]
        f_beta = sum(f_values) / len(f_values) if f_values else None
        mean_gleu = scoring.mean_gleu({code: part.gleu for code, part in scored.items()})
        lines.append(
            f"- `{model}` at {temperature:g} ({mode}): F0.5 {_share(f_beta)}, "
            f"GLEU {_share(mean_gleu)} ({len(scored)} languages)"
        )
    lines += ["", "French is reported only: it has no band and does not decide."]
    return lines


# ── which kinds of error a model finds ────────────────────────────────────────

CATEGORY_ORDER: tuple[str, ...] = (
    "grammar",
    "spelling",
    "punctuation",
    "word_choice",
    "word_order",
    "other",
    UNLABELLED,
)
"""The error groups of ``--by-error category``, in the order of the columns."""


@dataclass
class _Found:
    """How many errors of one kind a model met: all, found exactly, found at least in place."""

    total: int = 0
    exact: int = 0
    located: int = 0

    def cell(self) -> str:
        """Return ``exact (located) / total``, or a dash when there was no error of this kind."""
        if not self.total:
            return "-"
        return f"{self.exact} ({self.located}) / {self.total}"


def _group_of(label: ReferenceLabel, by: str) -> str:
    """Return the group a reference error belongs to: its app category or its corpus type."""
    group = label.category if by == "category" else label.main_type
    return group or UNLABELLED


def error_table(
    rows: Sequence[ResultRow],
    labels: Mapping[str, Sequence[ReferenceLabel]],
    *,
    by: str = "category",
) -> str:
    """Return, per language, how many errors of each kind every model found, as markdown.

    A cell reads ``exact (located) / total``: of that many errors in the test set, the model made
    exactly the reference change for ``exact`` of them and changed at least the same words for
    ``located``.  The gap between the two is "noticed it, fixed it differently", which the
    F-score counts as a miss.

    Args:
        rows: The (already re-scored) result rows.
        labels: The corpus labels of the test records, keyed ``language/id``
            (:func:`results.read_labels`).
        by: ``"category"`` groups by the app's six categories, ``"type"`` by the kind of word
            the corpus label names (``DET``, ``VERB``…).
    """
    found: dict[tuple[str, float, str, str], dict[str, _Found]] = defaultdict(
        lambda: defaultdict(_Found)
    )
    agreement: dict[tuple[str, float, str], list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        row_labels = labels.get(row.id, ())
        if row.kind != "errors" or not row_labels:
            continue
        by_edit = {label.edit: label for label in row_labels}
        for match in match_edits(row.source, row.output, row.reference):
            label = by_edit.get(match.edit)
            if label is None:
                continue
            counts = found[(row.model, row.temperature, row.mode, row.language)][
                _group_of(label, by)
            ]
            counts.total += 1
            counts.exact += match.exact
            counts.located += match.located
        total = agreement[(row.model, row.temperature, row.mode)]
        total[0] += row.category_checked
        total[1] += row.category_agreed
    if not found:
        return "No labelled error texts: build the test set again with the corpus labels."

    lines: list[str] = []
    for language in ordered({key[3] for key in found}):
        part = {key: groups for key, groups in found.items() if key[3] == language}
        if not part:
            continue
        present = {group for groups in part.values() for group in groups}
        columns = [name for name in CATEGORY_ORDER if name in present] + sorted(
            present - set(CATEGORY_ORDER)
        )
        lines += [
            f"### {language_name(language)}: errors found exactly (found in place) / errors",
            "",
            "| Model | T | Mode | " + " | ".join(columns) + " | all |",
            "| --- | --- | --- | " + " | ".join("---" for _ in columns) + " | --- |",
        ]
        for (model, temperature, mode, _), groups in sorted(part.items()):
            everything = _Found(
                sum(g.total for g in groups.values()),
                sum(g.exact for g in groups.values()),
                sum(g.located for g in groups.values()),
            )
            cells = " | ".join(groups.get(name, _Found()).cell() for name in columns)
            lines.append(
                f"| `{model}` | {temperature:g} | {mode} | {cells} | {everything.cell()} |"
            )
        lines.append("")

    lines += [
        "### Category the model gave, where it fixed the error exactly",
        "",
        "Compared with the category the corpus label stands for; errors labelled `other` are not "
        "checked.  A rough diagnostic: the corpus labels were not made for these categories.",
        "",
        "| Model | T | Mode | Same category |",
        "| --- | --- | --- | --- |",
    ]
    for (model, temperature, mode), (checked, agreed) in sorted(agreement.items()):
        share = f"{agreed}/{checked} ({agreed / checked:.0%})" if checked else "-"
        lines.append(f"| `{model}` | {temperature:g} | {mode} | {share} |")
    return "\n".join(lines)


# ── what the texts cost ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class PerformanceSummary:
    """Speed and memory of one model, temperature and answer mode, over the texts it was run on."""

    model: str
    temperature: float
    mode: str
    texts: int
    """Texts with a performance record."""

    comparable: int
    """Of those, the texts that were not shared with another model on the server."""

    seconds_per_100_words: float | None
    answer_tokens_per_s: float | None
    answer_tokens_per_100_words: float | None
    requests_per_text: float | None
    vram_used_mb: int | None
    """Highest memory the model added on the card, over the comparable texts."""

    on_card_share: float | None
    """Lowest share of the model on the card, over the comparable texts."""


def summarise_performance(items: Sequence[ScoredItem]) -> list[PerformanceSummary]:
    """Return one performance summary per model, temperature and mode.

    Only texts that were **not shared** with another loaded model enter the figures: a second
    model on the card changes both speed and memory.  Texts written before performance was
    recorded are skipped.  The words are the manifest's count, marks included, so a figure here
    and the same figure in the index mean the same thing.
    """
    summaries: list[PerformanceSummary] = []
    for (model, temperature, mode), group in scoring.by_configuration(items):
        measured = [row for row in group if row.performance is not None]
        if not measured:
            continue
        clean = [row for row in measured if row.performance and not row.performance.shared]
        words = sum(row.words for row in clean)
        answer = sum(row.performance.answer_tokens for row in clean if row.performance)
        generation = sum(row.performance.generation_s for row in clean if row.performance)
        used = [
            row.performance.vram_used_mb
            for row in clean
            if row.performance and row.performance.vram_used_mb is not None
        ]
        shares = [
            share
            for row in clean
            if row.performance and (share := row.performance.on_card_share(model)) is not None
        ]
        summaries.append(
            PerformanceSummary(
                model=model,
                temperature=temperature,
                mode=mode,
                texts=len(measured),
                comparable=len(clean),
                seconds_per_100_words=(
                    per_100_words(sum(row.seconds for row in clean), words) if words else None
                ),
                answer_tokens_per_s=answer / generation if generation else None,
                answer_tokens_per_100_words=per_100_words(answer, words) if words else None,
                requests_per_text=(
                    sum(row.performance.request_count for row in clean if row.performance)
                    / len(clean)
                    if clean
                    else None
                ),
                vram_used_mb=max(used) if used else None,
                on_card_share=min(shares) if shares else None,
            )
        )
    return summaries


def performance_table(summaries: Iterable[PerformanceSummary]) -> str:
    """Return the speed and memory table as markdown.

    ``-`` marks a figure that could not be computed: no comparable text, no sampler, or a server
    that reports no durations.
    """

    def number(value: float | int | None, spec: str) -> str:
        return "-" if value is None else format(value, spec)

    lines = [
        "| Model | T | Mode | Texts (comparable) | s per 100 words | Answer tokens/s "
        "| Answer tokens per 100 words | Requests/text | Graphics memory MB | On the card |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in summaries:
        share = "-" if item.on_card_share is None else f"{item.on_card_share:.0%}"
        lines.append(
            f"| `{item.model}` | {item.temperature:g} | {item.mode} "
            f"| {item.texts} ({item.comparable}) "
            f"| {number(item.seconds_per_100_words, '.1f')} "
            f"| {number(item.answer_tokens_per_s, '.0f')} "
            f"| {number(item.answer_tokens_per_100_words, '.0f')} "
            f"| {number(item.requests_per_text, '.2f')} "
            f"| {number(item.vram_used_mb, 'd')} | {share} |"
        )
    lines += [
        "",
        "Only texts that were not shared with another loaded model count.  Answer tokens/s "
        "says how fast the machine runs the model; seconds per 100 words of student text is "
        "what a teacher waits.  Graphics memory is the peak the model added over the idle baseline, "
        'read from the card.  "On the card" is Ollama\'s own estimate and is unreliable for some '
        "models (it reported 0.4 GB for a Gemma 4 model that added 3.4 GB): trust the memory "
        "column.",
    ]
    return "\n".join(lines)
