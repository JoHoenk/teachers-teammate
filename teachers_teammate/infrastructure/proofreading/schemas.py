"""The answer shapes of steps ② and ③, and their parsers.

Step ② answers one note per edit of its batch; the ids are pinned by an enum and the list is
pinned to exactly the batch size, so a model cannot answer about an edit it was not asked about
and cannot leave one out silently.  Step ③ answers a list of unclear expressions, which is often
empty.

``pydantic`` arrives with ``ollama`` and ``langchain-core``, so importing this module adds no
dependency.  Only the infrastructure layer may import it; the domain stays pydantic-free.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, create_model

from ...domain.proofreading.edits import NoteAnswer
from ...domain.proofreading.review import CHANGE_CATEGORIES

Category = Literal["grammar", "spelling", "punctuation", "word_choice", "word_order", "other"]
"""The categories an explanation may name; the same list the review stores."""

assert set(Category.__args__) == set(CHANGE_CATEGORIES)  # ty: ignore[unresolved-attribute]


class ExplainNote(BaseModel):
    """The note for one numbered edit (step ②)."""

    id: int = Field(description="The edit number.")
    category: Category = Field(description="What kind of mistake the edit fixes.")
    explanation: str = Field(description="One short sentence for the teacher.")


class ExplainAnswer(BaseModel):
    """The answer of step ②: one note per edit of the batch."""

    edits: list[ExplainNote] = Field(description="One entry per edit number, in order.")


class UnclearItem(BaseModel):
    """One unclear or ambiguous expression (step ③)."""

    quote: str = Field(description="The exact words from the text.")
    why: str = Field(description="What makes the meaning unclear, for the teacher.")
    suggestion: str = Field(description="A clearer wording, or the question to ask the student.")
    determinable: bool = Field(
        description="False when the intended meaning cannot be told from the text."
    )


class UnclearAnswer(BaseModel):
    """The answer of step ③: the unclear expressions, often none."""

    unclear: list[UnclearItem] = Field(description="Often empty.")


def explain_schema(ids: Sequence[int]) -> type[BaseModel]:
    """Return the answer shape of step ②, with the ids of the batch pinned.

    The ids become an enum and the list is pinned to exactly one entry per id, so the answer
    cannot name an edit the batch did not ask about and cannot leave one out.  A model that
    ignores both is caught by :func:`~.edits.merge_notes`, which counts the unknown ids and asks
    for the missing ones once more.
    """
    numbers = tuple(ids)
    note = create_model(
        "ExplainNote",
        id=(Literal[numbers], Field(description="The edit number.")),  # ty: ignore[invalid-type-form]
        category=(Category, Field(description="What kind of mistake the edit fixes.")),
        explanation=(str, Field(description="One short sentence for the teacher.")),
    )
    return create_model(
        "ExplainAnswer",
        edits=(
            list[note],  # ty: ignore[invalid-type-form]
            Field(
                min_length=len(numbers),
                max_length=len(numbers),
                description="Exactly one entry per edit number, in order.",
            ),
        ),
    )


def parse_explain(raw: str) -> tuple[ExplainAnswer | None, str]:
    """Return ``(answer, "")`` for valid *raw* JSON, else ``(None, error_message)``.

    The error message is sent back to the model in the repair retry, so it must name what was
    wrong in the answer rather than describe an internal failure.
    """
    try:
        return ExplainAnswer.model_validate_json(raw), ""
    except ValidationError as error:
        return None, str(error)


def parse_unclear(raw: str) -> tuple[UnclearAnswer | None, str]:
    """Return ``(answer, "")`` for valid *raw* JSON, else ``(None, error_message)``."""
    try:
        return UnclearAnswer.model_validate_json(raw), ""
    except ValidationError as error:
        return None, str(error)


def explain_notes(answer: object) -> list[NoteAnswer]:
    """Return the notes of a validated explain answer, whatever model validated it.

    The provider may have validated the answer against the pinned schema of the batch, or the
    runner against :class:`ExplainAnswer`; both carry ``edits`` with the same three fields.
    """
    edits = getattr(answer, "edits", ())
    return [
        NoteAnswer(
            id=int(edit.id),
            category=str(edit.category),
            explanation=str(edit.explanation),
        )
        for edit in edits
    ]
