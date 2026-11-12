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
