"""The printed text of a run's documents: what the proofreading leaves alone.

One worksheet's printed text repeats in every document of it — the header, the tasks, the form
labels — while a student's own words do not.  The detection (:mod:`domain.task_text`) finds those
lines across the documents of a run, and this service stores what it found with each document, so
the proofreading leaves them as they came back from the reader.

The detection reads the transcription as it was read, the one reader of a document's texts
besides ``merged_view``: the printed lines are what the scan shows, and the teacher's fixes of the
student's sentences do not move them.  The marks are then looked for in the current transcription
(``domain.document_state.task_text_of``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from ..config import Config
from ..domain.task_text import TaskDocument, TaskTextFound, detect_task_text, new_passages
from .formatting import TextFormatter
from .ports import TaskTextStore, WorkLookup


class TaskTextService:
    """Finds the printed text the documents of a queue share, and marks it."""

    def __init__(
        self, *, state_store_factory: Callable[[Config], TaskTextStore], work: WorkLookup
    ) -> None:
        self._state_store = state_store_factory
        self._work = work

    def mark_a_queue(self, config: Config, files: Sequence[Path]) -> TaskTextFound:
        """Store the printed text found in *files*, and say what was found.

        The documents of *files* with a transcription are compared.  What a document already
        holds stays: a passage is added only when it marks a line the document's marks do not
        mark yet, and none is dropped.
        """
        store = self._state_store(config)
        documents: list[TaskDocument] = []
        by_name: dict[str, Path] = {}
        for file in files:
            state = store.load_valid(file)
            if state is None or not state.raw_text.strip():
                continue
            name = str(file.resolve())
            by_name[name] = file
            work = self._work.load(state.source_hash)
            documents.append(
                TaskDocument(
                    name=name,
                    # The reading itself, not the teacher's transcription: see the module.
                    plain=TextFormatter.plain_text(state.raw_text, state.raw_text_format),
                    marks=state.task_marks,
                    not_task=work.not_task if work is not None else (),
                )
            )
        detection = detect_task_text(documents)
        read = {document.name: document for document in documents}
        for name, texts in detection.passages.items():
            document = read[name]
            fresh = new_passages(document.plain, document.marks, texts, document.not_task)
            if fresh:
                store.add_task_passages(by_name[name], fresh)
        return TaskTextFound(
            read=len(documents),
            documents=detection.documents_with_task_text,
            worksheets=len(detection.worksheets),
        )
