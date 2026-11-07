"""The two reports the writing-model comparison produces, rendered from typed rows.

Both are pure: rows in, markdown out.  The table answers "how close to the reference?" per model
and language; the sheet is what a teacher marks by hand to judge the explanations themselves.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import random

from . import results
from .results import ResultRow

LANGUAGE_NAMES: dict[str, str] = {"de": "German", "en": "English", "fr": "French"}
"""Language codes of the test set and how they are printed."""

DEFAULT_BETA = 0.5


@dataclass(frozen=True)
class ModelSummary:
    """The per-model-and-language part of the table."""

    model: str
    temperature: float
    language: str
    precision: float
    recall: float
    f_beta: float
    kept_sentences: int
    total_sentences: int
    failed: int
    fallback: int
    added_marks: int
    rows: int
    explained: int
    needed: int
    orphans_per_text: float
    seconds_per_text: float


def summarise(rows: Sequence[ResultRow], beta: float = DEFAULT_BETA) -> list[ModelSummary]:
    """Return one summary per model, temperature and language that has scored rows.

    Args:
        rows: The (already re-scored) result rows.
        beta: Weight of the F-score; precision counts twice as much at ``0.5``.
    """
    summaries: list[ModelSummary] = []
    for (model, temperature), items in results.rows_by_model(rows):
        for language in LANGUAGE_NAMES:
            part = [row for row in items if row.language == language]
            errors = [row for row in part if row.kind == "errors"]
            if not errors:
                continue
            correct = [row for row in part if row.kind == "correct"]
            score = _combined_score(errors, beta)
            summaries.append(
                ModelSummary(
                    model=model,
                    temperature=temperature,
                    language=language,
                    precision=score[0],
                    recall=score[1],
                    f_beta=score[2],
                    kept_sentences=sum(row.unchanged for row in correct),
                    total_sentences=sum(len(row.sentences) for row in correct),
                    failed=sum(row.failed for row in part),
                    fallback=sum(row.fallback for row in part),
                    added_marks=sum(results.added_marks(row) for row in part),
                    rows=len(part),
                    explained=sum(row.explained for row in errors),
                    needed=sum(row.needed for row in errors),
                    orphans_per_text=sum(row.orphans for row in errors) / len(errors),
                    seconds_per_text=sum(row.seconds for row in part) / len(part),
                )
            )
    return summaries


def _combined_score(rows: Sequence[ResultRow], beta: float) -> tuple[float, float, float]:
    """Return ``(precision, recall, f_beta)`` over the summed counts of *rows*."""
    true_positives = sum(row.true_positives for row in rows)
    false_positives = sum(row.false_positives for row in rows)
    false_negatives = sum(row.false_negatives for row in rows)
    precision = (
        true_positives / (true_positives + false_positives)
        if true_positives + false_positives
        else 0.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if true_positives + false_negatives
        else 0.0
    )
    weighted = beta**2 * precision + recall
    f_beta = (1 + beta**2) * precision * recall / weighted if weighted else 0.0
    return precision, recall, f_beta


def table(summaries: Iterable[ModelSummary]) -> str:
    """Return the results table as markdown."""
    lines = [
        "| Model | T | Language | P | R | F0.5 | Left alone | Failed | Fallback "
        "| Added marks | Explained | Orphans/text | s/text |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    means: dict[tuple[str, float], list[float]] = {}
    for summary in summaries:
        lines.append(
            f"| `{summary.model}` | {summary.temperature:g} | {summary.language} "
            f"| {summary.precision:.2f} | {summary.recall:.2f} | {summary.f_beta:.2f} "
            f"| {summary.kept_sentences}/{summary.total_sentences} "
            f"| {summary.failed}/{summary.rows} | {summary.fallback}/{summary.rows} "
            f"| {summary.added_marks}/{summary.rows} "
            f"| {summary.explained}/{summary.needed} "
            f"| {summary.orphans_per_text:.1f} | {summary.seconds_per_text:.0f} |"
        )
        means.setdefault((summary.model, summary.temperature), []).append(summary.f_beta)
    lines.append("")
    lines.append("Mean F0.5 over the languages with a score:")
    lines.append("")
    for (model, temperature), values in sorted(means.items()):
        average = sum(values) / len(values)
        lines.append(f"- `{model}` at {temperature:g}: {average:.3f} ({len(values)} languages)")
    return "\n".join(lines)


def spot_check_sheet(
    rows: Sequence[ResultRow],
    *,
    models: Sequence[str],
    temperature: float,
    per_language: int = 10,
    seed: int = 7,
) -> str:
    """Return the teacher's spot-check list as markdown.

    Args:
        rows: The result rows; only ``"errors"`` texts contribute notes.
        models: The models to include, in the order they should appear.
        temperature: Only rows at this temperature are sampled.
        per_language: How many notes per model and language.
        seed: Seed of the sample, so two runs of the tool give the same sheet.
    """
    rng = random.Random(seed)  # a reproducible sample, not a security decision
    lines = [
        "# Spot-check of explanations",
        "",
        "Mark each explanation `correct`, `wrong` or `vague`.  Text is from public learner "
        "corpora; do not publish this sheet.",
        "",
    ]
    for model in models:
        for language, language_name in LANGUAGE_NAMES.items():
            notes = [
                (row.id, note)
                for row in rows
                if row.model == model
                and row.temperature == temperature
                and row.language == language
                and row.kind == "errors"
                for note in row.notes
                if str(note.get("explanation", "")).strip()
            ]
            if not notes:
                continue
            lines.extend(
                [
                    f"## `{model}`, {language_name}",
                    "",
                    "| # | Text | Change | Category | Explanation | Mark |",
                    "| --- | --- | --- | --- | --- | --- |",
                ]
            )
            for number, (text_id, note) in enumerate(
                rng.sample(notes, min(per_language, len(notes))), start=1
            ):
                change = f"{note.get('original') or '∅'} → {note.get('corrected') or '∅'}"
                lines.append(
                    f"| {number} | {text_id} | {change} | {note.get('category')} "
                    f"| {note.get('explanation')} |  |"
                )
            lines.append("")
    return "\n".join(lines)


def notes_of(review) -> tuple[dict, ...]:
    """Return a review's notes as plain dicts, for the spot-check sheet."""
    if review is None:
        return ()
    return tuple(
        note
        for sentence in review.sentences
        for note in (
            {
                "sentence": sentence.corrected,
                "original": change.original,
                "corrected": change.corrected,
                "category": change.category,
                "explanation": change.explanation,
            }
            for change in sentence.changes
        )
    )
