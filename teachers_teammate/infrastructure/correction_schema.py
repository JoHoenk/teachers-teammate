"""Pydantic schema, contract text and prompt plumbing for a structured correction review.

The model answers with this schema; :func:`to_review` converts the validated answer to the
pure domain model in :mod:`teachers_teammate.domain.correction_review`.  The **answer format**
(:mod:`teachers_teammate.domain.answer_format`) tells the model how to fill in this structure;
it is English for every language and names no language, because it addresses the model, not the
teacher.

``pydantic`` arrives with ``ollama`` and ``langchain-core``, so importing this module adds no
dependency.  Only the infrastructure layer may import it; the domain stays pydantic-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from ..domain.answer_format import ANSWER_FORMAT_VERSION
from ..domain.correction_policy import DEFAULT_LEVEL, OUTPUT_MODES  # noqa: F401  # re-exported
from ..domain.correction_review import (
    ChangeNote,
    CorrectionReview,
    SentenceNote,
    UnclearNote,
)
from ..domain.formatted_text import TextFormat
from ..domain.proofreading.word_diff import similarity

CONTRACT_VERSION = ANSWER_FORMAT_VERSION
"""Bumped (in ``domain/answer_format.py``) whenever the answer format or the schema changes:
it is part of the cache key."""


class Change(BaseModel):
    """One edit inside a sentence (the Pydantic mirror of :class:`ChangeNote`)."""

    original: str = Field(description='Exact words from the student sentence; "" for an insertion.')
    corrected: str = Field(description='Replacement words; "" for a deletion.')
    category: Literal["grammar", "spelling", "punctuation", "word_choice", "word_order", "other"]
    explanation: str = Field(description="One short sentence saying why the edit was made.")


class SentenceReview(BaseModel):
    """One changed sentence or one that contains an unclear expression."""

    original: str = Field(description="The sentence exactly as written (the anchor).")
    corrected: str = Field(description='Corrected sentence, without any "[unclear]" marker.')
    changes: list[Change] = Field(
        description="One entry per separate edit; empty when the sentence is only unclear."
    )


class UnclearExpression(BaseModel):
    """One unclear or ambiguous expression, even a grammatically correct one."""

    original_expression: str = Field(description="Exact words from the text (the anchor).")
    why_unclear: str = Field(description="What makes it unclear or ambiguous.")
    recommended_version: str = Field(description="A possible clearer version.")
    meaning_determinable: bool = Field(
        description="False when the intended meaning cannot be determined from the text."
    )


class CorrectionAnswer(BaseModel):
    """The complete answer: the changed sentences and the list of unclear expressions."""

    sentences: list[SentenceReview] = Field(
        description="Only sentences that were changed or contain an unclear expression."
    )
    unclear: list[UnclearExpression] = Field(
        description=(
            "Only expressions whose meaning is unclear or ambiguous, never an error that was "
            "already corrected; often empty."
        )
    )


def fill_placeholders(policy: str, *, level: str) -> str:
    """Fill the policy's known placeholders with ``str.replace``.

    ``str.format`` would raise on any stray brace in a teacher's custom prompt; only the
    documented names are substituted here, and everything else is left alone.
    """
    return policy.replace("{level}", level or DEFAULT_LEVEL)


def escape_braces(text: str) -> str:
    """Escape every brace so *text* is safe inside a LangChain prompt template."""
    return text.replace("{", "{{").replace("}", "}}")


UNRELATED_RATIO = 0.5
"""Below this similarity a ``corrected`` is not a correction of its sentence.

A real correction of a sentence with mistakes keeps well over half of its characters; a
word-order change or a new verb form is far above this.  What falls below is a placeholder
(the field's own name, a sentence of another text), and storing it would replace the
student's sentence with something that has nothing to do with it.
"""


@dataclass(frozen=True)
class Repairs:
    """What :func:`clean_answer` had to fix, counted so a model that needs it stays visible."""

    marks: int = 0
    """Sentences whose answer carried markup the source did not have."""
    noop: int = 0
    """Changes whose two sides are the same words."""
    junk: int = 0
    """Sentences whose ``corrected`` was not a correction of the sentence."""


def clean_answer(
    answer: CorrectionAnswer, text_format: TextFormat
) -> tuple[CorrectionAnswer, Repairs]:
    """Return *answer* with the three things removed that are not proofreading.

    The review path splices ``corrected`` verbatim into the text, so what the model wrote
    around the correction would reach the teacher's document.  Three cases, all of them
    cheap to recognise and none of them a judgement about the correction itself:

    * Markup in a plain text (``**went**``): removed, as the corrections-only path already
      does with :func:`~teachers_teammate.infrastructure.ocr_formats.normalize_llm_answer`.
      A Markdown text is left alone: there the marks are the student's.
    * A ``corrected`` that is not the sentence (:data:`UNRELATED_RATIO`): the sentence stays
      as it was, because a placeholder must never replace the student's words, and so do not
      its changes, which would explain corrections the text does not carry.
    * A change whose ``original`` and ``corrected`` are the same words: dropped, because it
      explains nothing.
    """
    from .ocr_formats import looks_like_markup, plain_text_of  # noqa: PLC0415

    marks = noop = junk = 0
    sentences: list[SentenceReview] = []
    for sentence in answer.sentences:
        corrected = sentence.corrected
        changes = sentence.changes
        if text_format != "markdown" and corrected.strip():
            if looks_like_markup(corrected) and not looks_like_markup(sentence.original):
                cleaned = plain_text_of(corrected, "markdown")
                marks += cleaned != corrected
                corrected = cleaned
        if corrected.strip() and sentence.original.strip():
            if similarity(_normal(sentence.original), _normal(corrected)) < UNRELATED_RATIO:
                junk += 1
                corrected = sentence.original
                changes = []
        kept = [
            change for change in changes if _normal(change.original) != _normal(change.corrected)
        ]
        noop += len(changes) - len(kept)
        sentences.append(
            SentenceReview(original=sentence.original, corrected=corrected, changes=kept)
        )
    return answer.model_copy(update={"sentences": sentences}), Repairs(
        marks=marks, noop=noop, junk=junk
    )


def _normal(text: str) -> str:
    """Return *text* with every run of whitespace collapsed, for comparing two answers."""
    return " ".join(text.split())


def to_review(answer: CorrectionAnswer, *, unplaced: tuple[int, ...] = ()) -> CorrectionReview:
    """Convert a validated *answer* to the domain model, tagging it with the unplaced indices."""
    return CorrectionReview(
        sentences=tuple(
            SentenceNote(
                original=sentence.original,
                corrected=sentence.corrected,
                changes=tuple(
                    ChangeNote(
                        original=change.original,
                        corrected=change.corrected,
                        category=change.category,
                        explanation=change.explanation,
                    )
                    for change in sentence.changes
                ),
            )
            for sentence in answer.sentences
        ),
        unclear=tuple(
            UnclearNote(
                expression=item.original_expression,
                why=item.why_unclear,
                recommendation=item.recommended_version,
                is_suggestion=not item.meaning_determinable,
            )
            for item in answer.unclear
        ),
        unplaced=unplaced,
    )


def validate_answer(raw: str) -> tuple[CorrectionAnswer | None, str]:
    """Return ``(answer, "")`` for valid *raw* JSON, else ``(None, error_message)``.

    The error message is sent back to the model in the repair retry, so it must name what
    was wrong in the answer rather than describe an internal failure.
    """
    try:
        return CorrectionAnswer.model_validate_json(raw), ""
    except Exception as exc:  # noqa: BLE001  # any pydantic validation error is a repairable answer
        return None, str(exc)


def repair_message(previous: str, error: str) -> str:
    """Return the message that asks the model to answer the same question again, validly."""
    return (
        "Your previous answer was not valid JSON for the structure described in the"
        " instructions.\n\n"
        f"Validation error:\n{error}\n\n"
        f"Your previous answer was:\n{previous}\n\n"
        "Answer again, in the same way, with only the JSON object described in the"
        " instructions."
    )


def schema_json() -> dict:
    """Return the JSON schema Ollama's ``format`` parameter constrains the answer to."""
    return CorrectionAnswer.model_json_schema()
