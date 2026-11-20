"""What the changes a model makes that the reference does not are: the false-alarm table.

The F-score counts a wrong change and a missed one alike, so a model that "fixes" a comma and one
that rewrites a sentence cost the same.  This table says which it is, in two dimensions:

* **where** — ``alt``: the change touches the words of a reference change, so the model noticed the
  error and fixed it differently; ``kept``: it lies in a sentence the reference leaves alone, so the
  model changed text nobody asked it to touch; ``extra``: it lies in an error sentence, but not
  where the reference changes anything.
* **kind** — what the change does to the words: ``comma``, ``punct``, ``case``, ``bre_ame`` (British
  against American spelling), ``small`` (one word, two characters apart), ``delete``, ``insert``,
  ``reword``, and ``space`` for a change that only moves whitespace.

The figures are per error text of a language, so two models that were run on different numbers of
texts can be read side by side.  The classification is a heuristic and says so: it is a diagnostic
of *what kind* of noise a model makes, not a verdict on a single change.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import re

from teachers_teammate.domain.proofreading.word_diff import tokenize

from ...core.constants import TextKind
from ...core.languages import name as language_name
from ...core.languages import ordered
from ...metrics.offsets import token_offsets
from ...metrics.quality import edits, spans_touch, word_positions
from .. import groups
from ..pooled import TextItem

WHERE_ORDER: tuple[str, ...] = ("alt", "kept", "extra")
"""The places a false alarm can sit in, in the order of the columns."""

KIND_ORDER: tuple[str, ...] = (
    "comma",
    "punct",
    "case",
    "bre_ame",
    "small",
    "delete",
    "insert",
    "reword",
    "space",
)
"""The kinds of change a false alarm can be, in the order of the columns."""

_BRITISH_AMERICAN: tuple[tuple[str, str], ...] = (
    (r"ize", r"ise"),
    (r"yze", r"yse"),
    (r"or\b", r"our\b"),
    (r"er\b", r"re\b"),
    (r"ense\b", r"ence\b"),
    (r"ll", r"l"),
)
"""Spelling pairs that differ only by the variety of English; a change between them is not an error."""

_NON_WORD = re.compile(r"[\W_]+")
"""What is left out when two spellings are compared: marks and underscores."""


def _core(text: str) -> str:
    """Return *text* without its marks, so ``"word,"`` and ``"word"`` compare equal."""
    return _NON_WORD.sub("", text)


def _distance(a: str, b: str) -> int:
    """Return the Levenshtein distance of two strings: how many characters turn one into the other."""
    previous = list(range(len(b) + 1))
    for row, left in enumerate(a, 1):
        current = [row]
        for column, right in enumerate(b, 1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left != right),
                )
            )
        previous = current
    return previous[-1]


def kind_of(source_text: str, replacement: str) -> str:
    """Return the kind of one change: what it does to the words it replaces.

    Args:
        source_text: The words the change removes, as they stand in the source.
        replacement: The words it puts there instead.
    """
    before, after = source_text.strip(), replacement.strip()
    if not before and not after:
        return "space"
    if _core(before) == _core(after):
        marks = (set(before) ^ set(after)) - {" ", "\n"}
        return "comma" if marks <= {","} else "punct"
    if _core(before).lower() == _core(after).lower():
        return "case"
    if not after:
        return "delete"
    if not before:
        return "insert"
    low_before, low_after = before.lower(), after.lower()
    for british, american in _BRITISH_AMERICAN:
        if re.sub(british, "#", low_before) == re.sub(american, "#", low_after) or re.sub(
            american, "#", low_before
        ) == re.sub(british, "#", low_after):
            return "bre_ame"
    if " " not in before and " " not in after and _distance(low_before, low_after) <= 2:
        return "small"
    return "reword"


def _sentence_at(spans: Sequence[tuple[int, int]], start: int, end: int) -> int | None:
    """Return the index of the sentence a character span sits in, or ``None`` when it sits in none.

    An insertion at the very end of a sentence belongs to that sentence, as the corpus's own
    segmentation counts it.
    """
    for index, (first, last) in enumerate(spans):
        if first <= start < last or (start == end == last):
            return index
    return None


@dataclass(frozen=True)
class FalseAlarm:
    """One change a model made that the reference does not, classified."""

    where: str
    kind: str
    source_text: str
    """The words the change removes, as they stand in the source."""
    replacement: str
    """The words it puts there instead."""


def classify(item: TextItem) -> list[FalseAlarm]:
    """Return the false alarms of one scored text, classified.

    A change is a false alarm when it is not one of the reference's changes.  The reference's
    changes are compared as word-diff changes, so a change that touches the same words with another
    replacement is a false alarm too — it is the ``alt`` kind, and the F-score counts it as a miss.
    """
    source = item.record.source
    reference = edits(source, item.record.reference)
    tokens = tokenize(source)
    positions = word_positions(tokens)
    reference_spans = [(positions[start], positions[end]) for start, end, _ in reference]
    offsets = token_offsets(source)
    unchanged = set(item.record.unchanged_sentences)
    found: list[FalseAlarm] = []
    for edit in sorted(edits(source, item.output)):
        if edit in reference:
            continue
        start, end, replacement = edit
        span = (positions[start], positions[end])
        sentence = _sentence_at(item.record.sentence_spans, offsets[start], offsets[end])
        if any(spans_touch(span, other) for other in reference_spans):
            where = "alt"
        elif sentence is not None and sentence in unchanged:
            where = "kept"
        else:
            where = "extra"
        source_text = "".join(tokens[start:end])
        found.append(
            FalseAlarm(
                where=where,
                kind=kind_of(source_text, "".join(replacement)),
                source_text=source_text,
                replacement="".join(replacement),
            )
        )
    return found


@dataclass(frozen=True)
class Tally:
    """The false alarms of one model in one language, and how many texts they came from."""

    texts: int
    counts: Mapping[tuple[str, str], int]
    """How many false alarms of each ``(where, kind)`` the model made."""

    @property
    def total(self) -> int:
        """How many false alarms the model made in the language."""
        return sum(self.counts.values())

    def per_text(self, where: str = "", kind: str = "") -> float | None:
        """Return the false alarms of one place or kind per text, or ``None`` without texts."""
        if not self.texts:
            return None
        return (
            sum(
                count
                for (found_where, found_kind), count in self.counts.items()
                if (not where or found_where == where) and (not kind or found_kind == kind)
            )
            / self.texts
        )


def tally(
    items: Iterable[TextItem], languages: Sequence[str] = groups.DECIDING_LANGUAGES
) -> dict[str, dict[str, Tally]]:
    """Return ``{model: {language: tally}}`` of the error texts of *languages*.

    A ``correct`` text is left out: it has no reference change, so every change a model makes in it
    is a false alarm of the ``kept`` kind, and counting those would mix two questions.
    """
    wanted = set(languages)
    texts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    counts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for item in items:
        if item.kind != TextKind.ERRORS or item.language not in wanted:
            continue
        texts[item.model][item.language] += 1
        for alarm in classify(item):
            counts[item.model][item.language][(alarm.where, alarm.kind)] += 1
    return {
        model: {
            language: Tally(texts=texts[model][language], counts=dict(counts[model][language]))
            for language in texts[model]
        }
        for model in texts
    }


def false_alarm_table(
    items: Iterable[TextItem], languages: Sequence[str] = groups.DECIDING_LANGUAGES
) -> str:
    """Return the false alarms as markdown: one table per language, one row per model.

    Args:
        items: The (already scored) texts of the store.
        languages: The languages to print, in the order of the tables.
    """
    found = tally(items, languages)
    if not found:
        return "No error text of the languages asked for: nothing to classify."
    lines: list[str] = []
    for language in ordered({code for by_language in found.values() for code in by_language}):
        part = {
            model: by_language[language]
            for model, by_language in found.items()
            if language in by_language
        }
        if not part:
            continue
        lines += [
            f"### {language_name(language)}: false alarms per error text",
            "",
            "| Model | Texts | False alarms/text | "
            + " | ".join(WHERE_ORDER)
            + " | "
            + " | ".join(KIND_ORDER)
            + " |",
            "| --- | --- | --- | "
            + " | ".join("---" for _ in WHERE_ORDER)
            + " | "
            + " | ".join("---" for _ in KIND_ORDER)
            + " |",
        ]
        for model, row in sorted(part.items()):
            cells = [_per_text(row.per_text(where=where)) for where in WHERE_ORDER]
            cells += [_per_text(row.per_text(kind=kind)) for kind in KIND_ORDER]
            lines.append(
                f"| `{model}` | {row.texts} | {_per_text(row.per_text())} | "
                + " | ".join(cells)
                + " |"
            )
        lines.append("")
    lines += [
        "`alt` is a change that touches the words of a reference change (noticed, fixed "
        "differently); `kept` one in a sentence the reference leaves alone; `extra` one elsewhere "
        "in an error sentence.  The kinds are a heuristic: `small` is one word two characters "
        "apart, `bre_ame` a British against an American spelling.",
    ]
    return "\n".join(lines)


def _per_text(value: float | None) -> str:
    """Return a per-text figure as one decimal, or a dash when the language has no text."""
    return "—" if value is None else f"{value:.1f}"


__all__ = [
    "KIND_ORDER",
    "WHERE_ORDER",
    "FalseAlarm",
    "Tally",
    "classify",
    "false_alarm_table",
    "kind_of",
    "tally",
]
