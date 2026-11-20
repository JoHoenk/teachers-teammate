"""Which kinds of error a model finds: the by-error table, per language.

The F-score says how much of a text a model got right; this says *what* it got right.  Every
reference change carries the corpus label of the error it fixes (``R:DET:FORM``, ``M:PUNCT``), so
the changes can be grouped by the app's category or by the kind of word the corpus names, and a
model that finds every determiner and no comma is a different model from one that does the reverse
at the same F-score.

A cell reads ``exact (located) / total``: of that many errors in the test set, the model made
exactly the reference change for ``exact`` of them and changed at least the same words for
``located``.  The gap between the two is "noticed it, fixed it differently", which the F-score
counts as a miss.

The second table is a rough diagnostic and not a verdict: it compares the category the model's note
gives with the category the corpus label stands for, over the errors it fixed exactly.  The corpus
labels were not made for these six categories, so a disagreement is a hint and not an error.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from ...core.constants import TextKind
from ...core.labels import CATEGORIES, UNLABELLED, ReferenceLabel
from ...core.languages import name as language_name
from ...core.languages import ordered
from ...metrics.offsets import token_offsets
from ...metrics.quality import match_edits
from ..pooled import TextItem

CATEGORY_ORDER: tuple[str, ...] = (*CATEGORIES, UNLABELLED)
"""The error groups of ``--by-error category``, in the order of the columns."""

BY_CHOICES: tuple[str, ...] = ("category", "type")
"""What ``--by-error`` may group by: the app's categories, or the corpus's kind of word."""


@dataclass
class Found:
    """How many errors of one kind a model met: all, found exactly, found at least in place."""

    total: int = 0
    exact: int = 0
    located: int = 0

    def cell(self) -> str:
        """Return ``exact (located) / total``, or a dash when there was no error of this kind."""
        if not self.total:
            return "-"
        return f"{self.exact} ({self.located}) / {self.total}"


def group_of(label: ReferenceLabel, by: str) -> str:
    """Return the group a reference error belongs to: its app category or its corpus type.

    Raises:
        ValueError: if *by* is neither ``"category"`` nor ``"type"``.
    """
    if by not in BY_CHOICES:
        allowed = ", ".join(BY_CHOICES)
        raise ValueError(f"unknown grouping {by!r}; use one of: {allowed}")
    group = label.category if by == "category" else label.main_type
    return group or UNLABELLED


def _labels_by_edit(item: TextItem) -> dict[tuple[int, int, str], ReferenceLabel]:
    """Return the record's labels keyed by the change they describe, in characters of the source.

    A label stores its replacement as text and a word-diff change as the tokens it puts there, so
    the two are compared as the text they spell: ``("ihrer", "Freundin")`` is ``"ihrer Freundin"``.
    """
    return {(label.start, label.end, label.replacement): label for label in item.record.labels}


def error_table(items: Sequence[TextItem], *, by: str = "category") -> str:
    """Return, per language, how many errors of each kind every model found, as markdown.

    Args:
        items: The (already scored) texts of the store.
        by: ``"category"`` groups by the app's six categories, ``"type"`` by the kind of word the
            corpus label names (``DET``, ``VERB``…).

    Raises:
        ValueError: if *by* is neither ``"category"`` nor ``"type"``.
    """
    if by not in BY_CHOICES:
        allowed = ", ".join(BY_CHOICES)
        raise ValueError(f"unknown grouping {by!r}; use one of: {allowed}")
    found: dict[tuple[str, float, str, str], dict[str, Found]] = defaultdict(
        lambda: defaultdict(Found)
    )
    agreement: dict[tuple[str, float, str], list[int]] = defaultdict(lambda: [0, 0])
    for item in items:
        if item.kind != TextKind.ERRORS or not item.record.labels:
            continue
        by_edit = _labels_by_edit(item)
        offsets = token_offsets(item.record.source)
        for match in match_edits(item.record.source, item.output, item.record.reference):
            start, end, replacement = match.edit
            label = by_edit.get((offsets[start], offsets[end], "".join(replacement)))
            if label is None:
                continue
            counts = found[(item.model, item.temperature, item.mode, item.language)][
                group_of(label, by)
            ]
            counts.total += 1
            counts.exact += match.exact
            counts.located += match.located
        total = agreement[(item.model, item.temperature, item.mode)]
        total[0] += item.category_checked
        total[1] += item.category_agreed
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
            everything = Found(
                sum(group.total for group in groups.values()),
                sum(group.exact for group in groups.values()),
                sum(group.located for group in groups.values()),
            )
            cells = " | ".join(groups.get(name, Found()).cell() for name in columns)
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


__all__ = ["BY_CHOICES", "CATEGORY_ORDER", "Found", "error_table", "group_of"]
