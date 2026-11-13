"""The summary page's lines: what the assistant will set, and what it downloads.

:func:`summary_rows` turns a plan into the lines the page shows, so the wording can be tested
without a window; :class:`SummaryRows` draws them, one checkbox per download, and hands back the
ones the teacher left ticked.  The place words come from the caller
(:func:`.._data_flow_summary.short_place`), so the summary, the service list and the "uses" lines
never describe one place differently.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QWidget

from ...application.setup_plan import PlannedDownload, PlannedStep, SetupPlan
from ...domain.data_location import (
    STAGE_ANONYMIZATION,
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    STAGE_OCR,
)
from .. import _user_text as txt

#: The step names the summary uses; the same words as the "uses" lines in the main window.
STEP_NAMES: dict[str, str] = {
    STAGE_OCR: "Reading handwriting",
    STAGE_CORRECTION: "Proofreading",
    STAGE_EVALUATION: "Content review",
    STAGE_ANONYMIZATION: txt.REMOVE_PERSONAL_INFO,
}
CLASSES_NAME = "Your classes"

ROW_DOWNLOADED = "✓ downloaded"
ROW_INSTALLED = "✓ installed"
ROW_KEY_MISSING = "key missing"
ROW_OFF = "off (tick it in the main window when you want it)"
ROW_KEPT = "{engine} (kept)"
ROW_SAME_MODEL = "same model as proofreading"
ROW_KEEPS_OWN = "keeps its own model"
ROW_NAMES_ON = "on · name detection for {language}"
ROW_CLASSES = "{language} · {hand_in}"
HANDWRITTEN = "handwritten pages"
TYPED = "typed texts"
SIZE_SMALL = "small"
SIZE_GB = "{size:.1f} GB"

#: How wide the step name column is, so the details line up.
_NAME_WIDTH = 150
_HINT_STYLE = "color: #666;"


@dataclass(frozen=True)
class SummaryRow:
    """One line of the summary page."""

    #: The step's name, e.g. ``"Reading handwriting"``.
    name: str
    #: What the step uses, e.g. ``"qwen2.5vl:7b · this computer"``.
    detail: str
    #: What this row downloads; empty when there is nothing to download.
    downloads: tuple[PlannedDownload, ...] = ()
    #: The short note at the end of the row, or ``""``.
    note: str = ""


def size_text(download: PlannedDownload) -> str:
    """Return the download's size as the row shows it: ``"6.0 GB"`` or ``"small"``."""
    if download.size_gb is None:
        return SIZE_SMALL
    return SIZE_GB.format(size=download.size_gb)


def summary_rows(
    plan: SetupPlan,
    place_of: Callable[[str], str],
    *,
    key_missing: Collection[str] = (),
) -> list[SummaryRow]:
    """Return the summary page's lines for *plan*.

    Args:
        plan: The setup plan the assistant worked out.
        place_of: Names where a provider runs in the few words the panel has room for.
        key_missing: The online providers the setup plan uses that have no key yet.
    """
    correction = next((step for step in plan.steps if step.stage == STAGE_CORRECTION), None)
    rows = [_classes_row(plan)]
    rows += [_step_row(step, plan, correction, place_of, key_missing) for step in plan.steps]
    return rows


def _classes_row(plan: SetupPlan) -> SummaryRow:
    """Return the row that says what the teacher answered about their classes."""
    hand_in = HANDWRITTEN if plan.panel.get("recognition_enabled") else TYPED
    return SummaryRow(
        name=CLASSES_NAME,
        detail=ROW_CLASSES.format(language=str(plan.panel.get("language", "")), hand_in=hand_in),
    )


def _step_row(
    step: PlannedStep,
    plan: SetupPlan,
    correction: PlannedStep | None,
    place_of: Callable[[str], str],
    key_missing: Collection[str],
) -> SummaryRow:
    """Return the row for one step of the setup plan."""
    name = STEP_NAMES.get(step.stage, step.stage)
    downloads = _downloads_for(step, plan)
    if step.stage == STAGE_ANONYMIZATION:
        return SummaryRow(
            name=name,
            detail=ROW_NAMES_ON.format(language=str(plan.panel.get("language", ""))),
            downloads=downloads,
        )
    if step.kept:
        detail = ROW_KEPT.format(engine=step.provider) if step.stage == STAGE_OCR else ROW_KEEPS_OWN
        return SummaryRow(name=name, detail=detail, note=ROW_INSTALLED)
    if not step.provider:
        return SummaryRow(name=name, detail=ROW_OFF)
    if _same_as_correction(step, correction):
        return SummaryRow(name=name, detail=ROW_SAME_MODEL)
    note = ROW_KEY_MISSING if step.provider in key_missing else _note(step, downloads)
    return SummaryRow(
        name=name,
        detail=f"{step.model} · {place_of(step.provider)}",
        downloads=downloads,
        note=note,
    )


def _same_as_correction(step: PlannedStep, correction: PlannedStep | None) -> bool:
    """Return whether *step* runs the same model as the proofreading, so the row can say so."""
    if correction is None or step.stage != STAGE_EVALUATION:
        return False
    return (step.provider, step.model) == (correction.provider, correction.model)


def _downloads_for(step: PlannedStep, plan: SetupPlan) -> tuple[PlannedDownload, ...]:
    """Return the downloads that belong to *step*'s stage."""
    return tuple(download for download in plan.downloads if download.stage == step.stage)


def _note(step: PlannedStep, downloads: Sequence[PlannedDownload]) -> str:
    """Return the note a row ends in when it has nothing to download."""
    if downloads:
        return ""
    return ROW_DOWNLOADED if step.installed else ""


class SummaryRows(QWidget):
    """The summary page's lines, with a checkbox for every download."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setColumnStretch(1, 1)
        self._boxes: dict[str, QCheckBox] = {}
        self._downloads: dict[str, PlannedDownload] = {}
        self._texts: list[str] = []

    def set_rows(self, rows: Sequence[SummaryRow]) -> None:
        """Replace the lines with *rows*, every download ticked."""
        self._clear()
        line = 0
        for row in rows:
            line = self._add_row(line, row)

    def downloads(self) -> list[PlannedDownload]:
        """Return the downloads the teacher left ticked."""
        return [
            download for name, download in self._downloads.items() if self._boxes[name].isChecked()
        ]

    def row_texts(self) -> list[str]:
        """Return the lines as the page shows them, one string per line."""
        return list(self._texts)

    def _add_row(self, line: int, row: SummaryRow) -> int:
        """Draw *row* at *line* and return the next free line."""
        name = QLabel(row.name)
        name.setMinimumWidth(_NAME_WIDTH)
        name.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._grid.addWidget(name, line, 0)
        detail = QLabel(row.detail)
        detail.setWordWrap(True)
        self._grid.addWidget(detail, line, 1)
        self._texts.append(f"{row.name}: {row.detail}")
        if not row.downloads:
            if row.note:
                self._grid.addWidget(_hint(row.note), line, 2)
                self._texts[-1] = f"{self._texts[-1]} — {row.note}"
            return line + 1
        for offset, download in enumerate(row.downloads):
            box = QCheckBox(size_text(download))
            box.setChecked(True)
            box.setToolTip(download.name)
            self._boxes[download.name] = box
            self._downloads[download.name] = download
            self._grid.addWidget(box, line + offset, 2)
            self._texts.append(f"{download.name}: {size_text(download)}")
        return line + len(row.downloads)

    def _clear(self) -> None:
        """Drop every line, so a new plan starts from an empty grid."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._boxes.clear()
        self._downloads.clear()
        self._texts.clear()


def _hint(text: str) -> QLabel:
    """Return *text* as a small grey note at the end of a row."""
    label = QLabel(text)
    label.setStyleSheet(_HINT_STYLE)
    label.setAlignment(Qt.AlignmentFlag.AlignTop)
    return label
