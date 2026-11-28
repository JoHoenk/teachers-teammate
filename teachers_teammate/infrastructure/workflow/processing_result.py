"""What one file's processing answers: a saved file, one left out, one stopped, one that failed.

The stages of a file raise :class:`Stopped` to say that the teacher pressed "Stop now".
:class:`~teachers_teammate.infrastructure.workflow.file_processor.FileProcessor` turns every way
out into a :class:`ProcessingResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...domain.formatted_text import TextFormat

if TYPE_CHECKING:
    from ...domain.proofreading.review import CorrectionReview
    from ...domain.stages import Stage


@dataclass(frozen=True)
class ProcessingResult:
    """Result of processing a single input file through all pipeline stages.

    A saved file carries its texts (the run counts the changes of its proofreading); a file that
    failed, was left out or was stopped carries the sentence a teacher reads and nothing else.
    """

    ok: bool
    message: str
    preview_img: str = ""
    raw_text: str = ""
    correction_text: str = ""
    evaluation_text: str = ""
    warnings: tuple[str, ...] = ()
    text_format: TextFormat = "plain"
    """Notation of ``raw_text`` and ``correction_text`` (``"plain"`` or ``"markdown"``)."""
    correction_review: CorrectionReview | None = None
    """Explanations and unclear expressions of the proofreading, ``None`` without a review."""
    correction_warning: str = ""
    """Why this file has no review (e.g. the fallback to corrections-only); empty when none."""
    skipped: bool = False
    """The file was left out on purpose (a scan while text recognition is off): neither a
    success nor a failure.  ``ok`` is ``False`` and ``message`` says why."""
    stopped: bool = False
    """The run was stopped before this file was complete: neither a success nor a failure.
    ``ok`` is ``False`` and ``message`` says before which step; the steps that were done are
    stored and reused by the next run."""
    stopped_stage: str = ""
    """With ``stopped``: the :class:`~teachers_teammate.domain.stages.Stage` the stop cut short
    (``"ocr"``, ``"correction"`` or ``"evaluation"``); empty when the file did not get as far as
    telling."""

    @classmethod
    def failure(cls, message: str) -> ProcessingResult:
        """Return the result of a file that failed, with the sentence a teacher reads."""
        return cls(False, message)

    @classmethod
    def left_out(cls, message: str) -> ProcessingResult:
        """Return the result of a file skipped on purpose; *message* says why."""
        return cls(False, message, skipped=True)

    @classmethod
    def stopped_at(
        cls,
        stage: str = "",
        *,
        preview_img: str = "",
        raw_text: str = "",
        correction_text: str = "",
        text_format: TextFormat = "plain",
        correction_review: CorrectionReview | None = None,
        correction_warning: str = "",
    ) -> ProcessingResult:
        """Return the result of a file stopped at *stage*, carrying only what is stored.

        *stage* stays empty when no stage of this file turned the stop into a result.
        """
        return cls(
            False,
            "",
            preview_img=preview_img,
            raw_text=raw_text,
            correction_text=correction_text,
            text_format=text_format,
            correction_review=correction_review,
            correction_warning=correction_warning,
            stopped=True,
            stopped_stage=stage,
        )


class Stopped(Exception):
    """Ends this file because the teacher pressed "Stop now"; it carries the result to report.

    Not a failure and not an error: the steps that were done are stored, and the next run picks
    them up.
    """

    def __init__(self, result: ProcessingResult) -> None:
        super().__init__()
        self.result = result


def stopped_result(
    stage: Stage,
    *,
    preview_img: str = "",
    raw_text: str = "",
    correction_text: str = "",
    text_format: TextFormat = "plain",
    correction_review: CorrectionReview | None = None,
    correction_warning: str = "",
) -> Stopped:
    """Return the stop that ends a file at *stage*.

    It carries only what is already stored (the texts, and the review and warning that belong
    to a stored correction), so the row shows what the next run reuses.
    """
    return Stopped(
        ProcessingResult.stopped_at(
            stage,
            preview_img=preview_img,
            raw_text=raw_text,
            correction_text=correction_text,
            text_format=text_format,
            correction_review=correction_review,
            correction_warning=correction_warning,
        )
    )
